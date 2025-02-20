import sys
import time
import torch
from demucs.pretrained import get_model
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def download_with_retries(max_retries=3, retry_delay=5):
    for attempt in range(max_retries):
        try:
            logger.info(f"Download attempt {attempt + 1}/{max_retries}")
            # Configure torch hub to be more verbose
            torch.hub.set_dir('/root/.cache/torch/hub')
            
            # Force download with progress
            model = get_model('mdx_extra')
            logger.info("Model downloaded successfully!")
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
    success = download_with_retries()
    sys.exit(0 if success else 1) 