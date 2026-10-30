"""Test suite for Phase 2: GitHub PR Review Bot."""
import hashlib
import hmac
import json
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import config
from app.main import app
from github_bot import auth, diff_utils, publisher, service
from github_bot.client import GitHubClient, GitHubReviewCommentError
from github_bot.webhook import verify_signature
from reviewer.models import ReviewIssue, ReviewResult
from tests.base import BaseTestCase


class TestDiffUtils(unittest.TestCase):
    """Verifies patch parsing, line mapping, and file filter rules."""

    def test_get_commentable_lines_single_hunk(self):
        patch = (
            "@@ -10,3 +10,4 @@ def foo():\n"
            " context 10\n"
            "-removed line\n"
            "+added line 11\n"
            "+added line 12\n"
            " context 13\n"
        )
        lines = diff_utils.get_commentable_lines(patch)
        # Hunk starts at +10
        # Line 10: context ' ' -> 10
        # Line -: removed -> not added to new file
        # Line +: added -> 11
        # Line +: added -> 12
        # Line ' ': context -> 13
        self.assertEqual(lines, {10, 11, 12, 13})

    def test_get_commentable_lines_multi_hunk(self):
        patch = (
            "@@ -1,2 +1,2 @@\n"
            "+line 1\n"
            " line 2\n"
            "@@ -20,2 +20,3 @@\n"
            "+line 20\n"
            "+line 21\n"
            " line 22\n"
        )
        lines = diff_utils.get_commentable_lines(patch)
        self.assertEqual(lines, {1, 2, 20, 21, 22})

    def test_get_commentable_lines_no_newline_marker(self):
        patch = (
            "@@ -1,2 +1,2 @@\n"
            "+line 1\n"
            "\\ No newline at end of file\n"
            " line 2\n"
        )
        lines = diff_utils.get_commentable_lines(patch)
        self.assertEqual(lines, {1, 2})

    def test_is_reviewable_file_exclusions(self):
        # Lockfile
        self.assertFalse(diff_utils.is_reviewable_file("package-lock.json", "+{}")[0])
        self.assertFalse(diff_utils.is_reviewable_file("poetry.lock", "+{}")[0])

        # Vendor / Build dirs
        self.assertFalse(diff_utils.is_reviewable_file("node_modules/foo/index.js", "+code")[0])
        self.assertFalse(diff_utils.is_reviewable_file("dist/bundle.js", "+code")[0])

        # Binary / assets
        self.assertFalse(diff_utils.is_reviewable_file("logo.png", "+data")[0])
        self.assertFalse(diff_utils.is_reviewable_file("app.min.js", "+code")[0])

        # Deleted file or empty patch
        self.assertFalse(diff_utils.is_reviewable_file("old.py", "+code", status="removed")[0])
        self.assertFalse(diff_utils.is_reviewable_file("foo.py", "")[0])

        # Valid source file
        self.assertTrue(diff_utils.is_reviewable_file("src/main.py", "+import os")[0])

    def test_detect_language(self):
        self.assertEqual(diff_utils.detect_language("app.py"), "python")
        self.assertEqual(diff_utils.detect_language("script.ts"), "typescript")
        self.assertEqual(diff_utils.detect_language("main.go"), "go")
        self.assertEqual(diff_utils.detect_language("unknown.xyz"), "plaintext")


class TestPublisherAndFormatting(unittest.TestCase):
    """Verifies review comment markdown formatting and 422 fallback publishing."""

    def test_format_inline_comment_uses_normal_fences_not_suggestion(self):
        issue = ReviewIssue(
            line=15,
            severity="high",
            category="security",
            title="SQL Injection",
            explanation="Unescaped user input in raw SQL query.",
            suggested_fix="cursor.execute('SELECT * FROM tbl WHERE id = %s', (uid,))"
        )
        body = publisher.format_inline_comment_body(issue, language="python")

        # Explicit text check
        self.assertIn("**Severity: HIGH**", body)
        self.assertIn("Category: `Security`", body)
        self.assertIn("SQL Injection", body)

        # Requirement: Do NOT use GitHub ```suggestion blocks!
        self.assertNotIn("```suggestion", body)
        self.assertIn("```python", body)
        self.assertIn("cursor.execute", body)

    def test_build_review_summary_structure(self):
        file_results = [
            ("src/db.py", ReviewResult(
                summary="Needs security fix",
                issues=[
                    ReviewIssue(
                        line=12,
                        severity="high",
                        category="security",
                        title="SQLi",
                        explanation="Bad query",
                        suggested_fix=None
                    )
                ]
            ))
        ]
        skipped = [("package-lock.json", "Lockfile excluded")]
        uncommentable = [("src/db.py", ReviewIssue(
            line=None,
            severity="medium",
            category="style",
            title="File docstring missing",
            explanation="Module lacks docstring",
            suggested_fix=None
        ))]

        summary = publisher.build_review_summary(file_results, skipped, uncommentable)
        self.assertIn("AI Code Reviewer Summary", summary)
        self.assertIn("🔴 **High** | 1", summary)
        self.assertIn("📌 General & Contextual Findings", summary)
        self.assertIn("package-lock.json", summary)

    def test_publish_pr_review_fallback_on_422(self):
        mock_client = MagicMock()
        # Attempt 1 raises 422
        mock_client.create_pull_request_review.side_effect = [
            GitHubReviewCommentError("Line out of diff", status_code=422),
            {"id": 1234, "status": "published"}  # Attempt 2 (fallback) succeeds
        ]

        inline_comments = [
            {"path": "foo.py", "line": 42, "side": "RIGHT", "body": "Inline comment text"}
        ]

        result = publisher.publish_pr_review(
            client=mock_client,
            owner="owner",
            repo="repo",
            pull_number=1,
            commit_id="abc1234",
            summary_body="Summary text",
            inline_comments=inline_comments,
            dry_run=False
        )

        self.assertEqual(mock_client.create_pull_request_review.call_count, 2)
        # Second call should have comments=None and body containing fallback text
        second_call_kwargs = mock_client.create_pull_request_review.call_args_list[1][1]
        self.assertIsNone(second_call_kwargs["comments"])
        self.assertIn("Detailed Inline Comments (Fallback Display)", second_call_kwargs["body"])
        self.assertIn("foo.py", second_call_kwargs["body"])


class TestWebhookEndpoints(BaseTestCase):
    """Verifies HMAC signature verification, event filtering, and background task enqueuing."""

    def setUp(self):
        super().setUp()
        self.client = TestClient(app)
        self.secret = "test_webhook_secret_123"
        config.GITHUB_WEBHOOK_SECRET = self.secret
        service.clear_reviewed_commits_cache()

    def _sign_payload(self, body: bytes) -> str:
        digest = hmac.new(self.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return f"sha256={digest}"

    def test_signature_verification_valid_and_invalid(self):
        body = b'{"hello": "world"}'
        valid_sig = self._sign_payload(body)
        self.assertTrue(verify_signature(body, valid_sig))
        self.assertFalse(verify_signature(body, "sha256=invalid_hex_signature"))
        self.assertFalse(verify_signature(body, None))

    def test_webhook_invalid_signature_returns_401(self):
        response = self.client.post(
            "/webhooks/github",
            content=b'{"action": "opened"}',
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "sha256=bad_sig"
            }
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("Invalid or missing X-Hub-Signature-256", response.json()["detail"])

    def test_webhook_ping_event_returns_pong(self):
        body = json.dumps({"zen": "Keep it logically awesome."}).encode("utf-8")
        sig = self._sign_payload(body)

        response = self.client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "ping",
                "X-Hub-Signature-256": sig,
                "Content-Type": "application/json"
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "pong")
        self.assertEqual(response.json()["zen"], "Keep it logically awesome.")

    def test_webhook_ignores_non_pull_request_event(self):
        body = json.dumps({"ref": "refs/heads/main"}).encode("utf-8")
        sig = self._sign_payload(body)

        response = self.client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": sig,
                "Content-Type": "application/json"
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ignored")

    def test_webhook_ignores_draft_pull_request(self):
        body = json.dumps({
            "action": "opened",
            "pull_request": {
                "number": 10,
                "draft": True,
                "head": {"sha": "abc123"},
                "user": {"login": "alice", "type": "User"}
            },
            "repository": {"name": "repo", "owner": {"login": "owner"}},
            "sender": {"login": "alice", "type": "User"}
        }).encode("utf-8")
        sig = self._sign_payload(body)

        response = self.client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": sig,
                "Content-Type": "application/json"
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reason"], "Draft pull requests are skipped")

    def test_webhook_ignores_bot_authored_pull_request(self):
        body = json.dumps({
            "action": "opened",
            "pull_request": {
                "number": 11,
                "draft": False,
                "head": {"sha": "abc123"},
                "user": {"login": "dependabot[bot]", "type": "Bot"}
            },
            "repository": {"name": "repo", "owner": {"login": "owner"}},
            "sender": {"login": "dependabot[bot]", "type": "Bot"}
        }).encode("utf-8")
        sig = self._sign_payload(body)

        response = self.client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": sig,
                "Content-Type": "application/json"
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reason"], "Bot-authored pull requests are skipped")

    @patch("github_bot.service.review_pull_request")
    def test_webhook_enqueues_valid_pr(self, mock_review_task):
        body = json.dumps({
            "action": "opened",
            "number": 42,
            "pull_request": {
                "number": 42,
                "draft": False,
                "head": {"sha": "1234567890abcdef"},
                "user": {"login": "developer", "type": "User"}
            },
            "repository": {"name": "my-app", "owner": {"login": "my-org"}},
            "sender": {"login": "developer", "type": "User"},
            "installation": {"id": 9999}
        }).encode("utf-8")
        sig = self._sign_payload(body)

        response = self.client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": sig,
                "Content-Type": "application/json"
            }
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "enqueued")
        self.assertEqual(data["repo"], "my-org/my-app")
        self.assertEqual(data["pull_number"], 42)


class TestServicePipeline(BaseTestCase):
    """Verifies end-to-end service orchestration, caps, dry-run, and deduplication."""

    def setUp(self):
        super().setUp()
        service.clear_reviewed_commits_cache()

    @patch("github_bot.client.GitHubClient.get_file_content", return_value="def add(a, b):\n    return a + b\n    pass\n")
    @patch("github_bot.auth.get_installation_access_token")
    @patch("github_bot.client.GitHubClient.get_pull_request")
    @patch("github_bot.client.GitHubClient.get_pull_request_files")
    @patch("github_bot.client.GitHubClient.create_pull_request_review")
    def test_service_dry_run_simulation(
        self,
        mock_create_review,
        mock_get_files,
        mock_get_pr,
        mock_get_token,
        mock_get_file_content
    ):
        mock_get_token.return_value = "ghs_test_token"
        mock_get_pr.return_value = {
            "head": {"sha": "feedbeef1234"},
            "draft": False,
            "number": 5
        }
        mock_get_files.return_value = [
            {
                "filename": "calc.py",
                "status": "modified",
                "patch": (
                    "@@ -1,3 +1,4 @@\n"
                    " def add(a, b):\n"
                    "+    return a + b\n"
                    "     pass\n"
                )
            }
        ]

        mock_engine = MagicMock()
        mock_engine.review.return_value = ReviewResult(
            summary="Looks solid",
            issues=[
                ReviewIssue(
                    line=2,
                    severity="low",
                    category="style",
                    title="Code style",
                    explanation="Redundant pass statement below return.",
                    suggested_fix="def add(a, b):\n    return a + b"
                )
            ]
        )

        res = service.review_pull_request(
            owner="octo",
            repo="world",
            pull_number=5,
            installation_id=101,
            dry_run=True,
            engine=mock_engine
        )

        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["inline_comments_count"], 1)
        self.assertTrue(res["review"]["dry_run"])
        # In dry run, create_pull_request_review must NOT be called on GitHub
        mock_create_review.assert_not_called()

    @patch("github_bot.auth.get_installation_access_token")
    @patch("github_bot.client.GitHubClient.get_pull_request")
    def test_service_skips_already_reviewed_commit(self, mock_get_pr, mock_get_token):
        mock_get_token.return_value = "token"
        mock_get_pr.return_value = {
            "head": {"sha": "commit_already_reviewed"},
            "draft": False,
            "number": 7
        }

        service.mark_commit_reviewed("owner/repo", "commit_already_reviewed")

        res = service.review_pull_request(
            owner="owner",
            repo="repo",
            pull_number=7,
            installation_id=1,
            dry_run=False,
            force=False
        )

        self.assertEqual(res["status"], "skipped")
        self.assertEqual(res["reason"], "Commit already reviewed")


if __name__ == "__main__":
    unittest.main()

