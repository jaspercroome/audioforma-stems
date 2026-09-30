import numpy as np
import pytest

from src.streaming.overlap import overlap_add_reference, stream_overlap_add, triangular_weight


def fake_separate(signal: np.ndarray):
    """A deterministic stand-in whose output depends on where the segment starts,
    like a real model's does (different context, different result)."""
    def separate(offset: int, n: int) -> np.ndarray:
        segment = signal[..., offset:offset + n]
        wobble = np.sin(np.arange(n) * 0.01 + offset * 1e-3)
        return np.stack([segment * 0.5 + wobble, np.tanh(segment * (1 + offset % 7))])
    return separate


@pytest.mark.parametrize("length", [1, 999, 1000, 1500, 7501, 10_000, 12_345])
def test_streamed_blocks_match_whole_file_overlap_add(length):
    rng = np.random.default_rng(length)
    signal = rng.standard_normal((2, length))
    separate = fake_separate(signal)
    reference = overlap_add_reference(length, 1000, 0.25, separate)

    blocks = list(stream_overlap_add(length, 1000, 0.25, separate))
    starts = [start for start, _ in blocks]
    joined = np.concatenate([block for _, block in blocks], axis=-1)

    assert starts[0] == 0
    assert all(b > a for a, b in zip(starts, starts[1:]))  # contiguous and ordered
    assert joined.shape == reference.shape
    np.testing.assert_allclose(joined, reference, rtol=0, atol=1e-12)


def test_first_block_arrives_after_one_segment():
    length = 100_000
    calls = []

    def separate(offset, n):
        calls.append(offset)
        return np.zeros((1, 1, n))

    stream = stream_overlap_add(length, 1000, 0.25, separate)
    start, block = next(stream)
    assert calls == [0]  # only one segment separated so far
    assert start == 0 and block.shape[-1] == 750  # up to where the next segment begins


def test_weight_matches_demucs_shape():
    weight = triangular_weight(10)
    assert weight.max() == 1.0 and weight.min() > 0
    np.testing.assert_allclose(weight, [0.2, 0.4, 0.6, 0.8, 1.0, 1.0, 0.8, 0.6, 0.4, 0.2])
