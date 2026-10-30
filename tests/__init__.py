"""Isolate the database for tests. Runs before any app module is imported."""
import atexit
import os
import shutil
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="ai_code_reviewer_tests_")
os.environ["DB_PATH"] = os.path.join(_TMP_DIR, "test_reviews.db")
atexit.register(shutil.rmtree, _TMP_DIR, ignore_errors=True)
