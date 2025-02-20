from supabase import create_client
import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = "https://lospfqgllrhgiplqmvgp.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

def get_public_url(bucket: str, path: str) -> str:
    """Get the public URL for a file in a Supabase bucket"""
    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}" 