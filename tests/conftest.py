import os
import tempfile
import atexit
from pathlib import Path

import pytest

# Ensure a temporary SQLite DB is used for the entire test run.
# This avoids touching the real data directory and guarantees a fresh schema.

_temp_db_path = None

def _create_temp_db_path() -> str:
    global _temp_db_path
    # Create a temporary file that persists for the duration of the process.
    # Using NamedTemporaryFile with delete=False because SQLite may create -wal/-shm sidecars.
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    _temp_db_path = path
    return path

@pytest.fixture(scope="session", autouse=True)
def set_test_db_path():
    """Set DB_PATH env var and override app.config before any modules import the DB.
    The fixture runs before any test collection, so the environment variable is in
    place for modules that read it at import time. We also assign to the config
    singleton directly for safety.
    """
    temp_path = _create_temp_db_path()
    os.environ["DB_PATH"] = temp_path
    # Import after env is set to guarantee config reads the new value.
    from app import config
    config.DB_PATH = temp_path
    # Ensure the temporary file is removed after the test run.
    def _cleanup():
        try:
            Path(temp_path).unlink(missing_ok=True)
        except Exception:
            pass
    atexit.register(_cleanup)
    yield

# Clear the in‑memory IP rate‑limit cache before each test function.
@pytest.fixture(autouse=True)
def clear_rate_limit_cache():
    from app import security
    security.clear_rate_limit_cache()
    yield

