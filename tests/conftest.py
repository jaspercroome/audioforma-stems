import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Run the app in a scratch directory with the stand-in separator and no Supabase.
_WORKDIR = Path(tempfile.mkdtemp(prefix="stems-test-"))
os.environ.setdefault("STEMS_BACKEND", "filterbank")
os.environ["STREAM_DIR"] = str(_WORKDIR / "stream")
os.environ["STREAM_UPLOAD_DIR"] = str(_WORKDIR / "uploads")
os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
os.chdir(_WORKDIR)


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from src.app import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def workdir():
    return _WORKDIR
