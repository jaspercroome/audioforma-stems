# src/processors/audio.py
from fastapi import UploadFile, HTTPException
from pathlib import Path
import shutil
import demucs.separate
from datetime import datetime
import re
import asyncio
import sys
from io import StringIO
import json
import logging
from ..config.supabase import supabase, get_public_url

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class StdoutCapture:
    def __init__(self, callback, loop):
        self.callback = callback
        self.original_stdout = sys.stdout
        self.loop = loop
        
    def write(self, text):
        self.original_stdout.write(text)
        self.loop.create_task(self.callback(text))
        
    def flush(self):
        self.original_stdout.flush()

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
            if not self._validate_audio(input_path):
                logger.error(f"File validation failed for {input_path}")
                raise HTTPException(status_code=400, detail="Invalid audio file")
            
            # Process with Demucs
            await self.update_progress(job_id, 0.2, "processing")
            
            # Track progress
            total_steps = 4  # mdx_extra uses 4 models
            current_model = 0
            progress_pattern = re.compile(r'(\d+\.\d+)/\d+\.\d+')
            
            async def process_output(text: str):
                nonlocal current_model
                if "%" in text:
                    match = progress_pattern.search(text)
                    if match:
                        current_seconds = float(match.group(1))
                        overall_progress = 0.2 + ((current_model * 100) + (current_seconds / 33.0 * 100)) / total_steps * 0.6
                        await self.update_progress(job_id, round(overall_progress, 2), "processing")
                elif "Separating track" in text:
                    current_model += 1
            
            # Capture stdout in real-time
            loop = asyncio.get_event_loop()
            stdout_capture = StdoutCapture(process_output, loop)
            original_stdout = sys.stdout
            sys.stdout = stdout_capture
            
            try:
                demucs.separate.main([
                    str(input_path),
                    "-n", "mdx_extra",
                    "--mp3",
                    "-o", str(job_dir)
                ])
            finally:
                sys.stdout = original_stdout
            
            # Upload files to Supabase
            await self.update_progress(job_id, 0.9, "uploading")
            
            output_dir = job_dir / "mdx_extra" / input_path.stem
            if not output_dir.exists():
                raise HTTPException(status_code=500, detail="Processing failed - no output directory")
            
            # Upload each stem file and get public URLs
            stem_files = {}
            for stem_name in ["vocals", "drums", "bass", "other"]:
                stem_path = output_dir / f"{stem_name}.mp3"
                if not stem_path.exists():
                    raise HTTPException(status_code=500, detail=f"Processing failed - missing {stem_name} file")
                
                # Upload to Supabase
                with open(stem_path, "rb") as f:
                    supabase.storage.from_(self.bucket_name).upload(
                        f"{job_id}/{stem_name}.mp3",
                        f.read(),
                        {"content-type": "audio/mpeg"}
                    )
                stem_files[stem_name] = get_public_url(self.bucket_name, f"{job_id}/{stem_name}.mp3")
            
            # Store metadata in stem_lookup table
            supabase.table("stem_lookup").insert({
                "artist": artist,
                "track": track,
                "directory": job_id
            }).execute()
            
            # Clean up local files
            await self.cleanup_job(job_id)
            
            return {
                "job_id": job_id,
                "status": "completed",
                "files": stem_files
            }
            
        except Exception as e:
            logger.error(f"Processing error: {str(e)}")
            await self.update_progress(job_id, 1.0, "error", str(e))
            raise HTTPException(status_code=500, detail=str(e))
    
    def _validate_audio(self, file_path: Path) -> bool:
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_mp3(str(file_path))
            
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
            
        supabase.table("job_progress").upsert(data).execute()