"""AI Code Review Engine using Anthropic Claude API."""
from __future__ import annotations

import json
import logging
import re
from typing import Literal, Optional

import anthropic
from anthropic import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)
from pydantic import ValidationError

from app import config
from reviewer.models import ReviewResult
from reviewer.prompts import (
    SYSTEM_PROMPT,
    build_retry_prompt,
    build_review_prompt,
)

logger = logging.getLogger(__name__)


class ReviewEngineError(Exception):
    """Base exception for code review engine failures."""
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ReviewAuthError(ReviewEngineError):
    """Raised when Anthropic API key is missing or invalid."""
    def __init__(self, message: str = "Anthropic API key is invalid or not configured. Please set ANTHROPIC_API_KEY in your environment."):
        super().__init__(message, status_code=500)


class ReviewTimeoutError(ReviewEngineError):
    """Raised when Anthropic API request times out."""
    def __init__(self, message: str = "Review request timed out while communicating with the AI service. Please try again with a smaller snippet."):
        super().__init__(message, status_code=504)


class ReviewTruncationError(ReviewEngineError):
    """Raised when AI output was cut off due to max_tokens limit."""
    def __init__(self, message: str = "Review response was truncated because it exceeded the maximum token limit. Please submit a smaller snippet or diff."):
        super().__init__(message, status_code=502)


class ReviewParsingError(ReviewEngineError):
    """Raised when the LLM returns invalid JSON that cannot be parsed after retry."""
    def __init__(self, message: str = "The AI review service returned an unparseable response after retry. Please try again."):
        super().__init__(message, status_code=502)


def extract_json_str(raw: str) -> str:
    """Extracts JSON substring, stripping markdown code fences if present."""
    text = raw.strip()
    # Check for markdown code fences like ```json ... ``` or ``` ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if match:
        text = match.group(1).strip()
    else:
        # Fallback: find outer matching braces
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            text = text[first_brace : last_brace + 1].strip()
    return text


class ReviewEngine:
    """Core review engine capable of being driven by API, CLI, or CI/CD bot."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        self.api_key = api_key if api_key is not None else config.ANTHROPIC_API_KEY
        self.model = model or config.ANTHROPIC_MODEL
        self.timeout = timeout if timeout is not None else config.ANTHROPIC_TIMEOUT_SECONDS
        self.max_tokens = max_tokens if max_tokens is not None else config.ANTHROPIC_MAX_TOKENS

    def _get_client(self) -> anthropic.Anthropic:
        if not self.api_key or not self.api_key.strip():
            raise ReviewAuthError("Anthropic API key is not configured. Please set ANTHROPIC_API_KEY in your .env or environment.")
        return anthropic.Anthropic(
            api_key=self.api_key.strip(),
            timeout=self.timeout,
        )

    def review(
        self,
        code: str,
        language: str = "plaintext",
        mode: Literal["code", "diff"] = "code",
        pr_title: Optional[str] = None,
        pr_description: Optional[str] = None,
        surrounding_code: Optional[str] = None,
        readme_excerpt: Optional[str] = None,
        static_findings: Optional[list[str]] = None,
        custom_guidelines: Optional[str] = None,
    ) -> ReviewResult:
        """
        Executes a code review against the Anthropic Claude API.

        Validates output against ReviewResult and performs 1 retry if JSON is malformed.
        Accepts optional PR context, surrounding code, static hints, and custom guidelines.
        """
        client = self._get_client()
        user_prompt = build_review_prompt(
            content=code,
            language=language,
            mode=mode,
            pr_title=pr_title,
            pr_description=pr_description,
            surrounding_code=surrounding_code,
            readme_excerpt=readme_excerpt,
            static_findings=static_findings,
            custom_guidelines=custom_guidelines,
        )

        messages = [
            {"role": "user", "content": user_prompt}
        ]

        # -------------------------------------------------------------
        # Attempt 1
        # -------------------------------------------------------------
        try:
            logger.info("Sending review request to Anthropic model: %s (mode=%s)", self.model, mode)
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                messages=messages,
            )
        except AuthenticationError as e:
            logger.error("Anthropic Authentication Error: %s", e)
            raise ReviewAuthError("Anthropic API key is invalid or not authorized. Check ANTHROPIC_API_KEY.") from e
        except APITimeoutError as e:
            logger.error("Anthropic Timeout Error: %s", e)
            raise ReviewTimeoutError() from e
        except RateLimitError as e:
            logger.error("Anthropic Rate Limit Error: %s", e)
            raise ReviewEngineError("Anthropic API rate limit exceeded. Please wait a moment and try again.", status_code=429) from e
        except APIConnectionError as e:
            logger.error("Anthropic Connection Error: %s", e)
            raise ReviewEngineError("Could not connect to Anthropic API. Please check network connectivity.", status_code=502) from e
        except APIError as e:
            logger.error("Anthropic API Error: %s", e)
            raise ReviewEngineError(f"Anthropic API returned an error: {e.message}", status_code=502) from e

        if response.stop_reason == "max_tokens":
            logger.warning("Anthropic response reached max_tokens limit (%s)", self.max_tokens)
            raise ReviewTruncationError()

        raw_text = ""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                raw_text += block.text

        # Parse & Validate Attempt 1
        try:
            cleaned_json = extract_json_str(raw_text)
            data = json.loads(cleaned_json)
            return ReviewResult.model_validate(data)
        except (json.JSONDecodeError, ValidationError, Exception) as err:
            logger.warning("Attempt 1 failed to parse or validate review JSON: %s. Initiating 1-shot retry.", err)
            parse_error = str(err)

        # -------------------------------------------------------------
        # Attempt 2: 1-Shot Retry with error feedback
        # -------------------------------------------------------------
        retry_prompt = build_retry_prompt(raw_text, parse_error)
        retry_messages = [
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": raw_text},
            {"role": "user", "content": retry_prompt},
        ]

        try:
            logger.info("Executing 1-shot retry with Anthropic model: %s", self.model)
            retry_response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                messages=retry_messages,
            )
        except APITimeoutError as e:
            raise ReviewTimeoutError("Retry request timed out while communicating with the AI service.") from e
        except APIError as e:
            raise ReviewEngineError(f"Anthropic API error during retry: {e.message}", status_code=502) from e

        if retry_response.stop_reason == "max_tokens":
            raise ReviewTruncationError("Review response was truncated during retry.")

        retry_raw_text = ""
        for block in retry_response.content:
            if getattr(block, "type", None) == "text":
                retry_raw_text += block.text

        try:
            retry_cleaned_json = extract_json_str(retry_raw_text)
            retry_data = json.loads(retry_cleaned_json)
            return ReviewResult.model_validate(retry_data)
        except Exception as retry_err:
            logger.error("1-shot retry also failed to produce valid JSON: %s\nOutput was: %s", retry_err, retry_raw_text)
            raise ReviewParsingError(
                f"Failed to obtain valid JSON review from AI after retry: {retry_err}"
            ) from retry_err

