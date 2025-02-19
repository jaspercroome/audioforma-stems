import os
from celery import Celery
from .processors.audio import AudioProcessor

# Use environment variable for Redis URL with a default fallback
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
celery = Celery('audioforma', broker=REDIS_URL, backend=REDIS_URL)

@celery.task(bind=True)
def process_audio(self, file_path: str):
    # Process audio and update progress
    self.update_state(state='PROGRESS', meta={'progress': 50})
    return {"result": "path/to/stems"} 