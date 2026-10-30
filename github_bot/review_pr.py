"""CLI runner to test and execute pull request reviews directly without webhooks.

Usage:
    python -m github_bot.review_pr <owner/repo> <pr_number> [--dry-run] [--force]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from github_bot import service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("github_bot.cli")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run AI Code Reviewer on a GitHub Pull Request from the command line."
    )
    parser.add_argument(
        "target",
        help="Repository target in the format 'owner/repo' (e.g. octocat/Hello-World)"
    )
    parser.add_argument(
        "pr_number",
        type=int,
        help="Pull Request number (e.g. 42)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulates the review and prints the review body & inline comments without posting to GitHub."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Forces review execution even if the head commit SHA was already reviewed."
    )

    args = parser.parse_args()

    if "/" not in args.target:
        print(f"Error: Target '{args.target}' must be in the format 'owner/repo'", file=sys.stderr)
        return 1

    owner, repo = args.target.strip().split("/", 1)
    if not owner or not repo:
        print(f"Error: Invalid repository format '{args.target}'", file=sys.stderr)
        return 1

    print(f"==> Initiating review for {owner}/{repo} PR #{args.pr_number} (dry_run={args.dry_run})")

    try:
        result = service.review_pull_request(
            owner=owner,
            repo=repo,
            pull_number=args.pr_number,
            dry_run=args.dry_run,
            force=args.force,
        )
    except Exception as e:
        logger.exception("Failed to review PR #%d on %s/%s: %s", args.pr_number, owner, repo, e)
        print(f"\n[ERROR] Review pipeline failed: {e}", file=sys.stderr)
        return 1

    if result.get("status") == "skipped":
        print(f"\n[SKIPPED] Reason: {result.get('reason')}")
        return 0

    review_info = result.get("review", {})
    inline_count = result.get("inline_comments_count", 0)
    uncommentable_count = result.get("uncommentable_issues_count", 0)
    skipped_count = result.get("skipped_files_count", 0)

    print("\n" + "=" * 60)
    print("                    REVIEW SUMMARY")
    print("=" * 60)
    print(f"Repository:            {owner}/{repo}")
    print(f"Pull Request:          #{args.pr_number}")
    print(f"Head Commit SHA:       {result.get('head_sha')}")
    print(f"Inline Comments:       {inline_count}")
    print(f"Contextual Issues:     {uncommentable_count}")
    print(f"Skipped Files:         {skipped_count}")
    print("=" * 60)

    if args.dry_run:
        print("\n[DRY RUN OUTPUT] Review Body that would be posted to GitHub:\n")
        print(review_info.get("body", ""))

        comments = review_info.get("comments", [])
        if comments:
            print("\n" + "-" * 60)
            print(f"[DRY RUN OUTPUT] {len(comments)} Inline Comments that would be posted:")
            print("-" * 60)
            for idx, c in enumerate(comments, start=1):
                print(f"\n--- Comment #{idx} on {c['path']} (Line {c['line']}, Side {c['side']}) ---")
                print(c["body"])
        else:
            print("\nNo inline comments generated.")
    else:
        print(f"\n[SUCCESS] Successfully posted review to GitHub PR #{args.pr_number}!")
        if "html_url" in review_info:
            print(f"View review at: {review_info['html_url']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

