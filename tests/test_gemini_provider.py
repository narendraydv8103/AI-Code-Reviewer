"""Tests for Google Gemini provider and multi-provider selection."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx

from app import config
from reviewer.engine import (
    ReviewAuthError,
    ReviewEngine,
    ReviewEngineError,
    ReviewParsingError,
    ReviewTimeoutError,
    ReviewTruncationError,
)
from reviewer.models import ReviewResult
from reviewer.providers import (
    AnthropicProvider,
    GeminiProvider,
    get_provider,
)
from tests.base import BaseTestCase


class TestProviderSelection(BaseTestCase):
    """Verifies provider selection via configuration and factory."""

    def test_provider_selection_anthropic(self):
        with patch.object(config, "get_active_provider", return_value="anthropic"):
            provider = get_provider(api_key="mock-key")
            self.assertIsInstance(provider, AnthropicProvider)
            self.assertEqual(provider.provider_name, "anthropic")

    def test_provider_selection_gemini(self):
        with patch.object(config, "get_active_provider", return_value="gemini"):
            provider = get_provider(api_key="mock-key")
            self.assertIsInstance(provider, GeminiProvider)
            self.assertEqual(provider.provider_name, "gemini")

    def test_unknown_provider_raises_value_error(self):
        with self.assertRaises(ValueError):
            get_provider(provider_name="unknown_provider")


class TestGeminiProvider(BaseTestCase):
    """Verifies Google Gemini provider requests, responses, and error mappings."""

    def test_missing_api_key_raises_auth_error(self):
        provider = GeminiProvider(api_key="")
        with self.assertRaises(ReviewAuthError) as ctx:
            provider.complete(system="sys", user="user")
        self.assertIn("GEMINI_API_KEY", str(ctx.exception))

    @patch("httpx.Client.post")
    def test_gemini_success_path(self, mock_post):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": json.dumps({"summary": "Gemini clean code", "issues": []})}
                        ],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 50,
                "candidatesTokenCount": 20,
            },
        }
        mock_post.return_value = mock_response

        provider = GeminiProvider(api_key="test-gemini-key", model="gemini-2.5-flash")
        result_text = provider.complete(system="System instructions", user="User code")

        self.assertEqual(mock_post.call_count, 1)
        args, kwargs = mock_post.call_args
        url = args[0]
        headers = kwargs["headers"]
        payload = kwargs["json"]

        # Check key is sent in header, NOT in URL
        self.assertIn("models/gemini-2.5-flash:generateContent", url)
        self.assertNotIn("test-gemini-key", url)
        self.assertEqual(headers["x-goog-api-key"], "test-gemini-key")
        self.assertEqual(headers["Content-Type"], "application/json")

        # Check payload structure
        self.assertEqual(payload["systemInstruction"]["parts"][0]["text"], "System instructions")
        self.assertEqual(payload["contents"][0]["parts"][0]["text"], "User code")
        self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")

        data = json.loads(result_text)
        self.assertEqual(data["summary"], "Gemini clean code")

    @patch("httpx.Client.post")
    def test_gemini_empty_candidates_raises_engine_error(self, mock_post):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"candidates": []}
        mock_post.return_value = mock_response

        provider = GeminiProvider(api_key="test-key")
        with self.assertRaises(ReviewEngineError) as ctx:
            provider.complete(system="sys", user="user")
        self.assertIn("no candidate completions", str(ctx.exception))

    @patch("httpx.Client.post")
    def test_gemini_prompt_blocked_by_safety(self, mock_post):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [],
            "promptFeedback": {"blockReason": "SAFETY"},
        }
        mock_post.return_value = mock_response

        provider = GeminiProvider(api_key="test-key")
        with self.assertRaises(ReviewEngineError) as ctx:
            provider.complete(system="sys", user="user")
        self.assertIn("safety policy", str(ctx.exception))

    @patch("httpx.Client.post")
    def test_gemini_candidate_safety_block(self, mock_post):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {"parts": []},
                    "finishReason": "SAFETY",
                }
            ]
        }
        mock_post.return_value = mock_response

        provider = GeminiProvider(api_key="test-key")
        with self.assertRaises(ReviewEngineError) as ctx:
            provider.complete(system="sys", user="user")
        self.assertIn("safety policy", str(ctx.exception))

    @patch("httpx.Client.post")
    def test_gemini_max_tokens_truncation(self, mock_post):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Partial text..."}]},
                    "finishReason": "MAX_TOKENS",
                }
            ]
        }
        mock_post.return_value = mock_response

        provider = GeminiProvider(api_key="test-key")
        with self.assertRaises(ReviewTruncationError):
            provider.complete(system="sys", user="user")

    @patch("httpx.Client.post")
    def test_gemini_400_invalid_key_raises_auth_error(self, mock_post):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 400
        mock_response.text = '{"error": {"message": "API_KEY_INVALID", "code": 400}}'
        mock_post.return_value = mock_response

        provider = GeminiProvider(api_key="test-key")
        with self.assertRaises(ReviewAuthError) as ctx:
            provider.complete(system="sys", user="user")
        self.assertIn("GEMINI_API_KEY", str(ctx.exception))

    @patch("httpx.Client.post")
    def test_gemini_403_forbidden_raises_auth_error(self, mock_post):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 403
        mock_response.text = '{"error": {"message": "Permission denied", "code": 403}}'
        mock_post.return_value = mock_response

        provider = GeminiProvider(api_key="test-key")
        with self.assertRaises(ReviewAuthError) as ctx:
            provider.complete(system="sys", user="user")
        self.assertIn("GEMINI_API_KEY", str(ctx.exception))

    @patch("time.sleep")
    @patch("httpx.Client.post")
    def test_gemini_429_exponential_backoff_and_recovery(self, mock_post, mock_sleep):
        resp_429 = MagicMock(spec=httpx.Response)
        resp_429.status_code = 429
        resp_429.headers = {"Retry-After": "0.1"}

        resp_200 = MagicMock(spec=httpx.Response)
        resp_200.status_code = 200
        resp_200.json.return_value = {
            "candidates": [
                {
                    "content": {"parts": [{"text": json.dumps({"summary": "Recovered", "issues": []})}]},
                    "finishReason": "STOP",
                }
            ]
        }

        mock_post.side_effect = [resp_429, resp_200]

        provider = GeminiProvider(api_key="test-key")
        text = provider.complete(system="sys", user="user")

        self.assertEqual(mock_post.call_count, 2)
        mock_sleep.assert_called_once_with(0.1)
        self.assertIn("Recovered", text)

    @patch("time.sleep")
    @patch("httpx.Client.post")
    def test_gemini_429_exhausted_raises_friendly_error(self, mock_post, mock_sleep):
        resp_429 = MagicMock(spec=httpx.Response)
        resp_429.status_code = 429
        resp_429.headers = {}
        resp_429.text = "Quota exceeded"

        mock_post.return_value = resp_429

        provider = GeminiProvider(api_key="test-key")
        with self.assertRaises(ReviewEngineError) as ctx:
            provider.complete(system="sys", user="user")

        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("free-tier rate limit reached", str(ctx.exception))
        self.assertEqual(mock_post.call_count, 3)

    @patch("httpx.Client.post")
    def test_gemini_timeout_mapping(self, mock_post):
        mock_post.side_effect = httpx.ReadTimeout("Connection timed out")

        provider = GeminiProvider(api_key="test-key")
        with self.assertRaises(ReviewTimeoutError):
            provider.complete(system="sys", user="user")

    @patch("httpx.Client.post")
    def test_gemini_review_engine_invalid_json_with_retry(self, mock_post):
        # Attempt 1: Malformed JSON
        resp1 = MagicMock(spec=httpx.Response)
        resp1.status_code = 200
        resp1.json.return_value = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Not valid JSON"}]},
                    "finishReason": "STOP",
                }
            ]
        }

        # Attempt 2: Valid JSON
        resp2 = MagicMock(spec=httpx.Response)
        resp2.status_code = 200
        resp2.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "summary": "Fixed on retry",
                                    "issues": [
                                        {
                                            "line": 42,
                                            "severity": "high",
                                            "category": "security",
                                            "title": "SQL Injection",
                                            "explanation": "Param flaw",
                                            "suggested_fix": "fix()",
                                        }
                                    ],
                                })
                            }
                        ]
                    },
                    "finishReason": "STOP",
                }
            ]
        }

        mock_post.side_effect = [resp1, resp2]

        engine = ReviewEngine(provider="gemini", api_key="test-key")
        res = engine.review("code")

        self.assertIsInstance(res, ReviewResult)
        self.assertEqual(res.summary, "Fixed on retry")
        self.assertEqual(len(res.issues), 1)
        self.assertEqual(mock_post.call_count, 2)

    @patch("httpx.Client.post")
    def test_gemini_review_engine_invalid_json_fails_after_retry(self, mock_post):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json.return_value = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Unparseable"}]},
                    "finishReason": "STOP",
                }
            ]
        }
        mock_post.return_value = resp

        engine = ReviewEngine(provider="gemini", api_key="test-key")
        with self.assertRaises(ReviewParsingError):
            engine.review("code")
        self.assertEqual(mock_post.call_count, 2)
