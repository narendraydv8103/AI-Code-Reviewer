"""Core orchestration service for GitHub Pull Request automated code reviews."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from analysis.runner import run_static_analysis
from app import config, database
from github_bot import auth, config_loader, diff_utils, publisher
from github_bot.client import GitHubClient
from reviewer.chunking import review_large_diff
from reviewer.engine import ReviewEngine
from reviewer.models import ReviewIssue, ReviewResult

logger = logging.getLogger(__name__)

# Severity hierarchy for filtering
SEVERITY_LEVELS = {
    "low": 1,
    "medium": 2,
    "high": 3,
}

SECURITY_KEYWORDS = (
    "auth", "sec", "crypto", "token", "passw", "cred", "login",
    "pay", "perm", "admin", "sql", "db", "api", "oauth"
)

# In-memory deduplication set: stores (owner_repo, head_sha)
_REVIEWED_COMMITS: Set[Tuple[str, str]] = set()


def is_commit_reviewed(owner_repo: str, head_sha: str) -> bool:
    """Checks whether the given commit SHA has already been reviewed or is in progress."""
    return (owner_repo.lower(), head_sha.lower()) in _REVIEWED_COMMITS


def mark_commit_reviewed(owner_repo: str, head_sha: str) -> None:
    """Records that a commit SHA is reviewed or under review."""
    _REVIEWED_COMMITS.add((owner_repo.lower(), head_sha.lower()))


def release_commit_reviewed(owner_repo: str, head_sha: str) -> None:
    """Releases the commit review lock if review execution fails."""
    _REVIEWED_COMMITS.discard((owner_repo.lower(), head_sha.lower()))


def clear_reviewed_commits_cache() -> None:
    """Clears the deduplication cache (useful in tests)."""
    _REVIEWED_COMMITS.clear()


def is_severity_accepted(issue_severity: str, min_severity: str) -> bool:
    """Returns True if the issue severity is equal to or greater than the min_severity threshold."""
    issue_val = SEVERITY_LEVELS.get(issue_severity.lower(), 1)
    min_val = SEVERITY_LEVELS.get(min_severity.lower(), 1)
    return issue_val >= min_val


def compute_file_risk(filename: str, additions: int, deletions: int) -> Tuple[int, int]:
    """
    Computes a sorting risk score (is_security_keyword, total_lines_changed).
    Higher risk files are prioritized when LLM call budgets are constrained.
    """
    name_lower = filename.lower()
    is_sec = 1 if any(kw in name_lower for kw in SECURITY_KEYWORDS) else 0
    return (is_sec, additions + deletions)


def review_pull_request(
    owner: str,
    repo: str,
    pull_number: int,
    installation_id: Optional[int] = None,
    dry_run: bool = False,
    force: bool = False,
    engine: Optional[ReviewEngine] = None,
) -> Dict[str, Any]:
    """
    Executes the full automated review pipeline for a GitHub Pull Request:
    1. Authenticates as GitHub App installation.
    2. Fetches PR and verifies it is not draft.
    3. Acquires commit lock immediately to prevent duplicate runs (released on failure).
    4. Fetches .reviewer.yml from the BASE branch with validation fallback.
    5. Gathers context: PR title, body, base README, and surrounding code at HEAD.
    6. Runs safe static analysis (Bandit & Semgrep) in an isolated tempdir.
    7. Evaluates diffs with chunking, respecting MAX_LLM_CALLS_PER_PR budget.
    8. Prioritizes and filters issues by confidence, category, and severity.
    9. Submits single review with inline comments and summary fallback.
    """
    owner_repo = f"{owner}/{repo}"

    # Check daily review quota
    if config.MAX_REVIEWS_PER_DAY > 0:
        reviews_today = database.get_reviews_count_today()
        if reviews_today >= config.MAX_REVIEWS_PER_DAY:
            logger.warning("Daily review quota reached (%d / %d reviews today)", reviews_today, config.MAX_REVIEWS_PER_DAY)
            return {
                "status": "skipped",
                "reason": f"Daily review quota reached ({config.MAX_REVIEWS_PER_DAY} reviews/day)",
            }

    start_time = time.time()

    # 1. Obtain installation token
    if installation_id is None:
        logger.info("Resolving installation ID for repository '%s'", owner_repo)
        installation_id = auth.get_repo_installation_id(owner, repo)

    token = auth.get_installation_access_token(installation_id)
    review_engine = engine or ReviewEngine()

    head_sha: Optional[str] = None
    lock_acquired = False

    try:
        with GitHubClient(token=token) as client:
            # 2. Fetch PR metadata
            pr = client.get_pull_request(owner, repo, pull_number)
            head_sha = pr.get("head", {}).get("sha", "")
            base_ref = pr.get("base", {}).get("ref", "main") if pr.get("base") else "main"
            pr_title = pr.get("title", "")
            pr_body = pr.get("body", "")

            # Head repo details (handling fork PRs)
            head_repo = pr.get("head", {}).get("repo")
            head_repo_owner = head_repo["owner"]["login"] if head_repo and head_repo.get("owner") else owner
            head_repo_name = head_repo["name"] if head_repo and head_repo.get("name") else repo

            if pr.get("draft") is True:
                logger.info("Skipping PR #%d on %s: marked as draft", pull_number, owner_repo)
                return {"status": "skipped", "reason": "Draft PR"}

            if not force and is_commit_reviewed(owner_repo, head_sha):
                logger.info(
                    "Skipping PR #%d on %s: head commit %s was already reviewed or is in progress",
                    pull_number, owner_repo, head_sha[:7]
                )
                return {"status": "skipped", "reason": "Commit already reviewed", "head_sha": head_sha}

            # 3. Acquire commit lock immediately when review starts
            if not dry_run:
                mark_commit_reviewed(owner_repo, head_sha)
                lock_acquired = True

            # 4. Load .reviewer.yml from BASE branch (never PR branch)
            repo_config, config_warning = config_loader.load_repo_config(
                client=client,
                owner=owner,
                repo=repo,
                base_ref=base_ref,
            )

            # 5. Fetch README excerpt from BASE branch (size-capped to 1500 chars)
            readme_excerpt: Optional[str] = None
            try:
                base_readme = client.get_file_content(owner, repo, "README.md", ref=base_ref)
                if base_readme:
                    readme_excerpt = base_readme[:1500]
            except Exception as e:
                logger.debug("Could not fetch base README.md for context: %s", e)

            # 6. Fetch changed files
            raw_files = client.get_pull_request_files(owner, repo, pull_number)
            logger.info("PR #%d on %s has %d changed files", pull_number, owner_repo, len(raw_files))

            eligible_files: List[Dict[str, Any]] = []
            skipped_files: List[Tuple[str, str]] = []
            total_diff_chars = 0

            for f in raw_files:
                filename = f.get("filename", "")
                patch = f.get("patch", "")
                status = f.get("status", "")

                # Path glob exclusions from .reviewer.yml
                if diff_utils.matches_any_glob(filename, repo_config.ignore_path_globs):
                    skipped_files.append((filename, "Ignored by path glob in .reviewer.yml"))
                    continue

                # Filter unreviewable files (lockfiles, vendor, binaries, deletions)
                eligible, skip_reason = diff_utils.is_reviewable_file(filename, patch, status)
                if not eligible:
                    skipped_files.append((filename, skip_reason or "Excluded by rule"))
                    continue

                # Check max file count cap
                if len(eligible_files) >= config.MAX_FILES:
                    skipped_files.append((filename, f"Exceeded MAX_FILES cap ({config.MAX_FILES})"))
                    continue

                # Check max diff size cap
                patch_len = len(patch)
                if total_diff_chars + patch_len > config.MAX_DIFF_CHARS:
                    skipped_files.append((filename, f"Exceeded MAX_DIFF_CHARS cap ({config.MAX_DIFF_CHARS:,})"))
                    continue

                total_diff_chars += patch_len
                eligible_files.append(f)

            # 7. Fetch full file contents at HEAD for surrounding code and static analysis
            # Fork PR safeguard: if fetching head file fails, fall back to patch-only
            files_content_map: Dict[str, str] = {}
            commentable_lines_map: Dict[str, Set[int]] = {}

            for f in eligible_files:
                fn = f["filename"]
                patch = f["patch"]
                commentable_lines_map[fn] = diff_utils.get_commentable_lines(patch)

                try:
                    head_file_content = client.get_file_content(
                        owner=head_repo_owner,
                        repo=head_repo_name,
                        path=fn,
                        ref=head_sha
                    )
                    if head_file_content:
                        files_content_map[fn] = head_file_content
                except Exception as fetch_err:
                    logger.warning("Could not fetch head file content for %s: %s; falling back to patch-only.", fn, fetch_err)

            # 8. Run safe static analysis (Bandit & Semgrep) on modified lines
            static_hints_by_file: Dict[str, List[str]] = {}
            try:
                static_findings = run_static_analysis(
                    files=files_content_map,
                    commentable_lines_map=commentable_lines_map,
                )
                for finding in static_findings:
                    static_hints_by_file.setdefault(finding.file, []).append(finding.to_hint_string())
                if static_findings:
                    logger.info("Generated %d static analysis hints across modified lines.", len(static_findings))
            except Exception as sa_err:
                logger.warning("Static analysis runner encountered an error: %s; continuing without hints.", sa_err)

            # 9. Prioritize files by risk score (security-sensitive paths, larger changes first)
            eligible_files.sort(
                key=lambda item: compute_file_risk(
                    item["filename"],
                    item.get("additions", 0),
                    item.get("deletions", 0)
                ),
                reverse=True
            )

            # 10. Review each file with chunking and MAX_LLM_CALLS_PER_PR budget
            file_results: List[Tuple[str, ReviewResult]] = []
            comments_with_issues: List[Tuple[ReviewIssue, Dict[str, Any]]] = []
            uncommentable_issues: List[Tuple[str, ReviewIssue]] = []

            max_llm_calls = config.MAX_LLM_CALLS_PER_PR
            llm_calls_made = 0

            for f in eligible_files:
                filename = f["filename"]
                patch = f["patch"]
                lang = diff_utils.detect_language(filename)
                commentable_lines = commentable_lines_map.get(filename, set())

                # Surrounding code context
                surrounding_code: Optional[str] = None
                if filename in files_content_map:
                    surrounding_code = diff_utils.extract_surrounding_window(
                        full_code=files_content_map[filename],
                        target_lines=commentable_lines,
                        window=15
                    )

                # Static hints for this file
                file_hints = static_hints_by_file.get(filename)

                # Check LLM call budget
                if llm_calls_made >= max_llm_calls:
                    skipped_files.append((filename, f"Skipped: exceeded MAX_LLM_CALLS_PER_PR budget ({max_llm_calls})"))
                    continue

                try:
                    result = review_large_diff(
                        engine=review_engine,
                        patch=patch,
                        language=lang,
                        pr_title=pr_title,
                        pr_description=pr_body,
                        surrounding_code=surrounding_code,
                        readme_excerpt=readme_excerpt,
                        static_findings=file_hints,
                        custom_guidelines=repo_config.custom_guidelines,
                    )
                    llm_calls_made += 1
                    file_results.append((filename, result))

                    if config.LLM_MIN_INTERVAL_SECONDS > 0:
                        time.sleep(config.LLM_MIN_INTERVAL_SECONDS)

                    # Filter issues by repo configuration (category, confidence, severity)
                    for issue in result.issues:
                        # Category filter
                        if issue.category not in repo_config.enabled_categories:
                            logger.debug("Filtered issue %s: category %s not enabled in .reviewer.yml", issue.title, issue.category)
                            continue

                        # Confidence filter (default 0.5)
                        if issue.confidence is not None and issue.confidence < repo_config.min_confidence:
                            logger.debug("Filtered issue %s: confidence %s < min_confidence %s", issue.title, issue.confidence, repo_config.min_confidence)
                            continue

                        # Severity filter
                        if not is_severity_accepted(issue.severity, repo_config.min_severity):
                            logger.debug("Filtered issue %s: severity %s < min_severity %s", issue.title, issue.severity, repo_config.min_severity)
                            continue

                        # Map to inline comment or summary finding
                        if issue.line is not None and issue.line in commentable_lines:
                            comment_body = publisher.format_inline_comment_body(issue, language=lang)
                            comment_payload = {
                                "path": filename,
                                "line": issue.line,
                                "side": "RIGHT",
                                "body": comment_body,
                            }
                            comments_with_issues.append((issue, comment_payload))
                        else:
                            uncommentable_issues.append((filename, issue))

                except Exception as e:
                    logger.error("Review failed for file %s: %s", filename, e)
                    skipped_files.append((filename, f"Review error: {e}"))

            # 11. Prioritize and cap inline comments
            final_inline_comments, omitted_comments_count = publisher.prioritize_and_cap_comments(
                comments_with_issues=comments_with_issues,
                max_comments=repo_config.max_comments,
            )

            # 12. Build review summary body
            summary_body = publisher.build_review_summary(
                file_results=file_results,
                skipped_files=skipped_files,
                uncommentable_issues=uncommentable_issues,
                config_warning=config_warning,
                omitted_comments_count=omitted_comments_count,
            )

            # 13. Publish PR review
            review_resp = publisher.publish_pr_review(
                client=client,
                owner=owner,
                repo=repo,
                pull_number=pull_number,
                commit_id=head_sha,
                summary_body=summary_body,
                inline_comments=final_inline_comments,
                dry_run=dry_run,
            )

            # 14. Record review and issues in analytics database (if not dry run)
            duration_ms = int((time.time() - start_time) * 1000)
            high_count = sum(1 for _, res in file_results for i in res.issues if i.severity.lower() == "high")
            medium_count = sum(1 for _, res in file_results for i in res.issues if i.severity.lower() == "medium")
            low_count = sum(1 for _, res in file_results for i in res.issues if i.severity.lower() == "low")

            if not dry_run:
                try:
                    review_id = database.record_review(
                        source="github",
                        model=review_engine.model,
                        duration_ms=duration_ms,
                        llm_call_count=llm_calls_made,
                        repo=owner_repo,
                        pr_number=pull_number,
                        head_sha=head_sha,
                        high_count=high_count,
                        medium_count=medium_count,
                        low_count=low_count,
                    )
                    # Record issues and map to GitHub comment IDs if available
                    for filename, res in file_results:
                        for issue in res.issues:
                            issue_id = database.record_issue(
                                review_id=review_id,
                                severity=issue.severity,
                                category=issue.category,
                                title=issue.title,
                                explanation=issue.explanation,
                                file_path=filename,
                                line_number=issue.line,
                                confidence=issue.confidence,
                            )
                            issue.id = issue_id

                    # If GitHub returned comments, link their comment IDs
                    gh_comments = review_resp.get("comments", []) if isinstance(review_resp, dict) else []
                    for c in gh_comments:
                        c_id = c.get("id")
                        c_path = c.get("path")
                        c_line = c.get("line")
                        # Find matching issue ID
                        for filename, res in file_results:
                            if filename == c_path:
                                for issue in res.issues:
                                    if issue.line == c_line and issue.id:
                                        database.link_github_comment(
                                            comment_id=c_id,
                                            issue_id=issue.id,
                                            repo=owner_repo,
                                            pr_number=pull_number,
                                        )
                except Exception as db_err:
                    logger.warning("Failed to record GitHub review in analytics database: %s", db_err)

            return {
                "status": "completed",
                "owner": owner,
                "repo": repo,
                "pull_number": pull_number,
                "head_sha": head_sha,
                "inline_comments_count": len(final_inline_comments),
                "uncommentable_issues_count": len(uncommentable_issues),
                "skipped_files_count": len(skipped_files),
                "omitted_comments_count": omitted_comments_count,
                "config_warning": config_warning,
                "review": review_resp,
            }

    except Exception as exc:
        # Release lock if review failed so it can be retried
        if lock_acquired and head_sha:
            logger.info("Releasing review lock for %s @ %s due to review failure", owner_repo, head_sha[:7])
            release_commit_reviewed(owner_repo, head_sha)
        raise exc


def collect_pr_feedback(
    owner: str,
    repo: str,
    pull_number: int,
    installation_id: Optional[int] = None,
) -> None:
    """
    Collects developer reactions (+1 / -1) on the bot's review comments when a PR is closed.
    Records findings as 'helpful' or 'not_helpful' in the analytics database.
    """
    owner_repo = f"{owner}/{repo}"
    comment_links = database.get_github_comments_for_pr(owner_repo, pull_number)
    if not comment_links:
        logger.info("No tracked review comments found for closed PR #%d on %s", pull_number, owner_repo)
        return

    logger.info("Collecting feedback for %d review comments on closed PR #%d on %s", len(comment_links), pull_number, owner_repo)
    if installation_id is None:
        installation_id = auth.get_repo_installation_id(owner, repo)

    token = auth.get_installation_access_token(installation_id)
    with GitHubClient(token=token) as client:
        for comment_id, issue_id in comment_links:
            try:
                reactions = client.get_comment_reactions(owner, repo, comment_id)
                plus_ones = sum(1 for r in reactions if r.get("content") in ("+1", "THUMBS_UP", "heart"))
                minus_ones = sum(1 for r in reactions if r.get("content") in ("-1", "THUMBS_DOWN"))

                if plus_ones > minus_ones:
                    database.record_feedback(issue_id, "helpful")
                    logger.info("Recorded 'helpful' feedback on issue %s from comment #%d", issue_id, comment_id)
                elif minus_ones > plus_ones:
                    database.record_feedback(issue_id, "not_helpful")
                    logger.info("Recorded 'not_helpful' feedback on issue %s from comment #%d", issue_id, comment_id)
            except Exception as e:
                logger.warning("Failed to collect reactions for comment #%d on %s: %s", comment_id, owner_repo, e)
