"""Stream stems from the service and save them, printing events as they arrive.

    python stream_client.py song.mp3 [--model htdemucs_6s] [--server http://localhost:8000]

Writes <song>_stems/<stem>.wav once finished. Needs only `requests`.
"""
import argparse
import io
import json
import time
import wave
from pathlib import Path

import requests


def events(url):
    """Minimal Server-Sent Events reader."""
    with requests.get(url, stream=True, headers={"Accept": "text/event-stream"}, timeout=(10, 120)) as response:
        response.raise_for_status()
        record = {}
        for line in response.iter_lines(decode_unicode=True):
            if line is None:
                continue
            if line.startswith("event: "):
                record["event"] = line[7:]
            elif line.startswith("data: "):
                record["data"] = json.loads(line[6:])
            elif line == "" and "event" in record:
                yield record
                if record["event"] in ("done", "error"):
                    return
                record = {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--model", default="htdemucs_6s")
    parser.add_argument("--server", default="http://localhost:8000")
    args = parser.parse_args()

    began = time.monotonic()
    with open(args.file, "rb") as f:
        job = requests.post(f"{args.server}/api/stream", files={"file": f}, data={"model": args.model}).json()
    print(f"job {job['job_id']}")

    parts = {}
    meta = None
    for record in events(args.server + job["events_url"]):
        kind, data = record["event"], record["data"]
        elapsed = time.monotonic() - began
        if kind == "meta":
            meta = data
            print(f"[{elapsed:5.1f}s] {data['duration']:.1f}s of audio, stems: {', '.join(data['stems'])}")
        elif kind == "chunk":
            for stem, url in data["urls"].items():
                parts.setdefault(stem, []).append(requests.get(args.server + url).content)
            print(f"[{elapsed:5.1f}s] chunk {data['index']}: {data['start']:.2f}s +{data['duration']:.2f}s ready")
        elif kind == "progress":
            print(f"[{elapsed:5.1f}s] {data['fraction'] * 100:3.0f}%  ({data['speed']:.2f}x real time)")
        elif kind == "error":
            raise SystemExit(f"error: {data['message']}")
        elif kind == "done":
            print(f"[{elapsed:5.1f}s] done")

    out_dir = Path(args.file).with_suffix("")
    out_dir = out_dir.parent / f"{out_dir.name}_stems"
    out_dir.mkdir(exist_ok=True)
    for stem, chunks in parts.items():
        with wave.open(str(out_dir / f"{stem}.wav"), "wb") as out:
            out.setnchannels(meta["channels"])
            out.setsampwidth(2)
            out.setframerate(meta["sample_rate"])
            for chunk in chunks:
                with wave.open(io.BytesIO(chunk), "rb") as part:
                    out.writeframes(part.readframes(part.getnframes()))
    print(f"saved {len(parts)} stems to {out_dir}")


if __name__ == "__main__":
    main()
