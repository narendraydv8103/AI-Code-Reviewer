"""LLM Provider abstractions for AI Code Reviewer.

Supports Anthropic Claude and Google Gemini (REST API), with extensible BaseProvider interface.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import httpx

from app import config
from reviewer.errors import (
    ReviewAuthError,
    ReviewEngineError,
    ReviewTimeoutError,
    ReviewTruncationError,
)

logger = logging.getLogger(__name__)


class BaseProvider(ABC):
    """Abstract base class for all LLM providers."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        self.api_key = api_key or ""
        self.model = model or ""
        self.timeout = timeout if timeout is not None else 60.0
        self.max_tokens = max_tokens if max_tokens is not None else 4096

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Returns the canonical name of the provider (e.g. 'anthropic', 'gemini')."""
        pass

    @abstractmethod
    def complete(
        self,
        system: str,
        user: str,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """
        Executes a prompt completion call against the provider API.

        Args:
            system: System prompt / instructions.
            user: User prompt text.
            max_tokens: Optional token completion override.
            timeout: Optional HTTP timeout override.
            history: Optional multi-turn message history for 1-shot retry flows.

        Returns:
            Raw response text from the model.
        """
        pass


class AnthropicProvider(BaseProvider):
    """Anthropic Claude provider using the official anthropic SDK."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        super().__init__(
            api_key=api_key if api_key is not None else config.ANTHROPIC_API_KEY,
            model=model or config.ANTHROPIC_MODEL,
            timeout=timeout if timeout is not None else config.ANTHROPIC_TIMEOUT_SECONDS,
            max_tokens=max_tokens if max_tokens is not None else config.ANTHROPIC_MAX_TOKENS,
        )

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def _get_client(self):
        import anthropic
        if not self.api_key or not self.api_key.strip():
            raise ReviewAuthError("Anthropic API key is not configured. Please set ANTHROPIC_API_KEY in your .env or environment.")
        return anthropic.Anthropic(
            api_key=self.api_key.strip(),
            timeout=self.timeout,
        )

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        from anthropic import (
            APIConnectionError,
            APIError,
            APITimeoutError,
            AuthenticationError,
            RateLimitError,
        )

        client = self._get_client()
        effective_max_tokens = max_tokens or self.max_tokens

        messages: List[Dict[str, str]] = []
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user})

        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=effective_max_tokens,
                system=system,
                messages=messages,
            )
        except AuthenticationError as e:
            logger.error("Anthropic Authentication Error: %s", e)
            raise ReviewAuthError("Anthropic API key is invalid or not authorized. Check ANTHROPIC_API_KEY.") from e
        except APITimeoutError as e:
            logger.error("Anthropic Timeout Error: %s", e)
            raise ReviewTimeoutError("Review request timed out while communicating with Anthropic.") from e
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
            logger.warning("Anthropic response reached max_tokens limit (%s)", effective_max_tokens)
            raise ReviewTruncationError("Review response was truncated because it exceeded Anthropic's maximum token limit. Please submit a smaller snippet or diff.")

        raw_text = ""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                raw_text += block.text

        return raw_text


class GeminiProvider(BaseProvider):
    """
    Google Gemini provider using REST API with httpx.
    Authenticates via x-goog-api-key header and enforces JSON structured output.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        super().__init__(
            api_key=api_key if api_key is not None else config.GEMINI_API_KEY,
            model=model or config.GEMINI_MODEL,
            timeout=timeout if timeout is not None else config.GEMINI_TIMEOUT_SECONDS,
            max_tokens=max_tokens if max_tokens is not None else config.GEMINI_MAX_TOKENS,
        )

    @property
    def provider_name(self) -> str:
        return "gemini"

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        if not self.api_key or not self.api_key.strip():
            raise ReviewAuthError("Gemini API key is not configured. Please set GEMINI_API_KEY in your .env or environment.")

        effective_max_tokens = max_tokens or self.max_tokens
        effective_timeout = timeout or self.timeout

        # Construct Gemini API REST endpoint
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key.strip(),
        }

        # Build contents structure with multi-turn mapping
        contents: List[Dict[str, Any]] = []
        if history:
            for msg in history:
                role = "user" if msg.get("role") == "user" else "model"
                contents.append({
                    "role": role,
                    "parts": [{"text": msg.get("content", "")}]
                })
        contents.append({
            "role": "user",
            "parts": [{"text": user}]
        })

        payload = {
            "systemInstruction": {
                "parts": [{"text": system}]
            },
            "contents": contents,
            "generationConfig": {
                "responseMimeType": "application/json",
                "maxOutputTokens": effective_max_tokens,
            }
        }

        max_retries = 3
        last_exception: Optional[Exception] = None

        for attempt in range(1, max_retries + 1):
            try:
                with httpx.Client(timeout=effective_timeout) as client:
                    response = client.post(url, headers=headers, json=payload)

                # Retry on 429 (rate limit) or 503 (service unavailable) with exponential backoff
                if response.status_code in (429, 503) and attempt < max_retries:
                    retry_after = response.headers.get("Retry-After")
                    wait_seconds: Optional[float] = None
                    if retry_after:
                        try:
                            wait_seconds = float(retry_after)
                        except ValueError:
                            wait_seconds = None
                    if wait_seconds is None or wait_seconds <= 0:
                        wait_seconds = 2.0 * (2 ** (attempt - 1))  # 2.0s, 4.0s

                    logger.warning(
                        "Gemini API returned HTTP %s (attempt %d/%d). Backing off for %.1f seconds...",
                        response.status_code, attempt, max_retries, wait_seconds
                    )
                    time.sleep(wait_seconds)
                    continue

                # Handle HTTP errors
                if response.status_code == 400:
                    err_body = response.text
                    logger.error("Gemini API HTTP 400 Bad Request: %s", err_body)
                    if "API_KEY" in err_body or "key" in err_body.lower() or "invalid_argument" in err_body.lower():
                        raise ReviewAuthError("Gemini API key is invalid or not authorized. Check GEMINI_API_KEY in your environment.")
                    raise ReviewEngineError(f"Gemini API bad request (HTTP 400): {err_body}", status_code=400)

                if response.status_code == 403:
                    logger.error("Gemini API HTTP 403 Forbidden: %s", response.text)
                    raise ReviewAuthError("Gemini API access forbidden or API key invalid. Check GEMINI_API_KEY in your environment.")

                if response.status_code == 429:
                    logger.error("Gemini API HTTP 429 Rate Limit: %s", response.text)
                    raise ReviewEngineError(
                        "Gemini free-tier rate limit reached. Please wait a moment and try again shortly.",
                        status_code=429
                    )

                if response.status_code >= 500:
                    logger.error("Gemini API HTTP %d Server Error: %s", response.status_code, response.text)
                    raise ReviewEngineError(
                        f"Gemini API returned server error (HTTP {response.status_code}). Please try again later.",
                        status_code=502
                    )

                if response.status_code != 200:
                    raise ReviewEngineError(
                        f"Gemini API unexpected response (HTTP {response.status_code}): {response.text}",
                        status_code=response.status_code
                    )

                data = response.json()
                return self._parse_gemini_response(data)

            except httpx.TimeoutException as te:
                logger.error("Gemini API Timeout: %s", te)
                raise ReviewTimeoutError("Review request timed out while communicating with Google Gemini API.") from te
            except httpx.NetworkError as ne:
                logger.error("Gemini API Network Error: %s", ne)
                raise ReviewEngineError("Could not connect to Google Gemini API. Please check network connectivity.", status_code=502) from ne
            except (ReviewEngineError, ReviewAuthError, ReviewTimeoutError, ReviewTruncationError):
                raise
            except Exception as e:
                logger.exception("Unexpected error during Gemini API completion: %s", e)
                last_exception = e
                break

        if last_exception:
            raise ReviewEngineError(f"Gemini API request failed: {last_exception}") from last_exception

        raise ReviewEngineError("Gemini API call failed after retries.")

    def _parse_gemini_response(self, data: Dict[str, Any]) -> str:
        """Extracts text and validates safety / truncation signals from Gemini response JSON."""
        # 1. Check for prompt safety blocks
        prompt_feedback = data.get("promptFeedback", {})
        block_reason = prompt_feedback.get("blockReason")
        if block_reason:
            logger.warning("Gemini prompt blocked due to policy: %s", block_reason)
            raise ReviewEngineError(f"Google Gemini blocked the review prompt due to safety policy ({block_reason}).")

        # 2. Check candidates existence
        candidates = data.get("candidates")
        if not candidates or len(candidates) == 0:
            logger.warning("Gemini returned response with empty candidates: %s", data)
            raise ReviewEngineError("Google Gemini returned an empty response with no candidate completions.")

        candidate = candidates[0]

        # 3. Check finishReason
        finish_reason = (candidate.get("finishReason") or "").upper()
        if finish_reason == "MAX_TOKENS":
            logger.warning("Gemini output truncated: finishReason=MAX_TOKENS")
            raise ReviewTruncationError("Review response was truncated because it exceeded Gemini's maximum token limit. Please submit a smaller snippet or diff.")

        if finish_reason in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"):
            logger.warning("Gemini candidate blocked: finishReason=%s", finish_reason)
            raise ReviewEngineError(f"Google Gemini blocked the review response due to safety policy ({finish_reason}).")

        # 4. Extract text from parts
        content = candidate.get("content", {})
        parts = content.get("parts", [])
        raw_text = "".join(part.get("text", "") for part in parts if isinstance(part, dict) and "text" in part)

        if not raw_text.strip():
            logger.warning("Gemini candidate content contained no text: %s", candidate)
            raise ReviewEngineError("Google Gemini candidate content contained no text output.")

        return raw_text


def get_provider(
    provider_name: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    timeout: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> BaseProvider:
    """
    Factory function for instantiating LLM providers.

    Extensible: add new providers (e.g. Groq, Ollama) by creating a BaseProvider subclass
    and registering its key here.
    """
    name = (provider_name or config.get_active_provider()).lower().strip()

    if name == "anthropic":
        return AnthropicProvider(
            api_key=api_key,
            model=model,
            timeout=timeout,
            max_tokens=max_tokens,
        )
    elif name == "gemini":
        return GeminiProvider(
            api_key=api_key,
            model=model,
            timeout=timeout,
            max_tokens=max_tokens,
        )
    else:
        raise ValueError(
            f"Unsupported LLM provider: '{name}'. Supported providers are: 'anthropic', 'gemini'."
        )
