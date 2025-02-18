from fastapi import APIRouter, UploadFile, BackgroundTasks, HTTPException
from ..processors.audio import AudioProcessor
from pathlib import Path
import json
from datetime import datetime
import shutil
import tempfile
import aiohttp
import aiofiles
from urllib.parse import urlparse
from pydantic import BaseModel

router = APIRouter()
JOBS_DIR = Path("jobs")
JOBS_DIR.mkdir(exist_ok=True)

async def process_audio(file_path: Path, job_id: str):
    processor = AudioProcessor()
    try:
        # Create an UploadFile from the saved file
        with open(file_path, 'rb') as f:
            temp_file = UploadFile(filename="input.mp3", file=f)
            result = await processor.process_file(temp_file, job_id)
        
        # Update final status - include the files from the result
        with open(JOBS_DIR / f"{job_id}.json", 'w') as f:
            json.dump({
                "status": "completed",
                "progress": 100,
                "files": result["files"],  # Include the files info
                "completed_at": datetime.now().isoformat()
            }, f)
    except Exception as e:
        with open(JOBS_DIR / f"{job_id}.json", 'w') as f:
            json.dump({
                "status": "error",
                "error": str(e),
                "completed_at": datetime.now().isoformat()
            }, f)

@router.post("/audio/separate")
async def separate_audio(file: UploadFile, background_tasks: BackgroundTasks):
    job_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_file = JOBS_DIR / f"{job_id}.json"
    
    # Save the uploaded file
    temp_dir = Path("temp_uploads")
    temp_dir.mkdir(exist_ok=True)
    temp_file_path = temp_dir / f"{job_id}.mp3"
    
    with temp_file_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    # Initialize job status
    with open(job_file, 'w') as f:
        json.dump({
            "status": "processing",
            "progress": 0,
            "started_at": datetime.now().isoformat()
        }, f)
    
    # Start processing in background
    background_tasks.add_task(process_audio, temp_file_path, job_id)
    
    return {"job_id": job_id}

# Add this class for the request body
class UrlRequest(BaseModel):
    url: str

@router.post("/audio/separate-from-url")
async def separate_audio_from_url(request: UrlRequest, background_tasks: BackgroundTasks):
    job_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_file = JOBS_DIR / f"{job_id}.json"
    
    # Create temp directory for download
    temp_dir = Path("temp_uploads")
    temp_dir.mkdir(exist_ok=True)
    temp_file_path = temp_dir / f"{job_id}.mp3"
    
    # Download the file
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(request.url) as response:
                if response.status != 200:
                    raise HTTPException(status_code=400, detail="Could not download file from URL")
                
                # Save the file
                async with aiofiles.open(temp_file_path, 'wb') as f:
                    await f.write(await response.read())
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error downloading file: {str(e)}")
    
    # Initialize job status
    with open(job_file, 'w') as f:
        json.dump({
            "status": "processing",
            "progress": 0,
            "started_at": datetime.now().isoformat()
        }, f)
    
    # Start processing in background
    background_tasks.add_task(process_audio, temp_file_path, job_id)
    
    return {"job_id": job_id}

@router.get("/audio/status/{job_id}")
async def get_status(job_id: str):
    job_file = JOBS_DIR / f"{job_id}.json"
    if not job_file.exists():
        raise HTTPException(status_code=404, detail="Job not found")
        
    with open(job_file) as f:
        return json.load(f) 