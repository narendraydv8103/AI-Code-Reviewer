"""Test suite for Phase 3: Smarter & Less Noisy Reviews."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from analysis.models import StaticAnalysisFinding
from analysis.runner import is_safe_relative_path, run_static_analysis
from github_bot import config_loader, publisher, service
from reviewer.chunking import (
    deduplicate_issues,
    review_large_diff,
    split_patch_into_chunks,
)
from reviewer.models import ReviewIssue, ReviewResult
from reviewer.prompts import build_review_prompt
from tests.base import BaseTestCase


class TestPatchChunkingAndMerging(unittest.TestCase):
    """Verifies hunk-aware patch chunking, multi-call merging, and issue deduplication."""

    def test_split_patch_small_returns_single_chunk(self):
        patch = "@@ -1,2 +1,3 @@\n+line1\n line2\n"
        chunks = split_patch_into_chunks(patch, max_chars=1000)
        self.assertEqual(chunks, [patch])

    def test_split_patch_preserves_header_across_chunks(self):
        header = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py"
        hunk1 = "@@ -1,5 +1,6 @@\n" + "+addition1\n" * 10
        hunk2 = "@@ -20,5 +20,6 @@\n" + "+addition2\n" * 10
        patch = f"{header}\n{hunk1}\n{hunk2}"

        # Set budget so that hunk1 + hunk2 cannot fit together
        budget = len(header) + len(hunk1) + 5
        chunks = split_patch_into_chunks(patch, max_chars=budget)

        self.assertEqual(len(chunks), 2)
        self.assertTrue(chunks[0].startswith(header))
        self.assertTrue(chunks[1].startswith(header))
        self.assertIn("addition1", chunks[0])
        self.assertIn("addition2", chunks[1])

    def test_deduplicate_issues_retains_higher_severity(self):
        issues = [
            ReviewIssue(
                line=10,
                severity="low",
                category="bug",
                title="Potential Null Pointer",
                explanation="low severity explanation",
                confidence=0.6,
            ),
            ReviewIssue(
                line=10,
                severity="high",
                category="bug",
                title="Critical Null Pointer Dereference",
                explanation="high severity explanation",
                confidence=0.9,
            ),
        ]
        deduped = deduplicate_issues(issues)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].severity, "high")
        self.assertEqual(deduped[0].confidence, 0.9)

    def test_deduplicate_preserves_distinct_issues_on_same_line(self):
        issues = [
            ReviewIssue(
                line=10,
                severity="high",
                category="security",
                title="SQL Injection",
                explanation="Raw formatting in SQL query.",
            ),
            ReviewIssue(
                line=10,
                severity="medium",
                category="performance",
                title="Unindexed Full Table Scan",
                explanation="Query scans table without index.",
            ),
        ]
        deduped = deduplicate_issues(issues)
        self.assertEqual(len(deduped), 2)

    def test_review_large_diff_calls_engine_per_chunk_and_merges(self):
        header = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py"
        hunk1 = "@@ -1,5 +1,6 @@\n" + "+added_one\n" * 10
        hunk2 = "@@ -50,5 +50,6 @@\n" + "+added_two\n" * 10
        patch = f"{header}\n{hunk1}\n{hunk2}"

        mock_engine = MagicMock()
        mock_engine.review.side_effect = [
            ReviewResult(
                summary="Chunk 1 clean.",
                issues=[ReviewIssue(line=2, severity="low", category="style", title="Style Nit", explanation="nit")]
            ),
            ReviewResult(
                summary="Chunk 2 has bug.",
                issues=[ReviewIssue(line=52, severity="high", category="bug", title="Crash Bug", explanation="crash")]
            ),
        ]

        result = review_large_diff(
            engine=mock_engine,
            patch=patch,
            language="python",
            max_chunk_chars=len(header) + len(hunk1) + 5
        )

        self.assertEqual(mock_engine.review.call_count, 2)
        self.assertEqual(len(result.issues), 2)
        self.assertIn("Chunk 1 clean.", result.summary)
        self.assertIn("Chunk 2 has bug.", result.summary)


class TestConfigLoader(unittest.TestCase):
    """Verifies .reviewer.yml parsing and graceful fallback on invalid configs."""

    def test_valid_config_loading(self):
        mock_client = MagicMock()
        valid_yaml = (
            "min_severity: medium\n"
            "enabled_categories:\n"
            "  - security\n"
            "  - bug\n"
            "ignore_path_globs:\n"
            "  - 'tests/**'\n"
            "max_comments: 5\n"
            "min_confidence: 0.8\n"
            "custom_guidelines: 'Ensure all endpoints validate tokens.'\n"
        )
        mock_client.get_file_content.return_value = valid_yaml

        cfg, warning = config_loader.load_repo_config(mock_client, "org", "repo", "main")
        self.assertIsNone(warning)
        self.assertEqual(cfg.min_severity, "medium")
        self.assertEqual(cfg.enabled_categories, ["security", "bug"])
        self.assertEqual(cfg.ignore_path_globs, ["tests/**"])
        self.assertEqual(cfg.max_comments, 5)
        self.assertEqual(cfg.min_confidence, 0.8)
        self.assertIn("validate tokens", cfg.custom_guidelines or "")

    def test_missing_config_returns_defaults_silently(self):
        mock_client = MagicMock()
        mock_client.get_file_content.return_value = None

        cfg, warning = config_loader.load_repo_config(mock_client, "org", "repo", "main")
        self.assertIsNone(warning)
        self.assertEqual(cfg.min_severity, "low")
        self.assertEqual(cfg.min_confidence, 0.5)

    def test_invalid_yaml_returns_defaults_with_warning(self):
        mock_client = MagicMock()
        mock_client.get_file_content.return_value = "min_severity: [invalid: unclosed: yaml"

        cfg, warning = config_loader.load_repo_config(mock_client, "org", "repo", "main")
        self.assertIsNotNone(warning)
        self.assertIn("syntax errors", warning.lower())
        self.assertEqual(cfg.min_severity, "low")

    def test_schema_validation_error_returns_defaults_with_warning(self):
        mock_client = MagicMock()
        # Invalid min_severity value
        mock_client.get_file_content.return_value = "min_severity: ultra_critical\n"

        cfg, warning = config_loader.load_repo_config(mock_client, "org", "repo", "main")
        self.assertIsNotNone(warning)
        self.assertIn("failed schema validation", warning.lower())
        self.assertEqual(cfg.min_severity, "low")


class TestStaticAnalysisSafetyAndFiltering(unittest.TestCase):
    """Verifies path traversal defenses and changed-lines filtering for static analysis."""

    def test_is_safe_relative_path(self):
        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td)
            # Safe relative paths
            self.assertIsNotNone(is_safe_relative_path("src/app.py", base_dir))
            self.assertIsNotNone(is_safe_relative_path("main.go", base_dir))

            # Dangerous paths
            self.assertIsNone(is_safe_relative_path("/etc/passwd", base_dir))
            self.assertIsNone(is_safe_relative_path("../secret.py", base_dir))
            self.assertIsNone(is_safe_relative_path("src/../../outside.py", base_dir))

    @patch("analysis.runner.run_bandit")
    @patch("analysis.runner.run_semgrep")
    def test_run_static_analysis_filters_to_commentable_lines_only(
        self,
        mock_semgrep,
        mock_bandit
    ):
        # Mock Bandit and Semgrep findings
        mock_bandit.return_value = [
            StaticAnalysisFinding(
                tool="Bandit",
                file="src/db.py",
                line=10,  # Modified line
                rule_id="B105",
                message="Hardcoded password",
                severity="high"
            ),
            StaticAnalysisFinding(
                tool="Bandit",
                file="src/db.py",
                line=99,  # Unchanged line outside diff
                rule_id="B106",
                message="Other finding",
                severity="low"
            ),
        ]
        mock_semgrep.return_value = [
            StaticAnalysisFinding(
                tool="Semgrep",
                file="src/api.py",
                line=25,  # Modified line
                rule_id="sqli-rule",
                message="SQL Injection",
                severity="error"
            )
        ]

        files = {
            "src/db.py": "line1\nline2\n",
            "src/api.py": "api code\n",
        }
        commentable_lines_map = {
            "src/db.py": {10, 11},
            "src/api.py": {25, 26},
        }

        findings = run_static_analysis(files, commentable_lines_map)

        self.assertEqual(len(findings), 2)
        # Line 99 finding was dropped
        finding_lines = {f.line for f in findings}
        self.assertIn(10, finding_lines)
        self.assertIn(25, finding_lines)
        self.assertNotIn(99, finding_lines)


class TestPromptContextAndDelimiters(unittest.TestCase):
    """Verifies delimited injection defenses for all Phase 3 context additions."""

    def test_prompt_includes_delimited_context_blocks(self):
        prompt = build_review_prompt(
            content="print(1)",
            language="python",
            mode="code",
            pr_title="Add Auth Layer",
            pr_description="Implements JWT validation",
            surrounding_code="1 | import jwt\n2 | def auth(): pass",
            readme_excerpt="# My Project\nFastAPI service",
            static_findings=["[Bandit] src/app.py:5 (B101): Use of assert"],
            custom_guidelines="Never use assert in production.",
        )

        self.assertIn("<pr_metadata>", prompt)
        self.assertIn("Title: Add Auth Layer", prompt)
        self.assertIn("</pr_metadata>", prompt)

        self.assertIn("<readme_context>", prompt)
        self.assertIn("FastAPI service", prompt)
        self.assertIn("</readme_context>", prompt)

        self.assertIn("<surrounding_code>", prompt)
        self.assertIn("import jwt", prompt)
        self.assertIn("</surrounding_code>", prompt)

        self.assertIn("<static_analysis_hints>", prompt)
        self.assertIn("[Bandit] src/app.py:5", prompt)
        self.assertIn("</static_analysis_hints>", prompt)

        self.assertIn("<custom_guidelines>", prompt)
        self.assertIn("Never use assert in production.", prompt)
        self.assertIn("</custom_guidelines>", prompt)


class TestNoiseReductionAndCommentCapping(BaseTestCase):
    """Verifies confidence filtering, severity prioritization, and comment capping."""

    def setUp(self):
        super().setUp()

    def test_prioritize_and_cap_comments(self):
        comments = [
            (
                ReviewIssue(line=1, severity="low", category="style", title="Low Conf Nit", confidence=0.4, explanation="nit"),
                {"path": "a.py", "line": 1, "body": "Low Nit"}
            ),
            (
                ReviewIssue(line=2, severity="high", category="security", title="Critical SQLi", confidence=0.95, explanation="sqli"),
                {"path": "b.py", "line": 2, "body": "Critical SQLi"}
            ),
            (
                ReviewIssue(line=3, severity="medium", category="bug", title="Medium Bug", confidence=0.7, explanation="bug"),
                {"path": "c.py", "line": 3, "body": "Medium Bug"}
            ),
            (
                ReviewIssue(line=4, severity="high", category="bug", title="High Logic Error", confidence=0.8, explanation="logic"),
                {"path": "d.py", "line": 4, "body": "High Logic Error"}
            ),
        ]

        # Cap at 2 comments: should select the two high-severity issues (ranked by confidence)
        kept, omitted = publisher.prioritize_and_cap_comments(comments, max_comments=2)

        self.assertEqual(len(kept), 2)
        self.assertEqual(omitted, 2)
        self.assertEqual(kept[0]["path"], "b.py")  # High (conf 0.95)
        self.assertEqual(kept[1]["path"], "d.py")  # High (conf 0.80)

    @patch("github_bot.client.GitHubClient.get_file_content", return_value=None)
    @patch("github_bot.auth.get_installation_access_token")
    @patch("github_bot.client.GitHubClient.get_pull_request")
    def test_review_lock_released_on_failure(self, mock_get_pr, mock_get_token, mock_get_content):
        mock_get_token.return_value = "token"
        mock_get_pr.return_value = {
            "head": {"sha": "sha_fail_123"},
            "base": {"ref": "main"},
            "draft": False,
            "number": 99,
        }

        service.clear_reviewed_commits_cache()

        with patch("github_bot.client.GitHubClient.get_pull_request_files", side_effect=RuntimeError("API crashed")):
            with self.assertRaises(RuntimeError):
                service.review_pull_request(
                    owner="owner",
                    repo="repo",
                    pull_number=99,
                    installation_id=1,
                    dry_run=False
                )

        # Confirm lock was released so review can be retried
        self.assertFalse(service.is_commit_reviewed("owner/repo", "sha_fail_123"))


class TestDbIsolation(unittest.TestCase):
    def test_db_path_is_temporary(self):
        from app import config
        self.assertIn("ai_code_reviewer_tests_", str(config.DB_PATH))


if __name__ == "__main__":
    unittest.main()

