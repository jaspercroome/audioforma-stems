from celery import Celery
from .processors.audio import AudioProcessor

celery = Celery('audioforma', broker='redis://localhost:6379/0')

@celery.task(bind=True)
def process_audio(self, file_path: str):
    # Process audio and update progress
    self.update_state(state='PROGRESS', meta={'progress': 50})
    return {"result": "path/to/stems"} 