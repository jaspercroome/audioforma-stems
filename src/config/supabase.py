import logging
import os

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://lospfqgllrhgiplqmvgp.supabase.co")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")


def _create_client():
    """The Supabase client, or None when no key is set (local mode: nothing is persisted)."""
    if not SUPABASE_SERVICE_ROLE_KEY:
        logger.warning("SUPABASE_SERVICE_ROLE_KEY is not set: running without Supabase")
        return None
    from supabase import create_client

    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


supabase = _create_client()


def get_public_url(bucket: str, path: str) -> str:
    """Get the public URL for a file in a Supabase bucket"""
    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"
