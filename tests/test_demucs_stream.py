"""Streaming Demucs must equal separating the whole file at once.

Uses a small, randomly initialised HTDemucs: the weights don't matter for
this check, only that the same segments, blend and padding context are used.
"""
import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("demucs")

from demucs.apply import BagOfModels, apply_model  # noqa: E402
from demucs.htdemucs import HTDemucs  # noqa: E402

from src.streaming.separator import DemucsSeparator  # noqa: E402


def tiny_bag(sources):
    torch.manual_seed(0)
    model = HTDemucs(sources=sources, channels=8, depth=2, t_layers=1, t_heads=2, segment=1.0)
    return BagOfModels([model])


def offline(bag, wav: np.ndarray) -> np.ndarray:
    """What `demucs --shifts 0` computes: normalise, split, blend, denormalise."""
    mix = torch.from_numpy(wav)
    ref = mix.mean(0)
    mean, std = ref.mean(), ref.std()
    with torch.no_grad():
        out = apply_model(bag, ((mix - mean) / std)[None], shifts=0, split=True, overlap=0.25)
    return (out[0] * std + mean).numpy()


@pytest.mark.parametrize("seconds", [0.6, 2.3, 3.0])
def test_streaming_matches_offline_separation(seconds):
    rng = np.random.default_rng(1)
    wav = (0.2 * rng.standard_normal((2, int(44100 * seconds)))).astype(np.float32)
    bag = tiny_bag(["drums", "bass", "other", "vocals"])
    separator = DemucsSeparator("htdemucs", model=bag, device="cpu")
    assert separator.segment_length == 44100  # the model's 1 s segment

    blocks = list(separator.stream(wav))
    streamed = np.concatenate([block for _, block in blocks], axis=-1)
    expected = offline(bag, wav)

    assert streamed.shape == expected.shape
    np.testing.assert_allclose(streamed, expected, atol=2e-5)
    if seconds > 1:
        assert len(blocks) > 1  # really streamed, not one block at the end


def test_silence_does_not_produce_nans():
    bag = tiny_bag(["drums", "bass", "other", "vocals"])
    separator = DemucsSeparator("htdemucs", model=bag, device="cpu")
    blocks = list(separator.stream(np.zeros((2, 30000), dtype=np.float32)))
    assert all(np.isfinite(block).all() for _, block in blocks)
