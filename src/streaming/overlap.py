"""Streaming overlap-add for chunked source separation.

Demucs separates long audio in overlapping segments and blends them with a
triangular weight (``demucs.apply.apply_model`` with ``split=True``). Once
segment ``i`` has been separated, every sample before segment ``i + 1``
starts has received all of its contributions, so it is final.

``stream_overlap_add`` yields those final stretches as soon as they exist.
The streamed result is therefore sample-for-sample what separating the whole
file at once would give (with ``shifts=0``), just delivered progressively,
and it only keeps about one segment of partial sums in memory.

This module is plain numpy so it can be tested without torch.
"""
from typing import Callable, Iterator, Tuple

import numpy as np


def triangular_weight(segment_length: int, transition_power: float = 1.0) -> np.ndarray:
    """The blend weight demucs uses: a triangle peaking mid-segment, never zero."""
    half = segment_length // 2
    weight = np.concatenate(
        [np.arange(1, half + 1), np.arange(segment_length - half, 0, -1)]
    ).astype(np.float64)
    return (weight / weight.max()) ** transition_power


def segment_stride(segment_length: int, overlap: float) -> int:
    return int((1 - overlap) * segment_length)


def stream_overlap_add(
    length: int,
    segment_length: int,
    overlap: float,
    separate: Callable[[int, int], np.ndarray],
    transition_power: float = 1.0,
) -> Iterator[Tuple[int, np.ndarray]]:
    """Separate ``length`` samples segment by segment, yielding final audio early.

    Args:
        length: total number of samples in the input.
        segment_length: samples per segment (the model's segment).
        overlap: fraction of each segment shared with the next (demucs uses 0.25).
        separate: ``separate(offset, n)`` returns the separated segment covering
            samples ``offset .. offset + n`` with time on the last axis, for
            example ``[sources, channels, n]``.
        transition_power: sharpness of the blend (demucs default 1).

    Yields:
        ``(start, block)``: samples ``start .. start + block.shape[-1]`` are final.
        Blocks are contiguous and together cover ``0 .. length``.
    """
    if length <= 0:
        return
    stride = segment_stride(segment_length, overlap)
    if stride <= 0:
        raise ValueError("overlap leaves no stride between segments")
    weight = triangular_weight(segment_length, transition_power)

    pending = None  # weighted partial sums for samples [pending_start, pending_start + width)
    pending_weight = None
    pending_start = 0

    for offset in range(0, length, stride):
        n = min(segment_length, length - offset)
        out = np.asarray(separate(offset, n), dtype=np.float64)
        if out.shape[-1] != n:
            raise ValueError(f"separate() returned {out.shape[-1]} samples, expected {n}")
        w = weight[:n]

        # Merge this segment into the pending partial sums.
        end = offset + n
        if pending is None:
            pending = out * w
            pending_weight = w.copy()
            pending_start = offset
        else:
            width = max(pending_start + pending.shape[-1], end) - pending_start
            merged = np.zeros(out.shape[:-1] + (width,))
            merged_weight = np.zeros(width)
            merged[..., : pending.shape[-1]] = pending
            merged_weight[: pending_weight.shape[0]] = pending_weight
            at = offset - pending_start
            merged[..., at : at + n] += out * w
            merged_weight[at : at + n] += w
            pending, pending_weight = merged, merged_weight

        # Everything before the next segment's start is now final.
        final_until = min(offset + stride, length)
        if offset + stride >= length:
            final_until = length
        count = final_until - pending_start
        if count > 0:
            yield pending_start, pending[..., :count] / pending_weight[:count]
            pending = pending[..., count:]
            pending_weight = pending_weight[count:]
            pending_start = final_until


def overlap_add_reference(
    length: int,
    segment_length: int,
    overlap: float,
    separate: Callable[[int, int], np.ndarray],
    transition_power: float = 1.0,
) -> np.ndarray:
    """Whole-signal overlap-add, written the way demucs does it. For tests."""
    stride = segment_stride(segment_length, overlap)
    weight = triangular_weight(segment_length, transition_power)
    out = None
    total = np.zeros(length)
    for offset in range(0, length, stride):
        n = min(segment_length, length - offset)
        chunk = np.asarray(separate(offset, n), dtype=np.float64)
        if out is None:
            out = np.zeros(chunk.shape[:-1] + (length,))
        out[..., offset : offset + n] += weight[:n] * chunk
        total[offset : offset + n] += weight[:n]
    return out / total
