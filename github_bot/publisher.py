"""Formats and publishes pull request reviews and inline comments to GitHub."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from github_bot.client import GitHubClient, GitHubReviewCommentError
from reviewer.models import ReviewIssue, ReviewResult

logger = logging.getLogger(__name__)

# Severity mapping for ordering and display badges
SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1}
SEVERITY_ICONS = {
    "high": "🔴",
    "medium": "🟡",
    "low": "🔵",
}


def format_inline_comment_body(issue: ReviewIssue, language: str = "") -> str:
    """
    Formats an inline comment for GitHub.
    Uses explicit severity text, category, explanation, and a standard fenced code block.
    NOTE: Does NOT use GitHub ```suggestion blocks per safety requirement.
    """
    sev_upper = issue.severity.upper()
    sev_icon = SEVERITY_ICONS.get(issue.severity.lower(), "⚪")
    category_label = issue.category.capitalize()

    lines = [
        f"{sev_icon} **Severity: {sev_upper}** | Category: `{category_label}`",
        f"**{issue.title}**",
        "",
        issue.explanation,
    ]

    if issue.suggested_fix and issue.suggested_fix.strip():
        lang_tag = language if language and language != "plaintext" else ""
        lines.extend([
            "",
            "**Suggested fix:**",
            f"```{lang_tag}",
            issue.suggested_fix.strip(),
            "```",
        ])

    return "\n".join(lines)


def prioritize_and_cap_comments(
    comments_with_issues: List[Tuple[ReviewIssue, Dict[str, Any]]],
    max_comments: int,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Sorts comments by severity (High > Medium > Low) and confidence (descending),
    capping at max_comments. Returns (kept_comments, omitted_count).
    """
    def sort_key(item: Tuple[ReviewIssue, Dict[str, Any]]) -> Tuple[int, float]:
        issue, _ = item
        sev_rank = SEVERITY_ORDER.get(issue.severity.lower(), 1)
        conf_rank = issue.confidence if issue.confidence is not None else 0.5
        return (sev_rank, conf_rank)

    # Sort descending
    sorted_items = sorted(comments_with_issues, key=sort_key, reverse=True)
    kept = [payload for _, payload in sorted_items[:max_comments]]
    omitted_count = max(0, len(sorted_items) - max_comments)
    return kept, omitted_count


def build_review_summary(
    file_results: List[Tuple[str, ReviewResult]],
    skipped_files: List[Tuple[str, str]],
    uncommentable_issues: List[Tuple[str, ReviewIssue]],
    config_warning: Optional[str] = None,
    omitted_comments_count: int = 0,
) -> str:
    """
    Builds the top-level review markdown body containing severity totals,
    highlights, contextual issues that couldn't be commented inline, and skipped files.
    """
    total_issues = 0
    counts = {"high": 0, "medium": 0, "low": 0}

    for _, result in file_results:
        for issue in result.issues:
            total_issues += 1
            s = issue.severity.lower()
            if s in counts:
                counts[s] += 1

    lines = ["## 🤖 AI Code Reviewer Summary\n"]

    if config_warning:
        lines.append(f"> ⚠️ **Configuration Notice:** {config_warning}\n")

    if total_issues == 0:
        lines.append("✅ **All Clean!** No significant issues or bugs detected in the reviewed changes.\n")
    else:
        lines.append(f"**Found {total_issues} issue{'s' if total_issues != 1 else ''} across reviewed files:**\n")
        lines.append("| Severity | Count |")
        lines.append("| :--- | :---: |")
        lines.append(f"| 🔴 **High** | {counts['high']} |")
        lines.append(f"| 🟡 **Medium** | {counts['medium']} |")
        lines.append(f"| 🔵 **Low** | {counts['low']} |")
        lines.append("")

    if omitted_comments_count > 0:
        lines.append(f"*(Note: {omitted_comments_count} lower-priority inline comments were omitted to respect the comment limit.)*\n")

    # If there are file-level issues or issues outside diff hunks
    if uncommentable_issues:
        lines.append("### 📌 General & Contextual Findings")
        lines.append("*(Issues located outside the modified diff hunks or spanning the entire file)*\n")
        for filename, issue in uncommentable_issues:
            sev_icon = SEVERITY_ICONS.get(issue.severity.lower(), "⚪")
            line_str = f"Line {issue.line}" if issue.line is not None else "General"
            conf_str = f" ({round(issue.confidence * 100)}% conf)" if issue.confidence is not None else ""
            lines.append(
                f"- {sev_icon} **[{issue.severity.upper()}]** `{filename}` ({line_str}{conf_str}): "
                f"**{issue.title}** — {issue.explanation}"
            )
            if issue.suggested_fix:
                lines.append(f"  - *Suggested fix:* `{issue.suggested_fix.strip().splitlines()[0]}`")
        lines.append("")

    # If any files were skipped due to size, binary, lockfiles, or vendor paths
    if skipped_files:
        lines.append("### ℹ️ Skipped Files")
        lines.append("*(The following files were not analyzed according to review safety rules)*\n")
        for filename, reason in skipped_files:
            lines.append(f"- `{filename}`: {reason}")
        lines.append("")

    lines.append("---")
    lines.append("*Automated review powered by Claude 3.5 Sonnet & Antigravity*")

    return "\n".join(lines)


def publish_pr_review(
    client: GitHubClient,
    owner: str,
    repo: str,
    pull_number: int,
    commit_id: str,
    summary_body: str,
    inline_comments: List[Dict[str, Any]],
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Submits the PR review. If GitHub rejects inline comments with HTTP 422,
    automatically falls back to posting the summary with all comments included in the review body.
    """
    if dry_run:
        logger.info("[DRY-RUN] Review prepared for %s/%s #%d (commit: %s)", owner, repo, pull_number, commit_id[:7])
        return {
            "dry_run": True,
            "commit_id": commit_id,
            "body": summary_body,
            "comments": inline_comments,
            "comments_count": len(inline_comments),
        }

    try:
        # Attempt primary publish with inline comments
        return client.create_pull_request_review(
            owner=owner,
            repo=repo,
            pull_number=pull_number,
            commit_id=commit_id,
            body=summary_body,
            comments=inline_comments if inline_comments else None,
            event="COMMENT",
        )
    except GitHubReviewCommentError as err:
        logger.warning(
            "GitHub rejected inline comments (HTTP 422: %s). Initiating fallback to summary-only review.",
            err.message
        )
        # Fallback: compile all inline comments directly into markdown body
        fallback_body_lines = [
            summary_body,
            "",
            "### 📝 Detailed Inline Comments (Fallback Display)",
            "*(GitHub rejected line-specific inline comments; all findings are listed below)*\n"
        ]
        for c in inline_comments:
            fallback_body_lines.append(f"#### `{c['path']}` (Line {c['line']})")
            fallback_body_lines.append(c["body"])
            fallback_body_lines.append("")

        fallback_body = "\n".join(fallback_body_lines)
        return client.create_pull_request_review(
            owner=owner,
            repo=repo,
            pull_number=pull_number,
            commit_id=commit_id,
            body=fallback_body,
            comments=None,
            event="COMMENT",
        )

