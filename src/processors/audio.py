# src/processors/audio.py
import asyncio
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict

from fastapi import HTTPException, UploadFile

from ..config.supabase import get_public_url, supabase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MODEL = "mdx_extra"
STEM_NAMES = ["vocals", "drums", "bass", "other"]

# Job progress when Supabase isn't configured (local development).
LOCAL_PROGRESS: Dict[str, dict] = {}


def stem_files_for(job_id: str) -> Dict[str, str]:
    """Where a completed job's stems live: Supabase storage, or served locally."""
    if supabase is not None:
        return {stem: get_public_url("stems", f"{job_id}/{stem}.mp3") for stem in STEM_NAMES}
    return {stem: f"/files/{job_id}/{MODEL}/input/{stem}.mp3" for stem in STEM_NAMES}


class AudioProcessor:
    def __init__(self):
        self.temp_dir = Path("temp")
        self.temp_dir.mkdir(exist_ok=True)
        self.bucket_name = "stems"

    async def process_file(self, file: UploadFile, job_id: str, artist: str, track: str) -> dict:
        try:
            # Generate unique directory for this processing job
            job_dir = self.temp_dir / job_id
            job_dir.mkdir(exist_ok=True)

            # Save uploaded file
            await self.update_progress(job_id, 0, "uploading")
            input_path = job_dir / "input.mp3"
            logger.info(f"Saving file to {input_path}")
            with input_path.open("wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            # Validate file
            await self.update_progress(job_id, 0.1, "validating")
            if not await asyncio.to_thread(self._validate_audio, input_path):
                logger.error(f"File validation failed for {input_path}")
                raise HTTPException(status_code=400, detail="Invalid audio file")

            # Demucs takes minutes and holds the CPU/GPU. Running it on the event
            # loop (as before) froze the whole server, so status requests went
            # unanswered until it finished. Run it on a worker thread instead.
            await self.update_progress(job_id, 0.2, "processing")
            import demucs.separate

            await asyncio.to_thread(
                demucs.separate.main,
                [str(input_path), "-n", MODEL, "--mp3", "-o", str(job_dir)],
            )

            output_dir = job_dir / MODEL / input_path.stem
            if not output_dir.exists():
                raise HTTPException(status_code=500, detail="Processing failed - no output directory")
            for stem_name in STEM_NAMES:
                if not (output_dir / f"{stem_name}.mp3").exists():
                    raise HTTPException(status_code=500, detail=f"Processing failed - missing {stem_name} file")

            if supabase is not None:
                # Upload each stem file, then record the song.
                await self.update_progress(job_id, 0.9, "uploading")
                for stem_name in STEM_NAMES:
                    with open(output_dir / f"{stem_name}.mp3", "rb") as f:
                        supabase.storage.from_(self.bucket_name).upload(
                            f"{job_id}/{stem_name}.mp3",
                            f.read(),
                            {"content-type": "audio/mpeg"}
                        )
                supabase.table("stem_lookup").insert({
                    "artist": artist,
                    "track": track,
                    "directory": job_id
                }).execute()
                await self.cleanup_job(job_id)
            # Without Supabase the files stay in temp/ and are served from /files.

            stem_files = stem_files_for(job_id)
            # Mark the job finished: before, it stayed at "uploading" forever, so
            # clients polling for "completed" never stopped.
            await self.update_progress(job_id, 1.0, "completed")
            return {
                "job_id": job_id,
                "status": "completed",
                "files": stem_files
            }

        except HTTPException as e:
            logger.error(f"Processing error: {e.detail}")
            await self.update_progress(job_id, 1.0, "error", str(e.detail))
            raise
        except Exception as e:
            logger.error(f"Processing error: {str(e)}")
            await self.update_progress(job_id, 1.0, "error", str(e))
            raise HTTPException(status_code=500, detail=str(e))

    def _validate_audio(self, file_path: Path) -> bool:
        try:
            from pydub import AudioSegment
            # from_file lets ffmpeg detect the format (the upload is always saved
            # as input.mp3, but it may really be WAV, M4A, FLAC...).
            audio = AudioSegment.from_file(str(file_path))

            # Check duration constraints (30 seconds to 15 minutes)
            duration_ms = len(audio)
            min_duration_ms = 29 * 1000
            max_duration_ms = 15 * 60 * 1000

            if duration_ms < min_duration_ms:
                logger.warning(f"File too short: {duration_ms/1000} seconds")
                return False
            if duration_ms > max_duration_ms:
                logger.warning(f"File too long: {duration_ms/1000} seconds")
                return False

            return True

        except Exception as e:
            logger.error(f"Audio validation error: {str(e)}")
            return False

    async def cleanup_job(self, job_id: str):
        job_dir = self.temp_dir / job_id
        if job_dir.exists():
            shutil.rmtree(job_dir)

    async def update_progress(self, job_id: str, progress: float, status: str = "processing", error: str = None):
        data = {
            "job_id": job_id,
            "status": status,
            "progress": progress,
            "updated_at": datetime.now().isoformat()
        }
        if error:
            data["error"] = error

        if supabase is None:
            LOCAL_PROGRESS[job_id] = data
            return
        await asyncio.to_thread(lambda: supabase.table("job_progress").upsert(data).execute())
