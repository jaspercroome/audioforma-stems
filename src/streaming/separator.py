"""Separators that deliver stems progressively, a segment at a time."""
import subprocess
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Tuple

import numpy as np

from .overlap import stream_overlap_add

# Models a client may ask for. htdemucs_6s adds piano and guitar stems.
KNOWN_MODELS = ("htdemucs_6s", "htdemucs", "htdemucs_ft", "mdx_extra", "mdx_extra_q")
DEFAULT_MODEL = "htdemucs_6s"

# Longest segment to stream with for models that accept any length (the older
# HDemucs bags such as mdx_extra train on long segments; this keeps latency sane).
MAX_STREAM_SEGMENT_SECONDS = 10.0

Block = Tuple[int, np.ndarray]  # (start sample, [sources, channels, n] float32)


def decode_audio(path: Path, samplerate: int, channels: int) -> np.ndarray:
    """Decode any format ffmpeg reads to float32 ``[channels, samples]``."""
    cmd = [
        "ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
        "-f", "f32le", "-acodec", "pcm_f32le",
        "-ac", str(channels), "-ar", str(samplerate), "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        message = result.stderr.decode(errors="replace").strip().splitlines()
        raise ValueError(f"Could not decode audio: {message[-1] if message else 'ffmpeg failed'}")
    data = np.frombuffer(result.stdout, dtype=np.float32)
    if data.size == 0:
        raise ValueError("Could not decode audio: no samples")
    return data.reshape(-1, channels).T.copy()


def _model_segment_seconds(model) -> float:
    """Segment length the model separates in, as demucs would use it."""
    allowed = getattr(model, "max_allowed_segment", None)  # BagOfModels with HTDemucs inside
    if allowed is not None and np.isfinite(float(allowed)):
        return float(allowed)
    segment = getattr(model, "segment", None)
    if segment is not None:
        return float(segment)
    return min(float(sub.segment) for sub in model.models)


class DemucsSeparator:
    """Demucs, streamed segment by segment.

    Output matches ``demucs --shifts 0`` for single-model checkpoints
    (htdemucs, htdemucs_6s): same segments, same blend, same neighbouring
    context for padding. Bags of models with different segment lengths
    (mdx_extra) are blended per segment, which is close but not identical.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL, device: Optional[str] = None,
                 overlap: float = 0.25, model=None):
        import torch

        self._torch = torch
        if model is None:
            from demucs.pretrained import get_model

            model = get_model(model_name)
        self.name = model_name
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        # Move once: apply_model otherwise shuttles each sub-model to the device
        # and back for every call, which is slow for segment-by-segment use.
        self.model = model.to(self.device).eval()
        self.samplerate = int(model.samplerate)
        self.channels = int(model.audio_channels)
        self.sources: List[str] = list(model.sources)
        segment = _model_segment_seconds(model)
        if segment > MAX_STREAM_SEGMENT_SECONDS:
            segment = MAX_STREAM_SEGMENT_SECONDS
        self.segment_length = int(self.samplerate * segment)
        self.overlap = overlap

    def stream(self, wav: np.ndarray) -> Iterator[Block]:
        """Separate ``wav`` (float32 ``[channels, samples]`` at ``samplerate``)."""
        torch = self._torch
        from demucs.apply import TensorChunk, apply_model

        mix = torch.from_numpy(np.ascontiguousarray(wav, dtype=np.float32))
        # Same normalisation as demucs.separate.
        ref = mix.mean(0)
        mean = ref.mean()
        std = ref.std()
        if not torch.isfinite(std) or float(std) < 1e-8:
            std = torch.tensor(1.0)
        mix = ((mix - mean) / std)[None]

        def separate(offset: int, n: int) -> np.ndarray:
            # A TensorChunk (not a slice) so padding borrows the real
            # neighbouring audio, exactly as apply_model's own split does.
            chunk = TensorChunk(mix, offset, n)
            with torch.no_grad():
                out = apply_model(self.model, chunk, shifts=0, split=False, device=self.device)
            return (out[0] * std + mean).cpu().numpy()

        for start, block in stream_overlap_add(mix.shape[-1], self.segment_length, self.overlap, separate):
            yield start, block.astype(np.float32)


# Band edges (Hz) for the stand-in separator, lowest band first.
_BANDS = {
    "bass": (0, 180),
    "vocals": (180, 1200),
    "piano": (1200, 2000),
    "guitar": (2000, 3000),
    "other": (3000, 7000),
    "drums": (7000, None),
}


class FilterBankSeparator:
    """A stand-in for tests and local development without torch.

    Splits the spectrum into bands named like demucs stems. It is not a real
    separation, but the stems sum back to the input exactly, it streams
    through the same overlap-add path, and it is fast.
    """

    def __init__(self, sources: Sequence[str] = ("drums", "bass", "other", "vocals"),
                 samplerate: int = 44100, channels: int = 2,
                 segment_seconds: float = 4.0, overlap: float = 0.25):
        unknown = [s for s in sources if s not in _BANDS]
        if unknown:
            raise ValueError(f"No band for {unknown}")
        self.name = "filterbank"
        self.samplerate = samplerate
        self.channels = channels
        self.sources = list(sources)
        self.segment_length = int(samplerate * segment_seconds)
        self.overlap = overlap
        # Contiguous bands in frequency order, so the masks partition the spectrum.
        ordered = sorted(self.sources, key=lambda s: _BANDS[s][0])
        self._edges = []
        for i, source in enumerate(ordered):
            low = 0 if i == 0 else _BANDS[source][0]
            high = None if i == len(ordered) - 1 else _BANDS[ordered[i + 1]][0]
            self._edges.append((source, low, high))

    def stream(self, wav: np.ndarray) -> Iterator[Block]:
        sr = self.samplerate

        def separate(offset: int, n: int) -> np.ndarray:
            segment = wav[:, offset:offset + n].astype(np.float64)
            spectrum = np.fft.rfft(segment, axis=-1)
            freqs = np.fft.rfftfreq(n, 1 / sr)
            out = np.zeros((len(self.sources), segment.shape[0], n))
            for source, low, high in self._edges:
                mask = freqs >= low
                if high is not None:
                    mask &= freqs < high
                out[self.sources.index(source)] = np.fft.irfft(spectrum * mask, n=n, axis=-1)
            return out

        for start, block in stream_overlap_add(wav.shape[-1], self.segment_length, self.overlap, separate):
            yield start, block.astype(np.float32)
