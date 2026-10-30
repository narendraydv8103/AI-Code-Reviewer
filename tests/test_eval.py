"""Tests for Stage 3: Quality Evaluation Harness."""
import tempfile
import unittest
from pathlib import Path

from eval.run_eval import (
    MockReviewEngine,
    load_eval_cases,
    match_issues,
    run_evaluation,
)
from reviewer.models import ReviewIssue, ReviewResult
from tests.base import BaseTestCase


class TestEvaluationMatchingAndMetrics(BaseTestCase):
    """Verifies issue matching rules, severity filtering, and metrics calculation."""

    def test_exact_match_returns_true_positive(self):
        expected = [{
            "category": "security",
            "line": 10,
            "min_severity": "high",
            "title": "SQLi"
        }]
        returned = [
            ReviewIssue(
                line=10,
                severity="high",
                category="security",
                title="SQL Injection",
                explanation="Raw formatting."
            )
        ]
        tp, fp, fn = match_issues(expected, returned)
        self.assertEqual(tp, 1)
        self.assertEqual(fp, 0)
        self.assertEqual(fn, 0)

    def test_line_within_tolerance_matches(self):
        expected = [{
            "category": "bug",
            "line": 20,
            "min_severity": "medium",
            "title": "Off-by-one"
        }]
        # Within +/- 3 lines (line 22 is +2)
        returned = [
            ReviewIssue(
                line=22,
                severity="medium",
                category="bug",
                title="Off-by-one",
                explanation="Index error."
            )
        ]
        tp, fp, fn = match_issues(expected, returned, line_tolerance=3)
        self.assertEqual(tp, 1)
        self.assertEqual(fp, 0)
        self.assertEqual(fn, 0)

    def test_line_outside_tolerance_counts_as_false_positive_and_negative(self):
        expected = [{
            "category": "bug",
            "line": 20,
            "min_severity": "medium",
            "title": "Off-by-one"
        }]
        # 5 lines away (outside tolerance of 3)
        returned = [
            ReviewIssue(
                line=25,
                severity="medium",
                category="bug",
                title="Off-by-one",
                explanation="Index error."
            )
        ]
        tp, fp, fn = match_issues(expected, returned, line_tolerance=3)
        self.assertEqual(tp, 0)
        self.assertEqual(fp, 1)
        self.assertEqual(fn, 1)

    def test_category_mismatch_does_not_match(self):
        expected = [{
            "category": "security",
            "line": 10,
            "min_severity": "high"
        }]
        returned = [
            ReviewIssue(
                line=10,
                severity="high",
                category="style",
                title="Naming",
                explanation="Style nit."
            )
        ]
        tp, fp, fn = match_issues(expected, returned)
        self.assertEqual(tp, 0)
        self.assertEqual(fp, 1)
        self.assertEqual(fn, 1)

    def test_severity_below_minimum_does_not_match(self):
        expected = [{
            "category": "security",
            "line": 10,
            "min_severity": "high"
        }]
        # Severity is low, expected min_severity is high
        returned = [
            ReviewIssue(
                line=10,
                severity="low",
                category="security",
                title="Minor security remark",
                explanation="Info only."
            )
        ]
        tp, fp, fn = match_issues(expected, returned)
        self.assertEqual(tp, 0)
        self.assertEqual(fp, 1)
        self.assertEqual(fn, 1)

    def test_clean_case_returns_zero_fp_when_no_issues(self):
        expected = []
        returned = []
        tp, fp, fn = match_issues(expected, returned)
        self.assertEqual(tp, 0)
        self.assertEqual(fp, 0)
        self.assertEqual(fn, 0)

    def test_clean_case_returns_fp_when_issues_returned(self):
        expected = []
        returned = [
            ReviewIssue(
                line=5,
                severity="low",
                category="style",
                title="False positive",
                explanation="Should not be flagged."
            )
        ]
        tp, fp, fn = match_issues(expected, returned)
        self.assertEqual(tp, 0)
        self.assertEqual(fp, 1)
        self.assertEqual(fn, 0)


class TestMockEvaluationPipeline(BaseTestCase):
    """Verifies end-to-end evaluation pipeline with MockReviewEngine."""

    def test_run_eval_with_mock_engine(self):
        cases_dir = Path(__file__).resolve().parent.parent / "eval" / "cases"
        cases = load_eval_cases(cases_dir)
        self.assertGreaterEqual(len(cases), 12)

        engine = MockReviewEngine()
        summary = run_evaluation(cases, engine)

        self.assertEqual(summary["total_cases"], len(cases))
        self.assertGreaterEqual(summary["clean_cases"], 3)
        self.assertEqual(summary["clean_cases_with_fp"], 0)
        self.assertEqual(summary["clean_fp_rate"], 0.0)
        self.assertEqual(summary["precision"], 1.0)
        self.assertEqual(summary["recall"], 1.0)
        self.assertEqual(summary["f1"], 1.0)
        self.assertGreaterEqual(summary["avg_latency_ms"], 0.0)


if __name__ == "__main__":
    unittest.main()
