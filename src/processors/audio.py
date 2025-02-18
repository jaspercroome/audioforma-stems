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
        self.jobs_dir = Path("jobs")
        self.jobs_dir.mkdir(exist_ok=True)
        
    async def process_file(self, file: UploadFile, job_id: str) -> dict:
        try:
            # Generate unique directory for this processing job
            job_dir = self.temp_dir / job_id
            job_dir.mkdir(exist_ok=True)
            
            # Save uploaded file
            await self.update_progress(job_id, 0, "uploading")
            input_path = job_dir / "input.mp3"
            print(f"Saving file to {input_path}")
            with input_path.open("wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            
            # Validate file
            await self.update_progress(job_id, 10, "validating")
            if not self._validate_audio(input_path):
                print(f"File validation failed for {input_path}")
                raise HTTPException(status_code=400, detail="Invalid audio file")
            
            # Process with Demucs
            await self.update_progress(job_id, 20, "processing")
            
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
                        overall_progress = 20 + ((current_model * 100) + (current_seconds / 33.0 * 100)) / total_steps * 0.8
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
            
            # Move files to the expected location
            output_dir = job_dir / "mdx_extra"
            if not output_dir.exists():
                raise HTTPException(status_code=500, detail="Processing failed - no output directory")
            
            # List all files in output directory
            logger.info(f"Job directory: {job_dir}")
            logger.info(f"Output directory: {output_dir}")
            all_files = list(output_dir.glob('**/*.mp3'))
            logger.info(f"All files in output directory: {[f.name for f in all_files]}")
            
            # Check for all stem files with more flexible naming
            input_name = input_path.stem
            stem_files = {
                "vocals": next(output_dir.glob(f'**/{input_name}/vocals.mp3'), None),
                "drums": next(output_dir.glob(f'**/{input_name}/drums.mp3'), None),
                "bass": next(output_dir.glob(f'**/{input_name}/bass.mp3'), None),
                "other": next(output_dir.glob(f'**/{input_name}/other.mp3'), None)
            }
            
            # Log what we found
            for stem_name, stem_path in stem_files.items():
                logger.info(f"{stem_name} path found: {stem_path}")
                if not stem_path:
                    raise HTTPException(
                        status_code=500, 
                        detail=f"Processing failed - missing {stem_name} file. Found files: {[f.name for f in all_files]}"
                    )
            
            # Return paths using the actual filenames and relative paths
            return {
                "job_id": job_id,
                "status": "completed",
                "files": {
                    stem_name: f"/files/{job_id}/mdx_extra/{input_name}/{stem_path.name}"
                    for stem_name, stem_path in stem_files.items()
                    if stem_path
                }
            }
            
        except Exception as e:
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
                print(f"File too short: {duration_ms/1000} seconds")
                return False
            if duration_ms > max_duration_ms:
                print(f"File too long: {duration_ms/1000} seconds")
                return False
            
            return True
            
        except Exception as e:
            print(f"Audio validation error: {str(e)}")
            return False
    
    async def cleanup_job(self, job_id: str):
        job_dir = self.temp_dir / job_id
        if job_dir.exists():
            shutil.rmtree(job_dir)

    async def update_progress(self, job_id: str, progress: float, status: str = "processing"):
        with open(self.jobs_dir / f"{job_id}.json", 'w') as f:
            json.dump({
                "status": status,
                "progress": progress,
                "updated_at": datetime.now().isoformat()
            }, f)