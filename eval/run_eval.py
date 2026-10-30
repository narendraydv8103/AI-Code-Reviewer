#!/usr/bin/env python3
"""AI Code Reviewer - Automated Quality Evaluation Harness.

Runs reproducible evaluations of the review engine across labeled benchmark
cases (offline with deterministic mock, or online with Anthropic Claude).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import config
from reviewer.engine import ReviewEngine
from reviewer.models import ReviewIssue, ReviewResult

SEVERITY_RANKS = {"low": 1, "medium": 2, "high": 3}


class MockReviewEngine:
    """Deterministic mock reviewer used for offline testing and CI harness verification."""

    def __init__(self, model: str = "mock-claude"):
        self.model = model

    def review(
        self,
        code: str,
        language: str = "plaintext",
        mode: str = "code",
        **kwargs: Any
    ) -> ReviewResult:
        time.sleep(0.005)  # Simulate minimal processing latency
        issues: List[ReviewIssue] = []

        # 1. SQL Injection
        if "SELECT id, name FROM users" in code or "SELECT id, username, email FROM accounts" in code:
            issues.append(
                ReviewIssue(
                    id="mock-sqli",
                    line=5,
                    severity="high",
                    category="security",
                    title="SQL Injection Vulnerability",
                    explanation="Unescaped user input concatenated into raw SQL statement.",
                    suggested_fix="cursor.execute('SELECT id, name FROM users WHERE username = ?', (user_input,))",
                    confidence=0.98,
                )
            )

        # 2. Hardcoded Secret
        elif "sk-ant-" in code or "API_KEY =" in code:
            issues.append(
                ReviewIssue(
                    id="mock-secret",
                    line=3,
                    severity="high",
                    category="security",
                    title="Hardcoded API Credential",
                    explanation="Secret API key committed in plaintext source code.",
                    suggested_fix="API_KEY = os.environ.get('API_KEY')",
                    confidence=0.99,
                )
            )

        # 3. Off-by-one Loop
        elif "range(len(items) + 1)" in code:
            issues.append(
                ReviewIssue(
                    id="mock-off-by-one",
                    line=4,
                    severity="medium",
                    category="bug",
                    title="Off-by-One IndexError",
                    explanation="Loop iterates beyond the valid bounds of the items collection.",
                    suggested_fix="for i in range(len(items)):",
                    confidence=0.93,
                )
            )

        # 4. N+1 Performance Issue
        elif "for uid in user_ids:" in code and "SELECT * FROM orders" in code:
            issues.append(
                ReviewIssue(
                    id="mock-n-plus-one",
                    line=8,
                    severity="medium",
                    category="performance",
                    title="N+1 Query in Loop",
                    explanation="Database query executed iteratively inside a loop causing high latency.",
                    suggested_fix="SELECT * FROM orders WHERE user_id IN (...)",
                    confidence=0.90,
                )
            )

        # 5. Missing Error Handling
        elif "json.loads(config_str)" in code:
            issues.append(
                ReviewIssue(
                    id="mock-unhandled-json",
                    line=5,
                    severity="low",
                    category="bug",
                    title="Missing Error Handling",
                    explanation="json.loads and dictionary lookup without exception handling.",
                    suggested_fix="try:\n    data = json.loads(config_str)\nexcept json.JSONDecodeError:\n    ...",
                    confidence=0.86,
                )
            )

        # 6. Unused Variable
        elif "unused_discount" in code:
            issues.append(
                ReviewIssue(
                    id="mock-unused-var",
                    line=3,
                    severity="low",
                    category="style",
                    title="Unused Local Variable",
                    explanation="Variable 'unused_discount' is assigned but never read.",
                    suggested_fix="Remove unused variable.",
                    confidence=0.82,
                )
            )

        # 7. Missing Tests
        elif "FinancialLedger" in code:
            issues.append(
                ReviewIssue(
                    id="mock-missing-tests",
                    line=3,
                    severity="low",
                    category="tests",
                    title="Missing Automated Unit Tests",
                    explanation="Sensitive fund transfer logic requires unit and regression tests.",
                    suggested_fix="Add test_transfer_funds_insufficient_balance()",
                    confidence=0.84,
                )
            )

        # 8. Prompt Injection with Command Injection
        elif "ignore previous instructions" in code:
            issues.append(
                ReviewIssue(
                    id="mock-prompt-injection",
                    line=6,
                    severity="high",
                    category="security",
                    title="Command Injection Vulnerability",
                    explanation="Untrusted input passed directly to os.system.",
                    suggested_fix="subprocess.run(['command', user_arg], check=True)",
                    confidence=0.97,
                )
            )

        # 9. Path Traversal
        elif "/var/data/{filename}" in code:
            issues.append(
                ReviewIssue(
                    id="mock-path-traversal",
                    line=5,
                    severity="high",
                    category="security",
                    title="Path Traversal Vulnerability",
                    explanation="User filename concatenated into file path without directory traversal sanitization.",
                    suggested_fix="Path(base).resolve() and verify safe prefix.",
                    confidence=0.96,
                )
            )

        # 10. Resource Leak
        elif "open(filename, \"w\")" in code and "with open" not in code:
            issues.append(
                ReviewIssue(
                    id="mock-resource-leak",
                    line=3,
                    severity="low",
                    category="bug",
                    title="Unclosed File Resource Leak",
                    explanation="File opened without context manager; descriptor may leak.",
                    suggested_fix="with open(filename, 'w') as f:\n    f.write(data)",
                    confidence=0.89,
                )
            )

        # 11. Diff Bug: Auth Bypass
        elif "diff --git a/services/auth.py" in code:
            issues.append(
                ReviewIssue(
                    id="mock-diff-auth-bypass",
                    line=20,
                    severity="high",
                    category="security",
                    title="Broken Access Control",
                    explanation="Permission check inverted, granting unauthorized access.",
                    suggested_fix="Explicitly whitelist authorized admin roles.",
                    confidence=0.94,
                )
            )

        # Clean cases return zero issues
        summary = (
            f"Review completed. Found {len(issues)} issue(s)."
            if issues
            else "Clean review: no code defects, bugs, or security vulnerabilities found."
        )
        return ReviewResult(summary=summary, issues=issues)


def load_eval_cases(cases_dir: Path) -> List[Dict[str, Any]]:
    """Loads and sorts all evaluation case JSON files from cases_dir."""
    files = sorted(cases_dir.glob("*.json"))
    cases: List[Dict[str, Any]] = []
    for f in files:
        with open(f, "r", encoding="utf-8") as fp:
            try:
                data = json.load(fp)
                cases.append(data)
            except Exception as e:
                print(f"Warning: Failed to load case file {f.name}: {e}", file=sys.stderr)
    return cases


def match_issues(
    expected_list: List[Dict[str, Any]],
    returned_issues: List[ReviewIssue],
    line_tolerance: int = 3
) -> Tuple[int, int, int]:
    """
    Matches returned issues against expected issues.

    Matching rule:
    - Same category (case-insensitive)
    - Line number within +/- line_tolerance lines
    - Severity >= expected min_severity

    Returns:
    (true_positives, false_positives, false_negatives)
    """
    matched_returned_indices = set()
    tp = 0

    for expected in expected_list:
        exp_cat = expected.get("category", "").lower()
        exp_line = expected.get("line")
        min_sev = expected.get("min_severity", "low").lower()
        min_rank = SEVERITY_RANKS.get(min_sev, 1)

        matched = False
        for idx, ret in enumerate(returned_issues):
            if idx in matched_returned_indices:
                continue

            ret_cat = (ret.category or "").lower()
            if ret_cat != exp_cat:
                continue

            ret_rank = SEVERITY_RANKS.get((ret.severity or "low").lower(), 1)
            if ret_rank < min_rank:
                continue

            # Check line match
            if exp_line is not None:
                if ret.line is not None and abs(ret.line - exp_line) <= line_tolerance:
                    matched = True
                    matched_returned_indices.add(idx)
                    tp += 1
                    break
            else:
                matched = True
                matched_returned_indices.add(idx)
                tp += 1
                break

    fn = len(expected_list) - tp
    fp = len(returned_issues) - len(matched_returned_indices)
    return tp, fp, fn


def run_evaluation(
    cases: List[Dict[str, Any]],
    engine: Any,
) -> Dict[str, Any]:
    """Runs the evaluation harness across all benchmark cases."""
    results: List[Dict[str, Any]] = []
    total_tp = 0
    total_fp = 0
    total_fn = 0
    clean_cases_count = 0
    clean_cases_with_fp = 0
    total_duration_ms = 0

    for case in cases:
        cid = case.get("id", "unknown")
        code = case.get("input", "")
        language = case.get("language", "plaintext")
        mode = case.get("mode", "code")
        expected = case.get("expected", [])

        start_time = time.perf_counter()
        try:
            review_res = engine.review(code=code, language=language, mode=mode)
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            returned_issues = review_res.issues
        except Exception as e:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            returned_issues = []
            print(f"Error reviewing case '{cid}': {e}", file=sys.stderr)

        total_duration_ms += duration_ms

        tp, fp, fn = match_issues(expected, returned_issues, line_tolerance=3)
        total_tp += tp
        total_fp += fp
        total_fn += fn

        is_clean_case = len(expected) == 0
        if is_clean_case:
            clean_cases_count += 1
            if fp > 0:
                clean_cases_with_fp += 1

        status = "PASS" if (fn == 0 and fp == 0) else ("WARN" if fn == 0 else "FAIL")

        results.append({
            "id": cid,
            "mode": mode,
            "language": language,
            "expected_count": len(expected),
            "returned_count": len(returned_issues),
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "duration_ms": duration_ms,
            "status": status,
        })

    # Summary metrics
    precision = (total_tp / (total_tp + total_fp)) if (total_tp + total_fp) > 0 else 1.0
    recall = (total_tp / (total_tp + total_fn)) if (total_tp + total_fn) > 0 else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    fp_rate_clean = (clean_cases_with_fp / clean_cases_count) if clean_cases_count > 0 else 0.0
    avg_latency = (total_duration_ms / len(cases)) if cases else 0.0

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_cases": len(cases),
        "clean_cases": clean_cases_count,
        "clean_cases_with_fp": clean_cases_with_fp,
        "clean_fp_rate": round(fp_rate_clean, 4),
        "total_tp": total_tp,
        "total_fp": total_fp,
        "total_fn": total_fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "avg_latency_ms": round(avg_latency, 1),
        "case_results": results,
    }


def format_markdown_table(eval_summary: Dict[str, Any]) -> str:
    """Formats evaluation results as a clean Markdown table with metrics summary."""
    lines: List[str] = []
    lines.append("# AI Code Reviewer - Quality Evaluation Report\n")
    lines.append(f"**Run Timestamp:** `{eval_summary['timestamp']}`  ")
    lines.append(f"**Total Benchmark Cases:** `{eval_summary['total_cases']}`\n")

    lines.append("| Case ID | Mode | Language | Expected | Returned | TP | FP | FN | Latency | Status |")
    lines.append("|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|---:|:---:|")

    for r in eval_summary["case_results"]:
        lines.append(
            f"| `{r['id']}` | {r['mode']} | {r['language']} | {r['expected_count']} | "
            f"{r['returned_count']} | {r['tp']} | {r['fp']} | {r['fn']} | {r['duration_ms']} ms | **{r['status']}** |"
        )

    lines.append("\n## Overall Metrics Summary\n")
    lines.append(f"- **Precision:** `{eval_summary['precision']:.2%}` ({eval_summary['total_tp']}/{eval_summary['total_tp'] + eval_summary['total_fp']})")
    lines.append(f"- **Recall:** `{eval_summary['recall']:.2%}` ({eval_summary['total_tp']}/{eval_summary['total_tp'] + eval_summary['total_fn']})")
    lines.append(f"- **F1 Score:** `{eval_summary['f1']:.2%}`")
    lines.append(f"- **False-Positive Rate (Clean Cases):** `{eval_summary['clean_fp_rate']:.2%}` ({eval_summary['clean_cases_with_fp']}/{eval_summary['clean_cases']})")
    lines.append(f"- **Average Processing Latency:** `{eval_summary['avg_latency_ms']} ms`\n")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Code Reviewer Quality Evaluation Harness")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use deterministic offline mock engine (requires no network or Anthropic API key)",
    )
    parser.add_argument(
        "--cases-dir",
        type=str,
        default=str(PROJECT_ROOT / "eval" / "cases"),
        help="Path to evaluation test cases directory",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(PROJECT_ROOT / "eval_results"),
        help="Directory to store evaluation outputs",
    )
    args = parser.parse_args()

    cases_dir = Path(args.cases_dir)
    if not cases_dir.is_dir():
        print(f"Error: Cases directory '{cases_dir}' does not exist.", file=sys.stderr)
        return 1

    cases = load_eval_cases(cases_dir)
    if not cases:
        print(f"Error: No JSON test cases found in '{cases_dir}'.", file=sys.stderr)
        return 1

    if args.mock:
        engine = MockReviewEngine()
    else:
        api_key = config.ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key or not api_key.strip():
            print(
                "\n[ERROR] Missing ANTHROPIC_API_KEY.\n"
                "Real evaluation runs require an Anthropic API key in your .env or environment.\n"
                "To run the offline evaluation harness with no API key, execute with --mock:\n\n"
                "    python eval/run_eval.py --mock\n",
                file=sys.stderr
            )
            return 1
        engine = ReviewEngine(api_key=api_key.strip())

    summary = run_evaluation(cases, engine)

    # Print Markdown table to stdout
    print(format_markdown_table(summary))

    # Save JSON results to eval_results/latest.json
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    latest_file = output_dir / "latest.json"

    with open(latest_file, "w", encoding="utf-8") as fp:
        json.dump(summary, fp, indent=2)

    print(f"Results saved to {latest_file.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
