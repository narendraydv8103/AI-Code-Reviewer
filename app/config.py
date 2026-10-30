"""Application configuration."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file from project root if it exists
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# LLM Provider selection ('anthropic' | 'gemini', default: 'anthropic')
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").lower().strip()

# Anthropic Claude configuration
DEFAULT_MODEL = "claude-sonnet-5-5"
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_TIMEOUT_SECONDS = float(os.getenv("ANTHROPIC_TIMEOUT_SECONDS", "60.0"))
ANTHROPIC_MAX_TOKENS = int(os.getenv("ANTHROPIC_MAX_TOKENS", "4096"))

# Google Gemini configuration (Free tier available at aistudio.google.com)
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_TIMEOUT_SECONDS = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "60.0"))
GEMINI_MAX_TOKENS = int(os.getenv("GEMINI_MAX_TOKENS", "4096"))

# Delay between LLM calls to respect free tier rate limits (15 RPM for Gemini -> 4.0s)
if "LLM_MIN_INTERVAL_SECONDS" in os.environ:
    LLM_MIN_INTERVAL_SECONDS = float(os.environ["LLM_MIN_INTERVAL_SECONDS"])
else:
    LLM_MIN_INTERVAL_SECONDS = 4.0 if LLM_PROVIDER == "gemini" else 0.0


def get_active_provider() -> str:
    """Returns the currently active LLM provider name."""
    return os.getenv("LLM_PROVIDER", LLM_PROVIDER).lower().strip()


def get_active_model() -> str:
    """Returns the configured model for the active LLM provider."""
    provider = get_active_provider()
    if provider == "gemini":
        return os.getenv("GEMINI_MODEL", GEMINI_MODEL)
    return os.getenv("ANTHROPIC_MODEL", ANTHROPIC_MODEL)


def get_active_api_key() -> str:
    """Returns the API key for the active LLM provider."""
    provider = get_active_provider()
    if provider == "gemini":
        return os.getenv("GEMINI_API_KEY", GEMINI_API_KEY)
    return os.getenv("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY)


def has_active_api_key() -> bool:
    """Returns True if the active provider has an API key configured."""
    return bool(get_active_api_key().strip())


def get_active_timeout() -> float:
    """Returns HTTP timeout in seconds for active provider."""
    provider = get_active_provider()
    if provider == "gemini":
        return float(os.getenv("GEMINI_TIMEOUT_SECONDS", str(GEMINI_TIMEOUT_SECONDS)))
    return float(os.getenv("ANTHROPIC_TIMEOUT_SECONDS", str(ANTHROPIC_TIMEOUT_SECONDS)))


def get_active_max_tokens() -> int:
    """Returns maximum output token limit for active provider."""
    provider = get_active_provider()
    if provider == "gemini":
        return int(os.getenv("GEMINI_MAX_TOKENS", str(GEMINI_MAX_TOKENS)))
    return int(os.getenv("ANTHROPIC_MAX_TOKENS", str(ANTHROPIC_MAX_TOKENS)))


# Safeguards & Limits for Web UI
MAX_CODE_CHARS = int(os.getenv("MAX_CODE_CHARS", "60000"))

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
