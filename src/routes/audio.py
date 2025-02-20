from fastapi import APIRouter, UploadFile, BackgroundTasks, HTTPException, Form
from ..processors.audio import AudioProcessor
from ..models.schemas import ProcessingResponse, AudioSeparationRequest
from ..config.supabase import supabase
from pathlib import Path
from datetime import datetime
import shutil
import aiohttp
import aiofiles
import logging

logger = logging.getLogger(__name__)
router = APIRouter()
JOBS_DIR = Path("jobs")
JOBS_DIR.mkdir(exist_ok=True)

async def process_audio(file_path: Path, job_id: str, artist: str, track: str):
    processor = AudioProcessor()
    try:
        # Create an UploadFile from the saved file
        with open(file_path, 'rb') as f:
            temp_file = UploadFile(filename="input.mp3", file=f)
            await processor.process_file(temp_file, job_id, artist, track)
    except Exception as e:
        logger.error(f"Error processing audio: {str(e)}")
        await processor.update_progress(job_id, 1.0, "error", str(e))
    finally:
        # Clean up the temporary upload file
        if file_path.exists():
            file_path.unlink()

@router.post("/audio/separate", response_model=ProcessingResponse)
async def separate_audio(
    background_tasks: BackgroundTasks,
    file: UploadFile,
    artist: str = Form(...),
    track: str = Form(...)
):
    job_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Save the uploaded file
    temp_dir = Path("temp_uploads")
    temp_dir.mkdir(exist_ok=True)
    temp_file_path = temp_dir / f"{job_id}.mp3"
    
    with temp_file_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    # Initialize job status
    processor = AudioProcessor()
    await processor.update_progress(job_id, 0, "processing")
    
    # Start processing in background
    background_tasks.add_task(process_audio, temp_file_path, job_id, artist, track)
    
    return {"job_id": job_id, "status": "processing"}

@router.post("/audio/separate-from-url", response_model=ProcessingResponse)
async def separate_audio_from_url(
    request: AudioSeparationRequest,
    background_tasks: BackgroundTasks
):
    job_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    
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
    processor = AudioProcessor()
    await processor.update_progress(job_id, 0, "processing")
    
    # Start processing in background
    background_tasks.add_task(process_audio, temp_file_path, job_id, request.artist, request.track)
    
    return {"job_id": job_id, "status": "processing"}

@router.get("/audio/status/{job_id}", response_model=ProcessingResponse)
async def get_status(job_id: str):
    response = supabase.table("job_progress").select("*").eq("job_id", job_id).execute()
    
    if not response.data:
        raise HTTPException(status_code=404, detail="Job not found")
        
    job_status = response.data[0]
    return {
        "job_id": job_status["job_id"],
        "status": job_status["status"],
        "files": job_status.get("files"),
        "error": job_status.get("error")
    } 