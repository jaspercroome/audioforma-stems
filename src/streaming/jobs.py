"""Streaming separation jobs: a worker thread separates, clients follow events.

Every job keeps an append-only list of events (meta, chunk, progress, done,
error). Clients follow it over Server-Sent Events and can reconnect with
Last-Event-ID without missing anything; each ``chunk`` event points at small
WAV files, one per stem, that are final the moment the event is sent.
"""
import asyncio
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

import numpy as np

from .separator import DEFAULT_MODEL, KNOWN_MODELS, DemucsSeparator, FilterBankSeparator, decode_audio

logger = logging.getLogger(__name__)

TERMINAL_EVENTS = ("done", "error")
JOB_ID_PATTERN = re.compile(r"^[0-9]{8}_[0-9]{6}_[0-9a-f]{6}$")
MIN_SECONDS = 0.5
MAX_SECONDS = 15 * 60
HEARTBEAT_SECONDS = 15.0


def new_job_id() -> str:
    """Sortable like the original IDs, plus a random suffix so two jobs never collide."""
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def write_wav(path: Path, data: np.ndarray, samplerate: int) -> None:
    """16-bit PCM WAV from float ``[channels, samples]``."""
    pcm = np.clip(data, -1.0, 1.0)
    pcm = np.round(pcm * 32767.0).astype("<i2").T
    with wave.open(str(path), "wb") as out:
        out.setnchannels(data.shape[0])
        out.setsampwidth(2)
        out.setframerate(samplerate)
        out.writeframes(np.ascontiguousarray(pcm).tobytes())


class StreamJob:
    """State and event log for one separation. Mutated only on the event loop."""

    def __init__(self, job_id: str, directory: Path, loop: asyncio.AbstractEventLoop,
                 model: str, artist: Optional[str] = None, track: Optional[str] = None):
        self.job_id = job_id
        self.dir = directory
        self.loop = loop
        self.model = model
        self.artist = artist
        self.track = track
        self.created = time.time()
        self.events: List[Dict[str, Any]] = []
        self.state = "queued"
        self.meta: Optional[Dict[str, Any]] = None
        self.chunks: List[Dict[str, Any]] = []
        self.progress: Dict[str, Any] = {"fraction": 0.0}
        self.files: Optional[Dict[str, str]] = None
        self.error: Optional[str] = None
        self._changed = asyncio.Event()

    # Safe to call from any thread.
    def publish(self, event: str, data: Dict[str, Any]) -> None:
        self.loop.call_soon_threadsafe(self._append, event, data)

    def _append(self, event: str, data: Dict[str, Any]) -> None:
        self.events.append({"id": len(self.events), "event": event, "data": data})
        if event == "status":
            self.state = data.get("state", self.state)
        elif event == "meta":
            self.meta = data
            self.state = "separating"
        elif event == "chunk":
            self.chunks.append(data)
        elif event == "progress":
            self.progress = data
        elif event == "done":
            self.state = "done"
            self.files = data.get("files")
        elif event == "error":
            self.state = "error"
            self.error = data.get("message")
        # Wake everyone waiting on the old event; new waiters get a fresh one.
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()

    @property
    def finished(self) -> bool:
        return self.state in ("done", "error")

    def snapshot(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.state,
            "model": self.model,
            "meta": self.meta,
            "progress": self.progress,
            "chunks": self.chunks,
            "files": self.files,
            "error": self.error,
        }

    async def follow(self, after: int = -1) -> AsyncIterator[Optional[Dict[str, Any]]]:
        """Yield events after id ``after``; ``None`` is a heartbeat. Ends after done/error."""
        index = after + 1
        while True:
            while index < len(self.events):
                record = self.events[index]
                index += 1
                yield record
                if record["event"] in TERMINAL_EVENTS:
                    return
            changed = self._changed
            try:
                await asyncio.wait_for(changed.wait(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield None


SeparatorFactory = Callable[[str], Any]


def default_separator_factory(model: str):
    backend = os.getenv("STEMS_BACKEND", "demucs")
    if backend == "filterbank":
        six = model.endswith("_6s")
        sources = ("drums", "bass", "other", "vocals", "guitar", "piano") if six else ("drums", "bass", "other", "vocals")
        return FilterBankSeparator(sources=sources)
    return DemucsSeparator(model)


class StreamManager:
    """Runs separations on a small thread pool and keeps their jobs."""

    def __init__(self, root: Path, separator_factory: SeparatorFactory = default_separator_factory,
                 max_workers: int = 1, job_ttl_seconds: float = 2 * 3600,
                 persist: Optional[Callable[[StreamJob, Dict[str, Path]], Optional[Dict[str, str]]]] = None):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs: Dict[str, StreamJob] = {}
        self.separator_factory = separator_factory
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="separate")
        self.job_ttl_seconds = job_ttl_seconds
        self.persist = persist
        self._separators: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> Optional[StreamJob]:
        return self.jobs.get(job_id)

    def start(self, input_path: Path, model: str = DEFAULT_MODEL, artist: Optional[str] = None,
              track: Optional[str] = None) -> StreamJob:
        if model not in KNOWN_MODELS:
            raise ValueError(f"Unknown model {model!r}; choose one of {', '.join(KNOWN_MODELS)}")
        self.prune()
        job_id = new_job_id()
        directory = self.root / job_id
        (directory / "chunks").mkdir(parents=True, exist_ok=True)
        job = StreamJob(job_id, directory, asyncio.get_running_loop(), model, artist, track)
        self.jobs[job_id] = job
        job.publish("status", {"state": "queued"})
        self.executor.submit(self._run, job, input_path)
        return job

    def _separator(self, model: str):
        # Loading a model takes seconds and hundreds of MB, so keep one per name.
        with self._lock:
            if model not in self._separators:
                self._separators[model] = self.separator_factory(model)
            return self._separators[model]

    def _run(self, job: StreamJob, input_path: Path) -> None:
        try:
            job.publish("status", {"state": "loading_model"})
            separator = self._separator(job.model)
            job.publish("status", {"state": "decoding"})
            wav = decode_audio(input_path, separator.samplerate, separator.channels)
            length = wav.shape[-1]
            seconds = length / separator.samplerate
            if seconds < MIN_SECONDS or seconds > MAX_SECONDS:
                raise ValueError(f"Audio must be between {MIN_SECONDS:g} s and {MAX_SECONDS // 60} minutes long")

            sr = separator.samplerate
            chunk_seconds = (separator.segment_length * (1 - separator.overlap)) / sr
            job.publish("meta", {
                "job_id": job.job_id,
                "model": job.model,
                "sample_rate": sr,
                "channels": separator.channels,
                "stems": separator.sources,
                "frames": length,
                "duration": seconds,
                "chunk_seconds": chunk_seconds,
                "chunk_count": int(np.ceil(length / max(1, int(separator.segment_length * (1 - separator.overlap))))),
                "chunk_format": "wav/pcm_s16le",
            })

            began = time.monotonic()
            done_frames = 0
            for index, (start, block) in enumerate(separator.stream(wav)):
                frames = block.shape[-1]
                urls = {}
                for s, stem in enumerate(separator.sources):
                    write_wav(job.dir / "chunks" / f"{index:05d}_{stem}.wav", block[s], sr)
                    urls[stem] = f"/api/stream/{job.job_id}/chunks/{index}/{stem}.wav"
                done_frames = start + frames
                job.publish("chunk", {
                    "index": index,
                    "start": start / sr,
                    "start_frame": int(start),
                    "frames": int(frames),
                    "duration": frames / sr,
                    "urls": urls,
                })
                elapsed = max(1e-6, time.monotonic() - began)
                job.publish("progress", {
                    "fraction": done_frames / length,
                    "seconds_done": done_frames / sr,
                    "elapsed": elapsed,
                    # Audio seconds separated per wall-clock second: >1 is faster than real time.
                    "speed": (done_frames / sr) / elapsed,
                })

            stems = self._assemble(job, separator)
            files = {stem: f"/api/stream/{job.job_id}/stems/{stem}.wav" for stem in stems}
            if self.persist is not None:
                try:
                    persisted = self.persist(job, stems)
                    if persisted:
                        files = persisted
                except Exception as error:  # keep the local files if upload fails
                    logger.warning("Persisting stems for %s failed: %s", job.job_id, error)
            job.publish("done", {"files": files, "elapsed": time.monotonic() - began})
        except Exception as error:
            logger.exception("Stream job %s failed", job.job_id)
            job.publish("error", {"message": str(error)})
        finally:
            try:
                input_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _assemble(self, job: StreamJob, separator) -> Dict[str, Path]:
        """Join each stem's chunks into one WAV for download or upload."""
        out_dir = job.dir / "stems"
        out_dir.mkdir(exist_ok=True)
        chunk_files = sorted((job.dir / "chunks").glob("*.wav"))
        stems = {}
        for stem in separator.sources:
            path = out_dir / f"{stem}.wav"
            with wave.open(str(path), "wb") as out:
                out.setnchannels(separator.channels)
                out.setsampwidth(2)
                out.setframerate(separator.samplerate)
                for chunk in chunk_files:
                    if chunk.stem.split("_", 1)[1] == stem:
                        with wave.open(str(chunk), "rb") as part:
                            out.writeframes(part.readframes(part.getnframes()))
            stems[stem] = path
        return stems

    def prune(self) -> None:
        """Forget jobs (and delete their files) older than the time-to-live."""
        now = time.time()
        for job_id, job in list(self.jobs.items()):
            if job.finished and now - job.created > self.job_ttl_seconds:
                shutil.rmtree(job.dir, ignore_errors=True)
                del self.jobs[job_id]


def encode_mp3(source: Path, target: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(source), "-codec:a", "libmp3lame", "-q:a", "2", str(target)],
        check=True, capture_output=True,
    )
