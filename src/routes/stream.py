"""Streaming separation API.

    POST /api/stream                 multipart: file, [model, artist, track]
    POST /api/stream/from-url        JSON: {url, [model, artist, track]}
    GET  /api/stream/{job}           snapshot (for polling clients)
    GET  /api/stream/{job}/events    Server-Sent Events: status, meta, chunk, progress, done, error
    GET  /api/stream/{job}/chunks/{index}/{stem}.wav
    GET  /api/stream/{job}/stems/{stem}.wav   (after done)
"""
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, Optional

import aiohttp
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from ..config.supabase import get_public_url, supabase
from ..streaming.jobs import JOB_ID_PATTERN, StreamJob, StreamManager, encode_mp3
from ..streaming.separator import DEFAULT_MODEL

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/stream")

UPLOAD_DIR = Path(os.getenv("STREAM_UPLOAD_DIR", "temp_uploads"))
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
STEM_NAME = re.compile(r"^[a-z]+$")


def persist_to_supabase(job: StreamJob, stems: Dict[str, Path]) -> Optional[Dict[str, str]]:
    """Upload finished stems like the original flow does, when Supabase is set up."""
    if supabase is None or not job.artist or not job.track:
        return None
    files = {}
    for stem, path in stems.items():
        mp3 = path.with_suffix(".mp3")
        encode_mp3(path, mp3)
        with open(mp3, "rb") as f:
            supabase.storage.from_("stems").upload(f"{job.job_id}/{stem}.mp3", f.read(), {"content-type": "audio/mpeg"})
        files[stem] = get_public_url("stems", f"{job.job_id}/{stem}.mp3")
    supabase.table("stem_lookup").insert({"artist": job.artist, "track": job.track, "directory": job.job_id}).execute()
    return files


manager = StreamManager(Path(os.getenv("STREAM_DIR", "temp/stream")), persist=persist_to_supabase)


class StreamFromUrl(BaseModel):
    url: str
    model: str = DEFAULT_MODEL
    artist: Optional[str] = None
    track: Optional[str] = None


def _started(job: StreamJob) -> dict:
    base = f"/api/stream/{job.job_id}"
    return {"job_id": job.job_id, "status": job.state, "events_url": f"{base}/events", "status_url": base}


def _start(path: Path, model: str, artist: Optional[str], track: Optional[str]) -> dict:
    try:
        job = manager.start(path, model=model, artist=artist, track=track)
    except ValueError as error:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(error))
    return _started(job)


def _upload_path(suffix: str = "") -> Path:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=UPLOAD_DIR, suffix=suffix)
    os.close(handle)
    return Path(name)


@router.post("")
async def stream_upload(
    file: UploadFile = File(...),
    model: str = Form(DEFAULT_MODEL),
    artist: Optional[str] = Form(None),
    track: Optional[str] = Form(None),
):
    path = _upload_path(Path(file.filename or "").suffix[:8])
    size = 0
    with path.open("wb") as out:
        while True:
            block = await file.read(1 << 20)
            if not block:
                break
            size += len(block)
            if size > MAX_DOWNLOAD_BYTES:
                out.close()
                path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="File is too large")
            out.write(block)
    return _start(path, model, artist, track)


@router.post("/from-url")
async def stream_from_url(request: StreamFromUrl):
    path = _upload_path()
    size = 0
    try:
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(request.url) as response:
                if response.status != 200:
                    raise HTTPException(status_code=400, detail=f"Could not download file (HTTP {response.status})")
                with path.open("wb") as out:
                    async for block in response.content.iter_chunked(1 << 20):
                        size += len(block)
                        if size > MAX_DOWNLOAD_BYTES:
                            raise HTTPException(status_code=413, detail="File is too large")
                        out.write(block)
    except HTTPException:
        path.unlink(missing_ok=True)
        raise
    except Exception as error:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Error downloading file: {error}")
    return _start(path, request.model, request.artist, request.track)


def _job(job_id: str) -> StreamJob:
    job = manager.get(job_id) if JOB_ID_PATTERN.match(job_id) else None
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/{job_id}")
async def stream_status(job_id: str):
    return _job(job_id).snapshot()


@router.get("/{job_id}/events")
async def stream_events(job_id: str, request: Request):
    job = _job(job_id)
    try:
        after = int(request.headers.get("last-event-id", "-1"))
    except ValueError:
        after = -1

    async def events():
        # Tell EventSource to wait 2 s before reconnecting after a drop.
        yield "retry: 2000\n\n"
        async for record in job.follow(after):
            if await request.is_disconnected():
                break
            if record is None:
                yield ": keep-alive\n\n"
                continue
            payload = json.dumps(record["data"], separators=(",", ":"))
            yield f"id: {record['id']}\nevent: {record['event']}\ndata: {payload}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


def _wav(path: Path, cache: str) -> FileResponse:
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not ready")
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": cache})


@router.get("/{job_id}/chunks/{index}/{stem}.wav")
async def stream_chunk(job_id: str, index: int, stem: str):
    job = _job(job_id)
    if index < 0 or not STEM_NAME.match(stem):
        raise HTTPException(status_code=404, detail="Chunk not found")
    # Chunks never change once written.
    return _wav(job.dir / "chunks" / f"{index:05d}_{stem}.wav", "public, max-age=86400, immutable")


@router.get("/{job_id}/stems/{stem}.wav")
async def stream_stem(job_id: str, stem: str):
    job = _job(job_id)
    if not STEM_NAME.match(stem) or job.state != "done":
        raise HTTPException(status_code=404, detail="Stem not ready")
    return _wav(job.dir / "stems" / f"{stem}.wav", "public, max-age=3600")
