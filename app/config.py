"""Application configuration."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file from project root if it exists
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# LLM Model configuration
DEFAULT_MODEL = "claude-sonnet-5-5"
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Safeguards & Limits for Web UI
MAX_CODE_CHARS = int(os.getenv("MAX_CODE_CHARS", "60000"))
ANTHROPIC_TIMEOUT_SECONDS = float(os.getenv("ANTHROPIC_TIMEOUT_SECONDS", "60.0"))
ANTHROPIC_MAX_TOKENS = int(os.getenv("ANTHROPIC_MAX_TOKENS", "4096"))

# GitHub App Configurations
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID", "")
GITHUB_PRIVATE_KEY_PATH = os.getenv("GITHUB_PRIVATE_KEY_PATH", "")
GITHUB_PRIVATE_KEY = os.getenv("GITHUB_PRIVATE_KEY", "")
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")

# GitHub Review Safeguards & Thresholds
MIN_SEVERITY = os.getenv("MIN_SEVERITY", "low").lower()
MAX_FILES = int(os.getenv("MAX_FILES", "20"))
MAX_DIFF_CHARS = int(os.getenv("MAX_DIFF_CHARS", "100000"))
MAX_LLM_CALLS_PER_PR = int(os.getenv("MAX_LLM_CALLS_PER_PR", "8"))
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.5"))

# Database & Storage
# WAL mode creates companion files (-wal and -shm), so a directory volume is required
DEFAULT_DB_PATH = str(BASE_DIR / "data" / "reviews.db")
DB_PATH = os.getenv("DB_PATH", DEFAULT_DB_PATH)

# Security, Hardening & Quotas
TRUST_PROXY = os.getenv("TRUST_PROXY", "false").lower() in ("true", "1", "yes")
MAX_REVIEWS_PER_DAY = int(os.getenv("MAX_REVIEWS_PER_DAY", "200"))
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
