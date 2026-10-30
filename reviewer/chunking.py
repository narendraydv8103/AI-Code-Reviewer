"""Diff patch chunking, multi-call merging, and issue deduplication."""
from __future__ import annotations

import logging
import re
import time
from typing import List, Optional, Tuple

from app import config
from reviewer.engine import ReviewEngine
from reviewer.models import ReviewIssue, ReviewResult

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_BUDGET_CHARS = 12000

SEVERITY_WEIGHT = {
    "high": 3,
    "medium": 2,
    "low": 1,
}


def split_patch_into_chunks(patch: str, max_chars: int = DEFAULT_CHUNK_BUDGET_CHARS) -> List[str]:
    """
    Splits a unified diff patch into chunks along @@ hunk boundaries within a character budget.
    Preserves the file/diff header across all chunks so the LLM retains file context.
    """
    if len(patch) <= max_chars:
        return [patch]

    lines = patch.splitlines()
    header_lines: List[str] = []
    hunks: List[List[str]] = []
    current_hunk: List[str] = []
    in_hunks = False

    hunk_header_re = re.compile(r"^@@\s+-[0-9]+(?:,[0-9]+)?\s+\+[0-9]+(?:,[0-9]+)?\s+@@")

    for line in lines:
        if hunk_header_re.match(line):
            in_hunks = True
            if current_hunk:
                hunks.append(current_hunk)
                current_hunk = []
            current_hunk.append(line)
        elif in_hunks:
            current_hunk.append(line)
        else:
            header_lines.append(line)

    if current_hunk:
        hunks.append(current_hunk)

    # If no @@ hunks were found, return the patch as-is
    if not hunks:
        return [patch]

    header_text = "\n".join(header_lines)
    header_cost = len(header_text) + 1 if header_text else 0

    chunks: List[str] = []
    current_chunk_hunks: List[str] = []
    current_len = header_cost

    for hunk in hunks:
        hunk_str = "\n".join(hunk)
        hunk_len = len(hunk_str) + 1

        if current_chunk_hunks and (current_len + hunk_len > max_chars):
            # Save accumulated chunk
            chunk_body = "\n".join(current_chunk_hunks)
            full_chunk = f"{header_text}\n{chunk_body}" if header_text else chunk_body
            chunks.append(full_chunk)
            current_chunk_hunks = [hunk_str]
            current_len = header_cost + hunk_len
        else:
            current_chunk_hunks.append(hunk_str)
            current_len += hunk_len

    if current_chunk_hunks:
        chunk_body = "\n".join(current_chunk_hunks)
        full_chunk = f"{header_text}\n{chunk_body}" if header_text else chunk_body
        chunks.append(full_chunk)

    return chunks


def _normalize_title(title: str) -> str:
    """Normalizes title string for duplicate comparison."""
    return re.sub(r"[^a-z0-9]", "", title.lower())


def _is_near_duplicate_title(t1: str, t2: str) -> bool:
    """Determines whether two issue titles are near-identical."""
    n1 = _normalize_title(t1)
    n2 = _normalize_title(t2)
    if n1 == n2 or n1 in n2 or n2 in n1:
        return True

    words1 = set(re.findall(r"\w+", t1.lower()))
    words2 = set(re.findall(r"\w+", t2.lower()))
    if not words1 or not words2:
        return False
    intersection = len(words1 & words2)
    union = len(words1 | words2)
    return (intersection / union) >= 0.65


def deduplicate_issues(issues: List[ReviewIssue]) -> List[ReviewIssue]:
    """
    Deduplicates review issues:
    - Same line + same category
    - Same line + near-identical titles
    - Both line=None + same category + near-identical titles

    Retains the issue with higher severity, then higher confidence.
    """
    if len(issues) <= 1:
        return issues

    def issue_rank(item: ReviewIssue) -> Tuple[int, float]:
        sev = SEVERITY_WEIGHT.get(item.severity.lower(), 1)
        conf = item.confidence if item.confidence is not None else 0.5
        return (sev, conf)

    unique_issues: List[ReviewIssue] = []

    for candidate in issues:
        duplicate_idx = -1
        for i, existing in enumerate(unique_issues):
            # Check line match
            same_line = (candidate.line is not None and candidate.line == existing.line)
            both_null_line = (candidate.line is None and existing.line is None)

            if same_line and (candidate.category == existing.category or _is_near_duplicate_title(candidate.title, existing.title)):
                duplicate_idx = i
                break
            elif both_null_line and candidate.category == existing.category and _is_near_duplicate_title(candidate.title, existing.title):
                duplicate_idx = i
                break

        if duplicate_idx != -1:
            existing = unique_issues[duplicate_idx]
            if issue_rank(candidate) > issue_rank(existing):
                unique_issues[duplicate_idx] = candidate
        else:
            unique_issues.append(candidate)

    return unique_issues


def review_large_diff(
    engine: ReviewEngine,
    patch: str,
    language: str = "plaintext",
    max_chunk_chars: int = DEFAULT_CHUNK_BUDGET_CHARS,
    pr_title: Optional[str] = None,
    pr_description: Optional[str] = None,
    surrounding_code: Optional[str] = None,
    readme_excerpt: Optional[str] = None,
    static_findings: Optional[List[str]] = None,
    custom_guidelines: Optional[str] = None,
) -> ReviewResult:
    """
    Reviews a diff patch, automatically chunking by hunks if size exceeds budget,
    and deduplicating returned issues into a unified ReviewResult.
    """
    chunks = split_patch_into_chunks(patch, max_chars=max_chunk_chars)
    if len(chunks) == 1:
        return engine.review(
            code=chunks[0],
            language=language,
            mode="diff",
            pr_title=pr_title,
            pr_description=pr_description,
            surrounding_code=surrounding_code,
            readme_excerpt=readme_excerpt,
            static_findings=static_findings,
            custom_guidelines=custom_guidelines,
        )

    logger.info("Diff exceeded chunk budget (%d chars); split into %d chunks.", len(patch), len(chunks))

    all_issues: List[ReviewIssue] = []
    summaries: List[str] = []

    for idx, chunk in enumerate(chunks, start=1):
        try:
            logger.info("Reviewing patch chunk %d/%d (%d chars)", idx, len(chunks), len(chunk))
            chunk_result = engine.review(
                code=chunk,
                language=language,
                mode="diff",
                pr_title=pr_title,
                pr_description=pr_description,
                surrounding_code=surrounding_code,
                readme_excerpt=readme_excerpt,
                static_findings=static_findings,
                custom_guidelines=custom_guidelines,
            )
            all_issues.extend(chunk_result.issues)
            if chunk_result.summary and chunk_result.summary not in summaries:
                summaries.append(chunk_result.summary)

            if idx < len(chunks) and config.LLM_MIN_INTERVAL_SECONDS > 0:
                time.sleep(config.LLM_MIN_INTERVAL_SECONDS)
        except Exception as chunk_err:
            logger.warning("Error reviewing chunk %d/%d: %s", idx, len(chunks), chunk_err)

    deduped = deduplicate_issues(all_issues)
    combined_summary = " ".join(summaries) if summaries else "Review completed across diff chunks."

    return ReviewResult(
        summary=combined_summary,
        issues=deduped
    )

