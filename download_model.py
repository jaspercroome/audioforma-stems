import os
import sys
import time
import torch
from demucs.pretrained import get_model
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# mdx_extra: the original /api/audio/separate flow.
# htdemucs_6s, htdemucs: the streaming /api/stream flow (6s adds piano and guitar).
MODELS = os.getenv("DEMUCS_MODELS", "mdx_extra,htdemucs_6s,htdemucs").split(",")

def download_with_retries(name, max_retries=3, retry_delay=5):
    for attempt in range(max_retries):
        try:
            logger.info(f"Downloading {name}: attempt {attempt + 1}/{max_retries}")
            # Configure torch hub to be more verbose
            torch.hub.set_dir('/root/.cache/torch/hub')

            # Force download with progress
            get_model(name)
            logger.info(f"{name} downloaded successfully!")
            return True
        except Exception as e:
            logger.error(f"Download failed: {str(e)}")
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                logger.error("Max retries reached. Download failed.")
                return False

if __name__ == "__main__":
    success = all(download_with_retries(name.strip()) for name in MODELS if name.strip())
    sys.exit(0 if success else 1)
