from pydantic import BaseModel
from typing import Dict, Optional
from datetime import datetime

class AudioSeparationRequest(BaseModel):
    url: str
    artist: str
    track: str

class JobProgress(BaseModel):
    job_id: str
    status: str
    progress: float
    updated_at: datetime
    error: Optional[str] = None

class StemLookup(BaseModel):
    artist: str
    track: str
    directory: str
    created_at: datetime

class ProcessingResponse(BaseModel):
    job_id: str
    status: str
    files: Optional[Dict[str, str]] = None
    error: Optional[str] = None 