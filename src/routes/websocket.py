# src/routes/websocket.py
from fastapi import APIRouter, WebSocket, UploadFile
import asyncio
from ..processors.audio import AudioProcessor
from ..models.states import ProcessingState
import tempfile
from fastapi import WebSocketDisconnect

router = APIRouter()
audio_processor = AudioProcessor()

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    try:
        # Receive the binary file data
        file_data = await websocket.receive_bytes()
        
        # Create a temporary file
        temp_file = UploadFile(
            filename="audio.mp3",
            file=tempfile.SpooledTemporaryFile()
        )
        temp_file.file.write(file_data)
        temp_file.file.seek(0)
        
        # Process the file
        result = await audio_processor.process_file(temp_file, websocket)
        await websocket.send_json(result)
        
        # Schedule cleanup after 1 hour
        if result.get("job_id"):
            asyncio.create_task(
                cleanup_after_delay(result["job_id"], delay_seconds=3600)
            )
            
    except WebSocketDisconnect:
        pass  # Client disconnected, no need to send error
    except Exception as e:
        try:
            await websocket.send_json({"error": str(e)})
        except:
            pass  # Can't send error if client is disconnected
    finally:
        try:
            await websocket.close()
        except:
            pass  # Already closed

async def cleanup_after_delay(job_id: str, delay_seconds: int):
    await asyncio.sleep(delay_seconds)
    await audio_processor.cleanup_job(job_id)
