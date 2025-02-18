# src/models/states.py
from enum import Enum

class ProcessingState(str, Enum):
    UPLOADING = "uploading"
    VALIDATING = "validating"
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"
    PROGRESS = "progress"