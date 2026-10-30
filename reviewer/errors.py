"""Domain exceptions for AI Code Reviewer."""
from __future__ import annotations

from typing import Optional

from app import config


class ReviewEngineError(Exception):
    """Base exception for code review engine failures."""
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ReviewAuthError(ReviewEngineError):
    """Raised when API key is missing or invalid."""
    def __init__(self, message: Optional[str] = None):
        if not message:
            provider = config.get_active_provider()
            key_name = "GEMINI_API_KEY" if provider == "gemini" else "ANTHROPIC_API_KEY"
            provider_title = "Google Gemini" if provider == "gemini" else "Anthropic"
            message = f"{provider_title} API key is invalid or not configured. Please set {key_name} in your environment."
        super().__init__(message, status_code=500)


class ReviewTimeoutError(ReviewEngineError):
    """Raised when AI API request times out."""
    def __init__(self, message: Optional[str] = None):
        if not message:
            provider = config.get_active_provider()
            provider_title = "Google Gemini" if provider == "gemini" else "Anthropic"
            message = f"Review request timed out while communicating with {provider_title}. Please try again with a smaller snippet."
        super().__init__(message, status_code=504)


class ReviewTruncationError(ReviewEngineError):
    """Raised when AI output was cut off due to max_tokens limit."""
    def __init__(self, message: Optional[str] = None):
        if not message:
            provider = config.get_active_provider()
            provider_title = "Google Gemini" if provider == "gemini" else "Anthropic"
            message = f"Review response was truncated because it exceeded {provider_title}'s maximum token limit. Please submit a smaller snippet or diff."
        super().__init__(message, status_code=502)


class ReviewParsingError(ReviewEngineError):
    """Raised when the LLM returns invalid JSON that cannot be parsed after retry."""
    def __init__(self, message: Optional[str] = None):
        if not message:
            provider = config.get_active_provider()
            provider_title = "Google Gemini" if provider == "gemini" else "Anthropic"
            message = f"The {provider_title} review service returned an unparseable response after retry. Please try again."
        super().__init__(message, status_code=502)
