# src/app.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routes import health, audio
from fastapi.responses import FileResponse
from pathlib import Path
import logging
import os
from fastapi.exceptions import HTTPException
from typing import List

# Set up logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Audio Separator")

# Get environment variables with defaults
ENVIRONMENT = os.getenv('ENVIRONMENT', 'development')
DEFAULT_ALLOWED_ORIGINS = [
    "http://localhost:5173",  # Vite dev server
    "http://localhost:3000",  # Optional: Production URL
]

# In production, add additional origins from environment variable
ALLOWED_ORIGINS: List[str] = (
    os.getenv('ALLOWED_ORIGINS', '').split(',') 
    if ENVIRONMENT == 'production' and os.getenv('ALLOWED_ORIGINS') 
    else DEFAULT_ALLOWED_ORIGINS
)

# CORS middleware for API routes
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Create temp directory
TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)
logger.info(f"Using temp directory: {TEMP_DIR.absolute()}")

# Include routers
app.include_router(health.router)
app.include_router(audio.router, prefix="/api")

# File serving route
@app.get("/files/{path:path}")
async def serve_file(path: str):
    logger.info(f"Serving file through route: {path}")
    full_path = os.path.join(TEMP_DIR.absolute(), path)
    
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    response = FileResponse(
        full_path,
        media_type="audio/mpeg",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "Range, Origin, Accept, Content-Type",
            "Access-Control-Expose-Headers": "Content-Range, Content-Length, Accept-Ranges",
            "Accept-Ranges": "bytes",
            "Cache-Control": "max-age=604800, no-transform"
        }
    )
    
    logger.info(f"Response headers: {dict(response.headers.items())}")
    return response

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv('PORT', 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)