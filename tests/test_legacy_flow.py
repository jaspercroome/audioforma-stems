"""The original /api/audio flow: the server must stay responsive while Demucs
runs, and jobs must end as `completed` with their stem URLs."""
import socket
import threading
import time
from pathlib import Path

import pytest
import requests

from tests.test_stream_api import make_wav

pytest.importorskip("demucs")


@pytest.fixture(scope="module")
def server():
    import uvicorn

    from src.app import app

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    for _ in range(100):
        if instance.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    instance.should_exit = True
    thread.join(5)


def test_status_answers_during_separation_and_ends_completed(server, monkeypatch):
    import demucs.separate

    started, release = threading.Event(), threading.Event()

    def fake_demucs(args):
        # Stand in for a long separation: block until the test lets go.
        started.set()
        release.wait(20)
        input_path = Path(args[0])
        out = Path(args[args.index("-o") + 1]) / "mdx_extra" / input_path.stem
        out.mkdir(parents=True)
        for stem in ("vocals", "drums", "bass", "other"):
            (out / f"{stem}.mp3").write_bytes(b"ID3 not really an mp3")

    monkeypatch.setattr(demucs.separate, "main", fake_demucs)
    r = requests.post(
        f"{server}/api/audio/separate",
        files={"file": ("song.wav", make_wav(30), "audio/wav")},
        data={"artist": "Test", "track": "Tone"},
        timeout=10,
    )
    job_id = r.json()["job_id"]
    assert started.wait(15), "separation never started"

    # Demucs is "running": before the fix this request hung until it finished.
    t0 = time.monotonic()
    status = requests.get(f"{server}/api/audio/status/{job_id}", timeout=5).json()
    assert time.monotonic() - t0 < 2
    assert status["status"] == "processing"

    release.set()
    for _ in range(100):
        status = requests.get(f"{server}/api/audio/status/{job_id}", timeout=5).json()
        if status["status"] in ("completed", "error"):
            break
        time.sleep(0.1)
    assert status["status"] == "completed", status
    assert status["progress"] == 1.0
    vocals = status["files"]["vocals"]
    assert vocals == f"/files/{job_id}/mdx_extra/input/vocals.mp3"
    assert requests.get(server + vocals, timeout=5).status_code == 200
