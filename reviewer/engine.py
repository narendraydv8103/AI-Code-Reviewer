"""AI Code Review Engine supporting Anthropic Claude and Google Gemini providers."""
from __future__ import annotations

import json
import logging
import re
from typing import Literal, Optional

from pydantic import ValidationError

from app import config
from reviewer.errors import (
    ReviewAuthError,
    ReviewEngineError,
    ReviewParsingError,
    ReviewTimeoutError,
    ReviewTruncationError,
)
from reviewer.models import ReviewResult
from reviewer.prompts import (
    SYSTEM_PROMPT,
    build_retry_prompt,
    build_review_prompt,
)
from reviewer.providers import BaseProvider, get_provider

logger = logging.getLogger(__name__)

# Re-export error classes for backward compatibility
__all__ = [
    "ReviewEngine",
    "ReviewEngineError",
    "ReviewAuthError",
    "ReviewTimeoutError",
    "ReviewTruncationError",
    "ReviewParsingError",
    "extract_json_str",
]


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
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        self.provider_name = (provider or config.get_active_provider()).lower().strip()
        self.provider: BaseProvider = get_provider(
            provider_name=self.provider_name,
            api_key=api_key,
            model=model,
            timeout=timeout,
            max_tokens=max_tokens,
        )
        self.model = self.provider.model
        self.api_key = self.provider.api_key
        self.timeout = self.provider.timeout
        self.max_tokens = self.provider.max_tokens

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
        Executes a code review against the configured AI provider.

        Validates output against ReviewResult and performs 1 retry if JSON is malformed.
        Accepts optional PR context, surrounding code, static hints, and custom guidelines.
        """
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

        # -------------------------------------------------------------
        # Attempt 1
        # -------------------------------------------------------------
        logger.info(
            "Sending review request to %s model: %s (mode=%s)",
            self.provider.provider_name, self.model, mode
        )
        raw_text = self.provider.complete(
            system=SYSTEM_PROMPT,
            user=user_prompt,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
        )

        # Parse & Validate Attempt 1
        try:
            cleaned_json = extract_json_str(raw_text)
            data = json.loads(cleaned_json)
            return ReviewResult.model_validate(data)
        except (json.JSONDecodeError, ValidationError, Exception) as err:
            logger.warning(
                "Attempt 1 failed to parse or validate review JSON: %s. Initiating 1-shot retry.",
                err
            )
            parse_error = str(err)

        # -------------------------------------------------------------
        # Attempt 2: 1-Shot Retry with error feedback
        # -------------------------------------------------------------
        retry_prompt = build_retry_prompt(raw_text, parse_error)
        history = [
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": raw_text},
        ]

        logger.info(
            "Executing 1-shot retry with %s model: %s",
            self.provider.provider_name, self.model
        )
        retry_raw_text = self.provider.complete(
            system=SYSTEM_PROMPT,
            user=retry_prompt,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
            history=history,
        )

        try:
            retry_cleaned_json = extract_json_str(retry_raw_text)
            retry_data = json.loads(retry_cleaned_json)
            return ReviewResult.model_validate(retry_data)
        except Exception as retry_err:
            logger.error(
                "1-shot retry also failed to produce valid JSON: %s\nOutput was: %s",
                retry_err, retry_raw_text
            )
            raise ReviewParsingError(
                f"Failed to obtain valid JSON review from AI after retry: {retry_err}"
            ) from retry_err
