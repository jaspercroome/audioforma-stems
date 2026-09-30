import io
import json
import wave

import numpy as np

SR = 44100


def make_wav(seconds: float = 3.0) -> bytes:
    t = np.arange(int(SR * seconds)) / SR
    left = 0.3 * np.sin(2 * np.pi * 110 * t) + 0.2 * np.sin(2 * np.pi * 660 * t)
    right = 0.3 * np.sin(2 * np.pi * 220 * t) + 0.1 * np.sin(2 * np.pi * 9000 * t)
    pcm = (np.stack([left, right], axis=1) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def read_wav(data: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(data), "rb") as w:
        frames = w.readframes(w.getnframes())
        channels = w.getnchannels()
        assert w.getframerate() == SR and w.getsampwidth() == 2
    return np.frombuffer(frames, dtype="<i2").reshape(-1, channels).T / 32767.0


def read_events(client, events_url):
    events = []
    with client.stream("GET", events_url) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        record = {}
        for line in response.iter_lines():
            if line.startswith("event: "):
                record["event"] = line[7:]
            elif line.startswith("data: "):
                record["data"] = json.loads(line[6:])
            elif line.startswith("id: "):
                record["id"] = int(line[4:])
            elif line == "" and record:
                events.append(record)
                if record.get("event") in ("done", "error"):
                    break
                record = {}
    return events


def start(client, seconds=3.0, model="htdemucs"):
    response = client.post(
        "/api/stream",
        files={"file": ("tone.wav", make_wav(seconds), "audio/wav")},
        data={"model": model},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_streams_chunks_that_add_back_up_to_the_input(client):
    job = start(client)
    events = read_events(client, job["events_url"])
    kinds = [e["event"] for e in events]
    assert kinds[-1] == "done", events[-1]
    assert kinds.index("meta") < kinds.index("chunk")

    meta = next(e["data"] for e in events if e["event"] == "meta")
    assert meta["stems"] == ["drums", "bass", "other", "vocals"]
    assert meta["sample_rate"] == SR and meta["channels"] == 2

    chunks = [e["data"] for e in events if e["event"] == "chunk"]
    assert [c["index"] for c in chunks] == list(range(len(chunks)))
    assert len(chunks) == meta["chunk_count"]
    # Chunks tile the song with no gaps or overlaps.
    position = 0
    for c in chunks:
        assert c["start_frame"] == position
        position += c["frames"]
    assert position == meta["frames"]

    stems = {s: [] for s in meta["stems"]}
    for c in chunks:
        for stem, url in c["urls"].items():
            r = client.get(url)
            assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
            stems[stem].append(read_wav(r.content))
    joined = {s: np.concatenate(parts, axis=-1) for s, parts in stems.items()}
    total = sum(joined.values())
    original = read_wav(make_wav())
    # The stand-in separator partitions the spectrum, so its stems sum to the input.
    assert np.max(np.abs(total - original)) < 5e-4 * len(stems)

    progress = [e["data"] for e in events if e["event"] == "progress"]
    assert progress[-1]["fraction"] == 1.0 and progress[-1]["speed"] > 0

    done = events[-1]["data"]
    full = client.get(done["files"]["bass"])
    assert full.status_code == 200
    np.testing.assert_allclose(read_wav(full.content), joined["bass"], atol=1e-9)

    snapshot = client.get(job["status_url"]).json()
    assert snapshot["status"] == "done" and len(snapshot["chunks"]) == len(chunks)


def test_six_stem_model_and_reconnect_with_last_event_id(client):
    job = start(client, seconds=2.0, model="htdemucs_6s")
    events = read_events(client, job["events_url"])
    meta = next(e["data"] for e in events if e["event"] == "meta")
    assert set(meta["stems"]) == {"drums", "bass", "other", "vocals", "guitar", "piano"}

    # Reconnecting with Last-Event-ID replays only what came after it.
    last = events[2]["id"]
    with client.stream("GET", job["events_url"], headers={"Last-Event-ID": str(last)}) as r:
        ids = [int(line[4:]) for line in r.iter_lines() if line.startswith("id: ")]
    assert ids[0] == last + 1 and ids[-1] == events[-1]["id"]


def test_rejects_unknown_models_and_jobs(client):
    r = client.post("/api/stream", files={"file": ("t.wav", make_wav(1), "audio/wav")}, data={"model": "nope"})
    assert r.status_code == 400
    assert client.get("/api/stream/20250101_000000_abcdef").status_code == 404
    assert client.get("/api/stream/not-a-job/events").status_code == 404


def test_reports_undecodable_audio_as_an_error_event(client):
    r = client.post("/api/stream", files={"file": ("x.mp3", b"not audio at all", "audio/mpeg")}, data={"model": "htdemucs"})
    events = read_events(client, r.json()["events_url"])
    assert events[-1]["event"] == "error"
    assert "decode" in events[-1]["data"]["message"].lower()


def test_cors_allows_local_tools(client):
    r = client.get("/health", headers={"Origin": "http://localhost:8765"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:8765"
