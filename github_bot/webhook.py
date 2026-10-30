"""GitHub webhook receiver and event dispatcher for FastAPI."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from app import config
from github_bot import service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["GitHub Webhook"])

ALLOWED_PR_ACTIONS = {"opened", "synchronize", "reopened"}


def verify_signature(payload_bytes: bytes, signature_header: Optional[str]) -> bool:
    """
    Verifies that the webhook payload was sent by GitHub using HMAC-SHA256.
    Performs constant-time comparison to prevent timing attacks.
    """
    secret = config.GITHUB_WEBHOOK_SECRET
    if not secret or not secret.strip():
        logger.error("GITHUB_WEBHOOK_SECRET is not configured on server.")
        return False

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    received_sig = signature_header.split("sha256=", 1)[1]
    expected_sig = hmac.new(
        key=secret.strip().encode("utf-8"),
        msg=payload_bytes,
        digestmod=hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(received_sig, expected_sig)


@router.post("/github", status_code=status.HTTP_200_OK)
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: Optional[str] = Header(None, alias="X-GitHub-Event"),
    x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
):
    """
    GitHub Webhook receiver endpoint.

    Verifies HMAC-SHA256 signature, responds immediately to ping events,
    filters relevant PR events, and delegates review execution to background tasks.
    """
    payload_bytes = await request.body()

    # 1. Webhook signature security verification
    if not verify_signature(payload_bytes, x_hub_signature_256):
        logger.warning("Rejected webhook request with invalid or missing X-Hub-Signature-256")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Hub-Signature-256 header."
        )

    try:
        payload: Dict[str, Any] = json.loads(payload_bytes)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Malformed JSON payload: {e}"
        )

    # 2. Respond to GitHub 'ping' event
    if x_github_event == "ping":
        logger.info("Received GitHub ping event (zen: %s)", payload.get("zen"))
        return {"status": "pong", "zen": payload.get("zen")}

    # 3. Only process 'pull_request' events
    if x_github_event != "pull_request":
        logger.debug("Ignoring non-pull_request event '%s'", x_github_event)
        return {"status": "ignored", "reason": f"Event '{x_github_event}' not processed"}

    action = payload.get("action")

    # 4. Handle PR 'closed' event strictly for reaction feedback collection (never reviews code)
    if action == "closed":
        pr = payload.get("pull_request", {})
        repo_info = payload.get("repository", {})
        owner = repo_info.get("owner", {}).get("login")
        repo = repo_info.get("name")
        pull_number = payload.get("number") or pr.get("number")
        installation_id = payload.get("installation", {}).get("id")

        if owner and repo and pull_number:
            logger.info("PR #%d on %s/%s closed; enqueuing reaction feedback collection", pull_number, owner, repo)
            background_tasks.add_task(
                service.collect_pr_feedback,
                owner=owner,
                repo=repo,
                pull_number=pull_number,
                installation_id=installation_id,
            )
        return {"status": "feedback_enqueued", "action": "closed"}

    if action not in ALLOWED_PR_ACTIONS:
        logger.info("Ignoring pull_request action '%s'", action)
        return {"status": "ignored", "reason": f"Action '{action}' not handled"}

    pr = payload.get("pull_request", {})

    # 4. Filter drafts
    if pr.get("draft") is True:
        logger.info("Ignoring pull_request #%s because it is a draft", pr.get("number"))
        return {"status": "ignored", "reason": "Draft pull requests are skipped"}

    # 5. Filter PRs authored by bots (based strictly on PR author, not event sender)
    author = pr.get("user", {})
    if (
        author.get("type") == "Bot"
        or author.get("login", "").endswith("[bot]")
    ):
        logger.info("Ignoring pull_request #%s: authored by a bot (%s)", pr.get("number"), author.get("login"))
        return {"status": "ignored", "reason": "Bot-authored pull requests are skipped"}

    # 6. Extract repository & PR information
    repo_info = payload.get("repository", {})
    owner = repo_info.get("owner", {}).get("login")
    repo = repo_info.get("name")
    pull_number = payload.get("number") or pr.get("number")
    head_sha = pr.get("head", {}).get("sha")

    if not owner or not repo or not pull_number or not head_sha:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incomplete pull_request payload (missing owner, repo, number, or head SHA)."
        )

    owner_repo = f"{owner}/{repo}"

    # 7. Check if this exact head commit was already reviewed
    if service.is_commit_reviewed(owner_repo, head_sha):
        logger.info("Skipping pull_request #%d on %s: commit %s already reviewed", pull_number, owner_repo, head_sha[:7])
        return {"status": "skipped", "reason": "Commit SHA was already reviewed", "head_sha": head_sha}

    installation_id = payload.get("installation", {}).get("id")

    # 8. Dispatch review to FastAPI background task
    logger.info(
        "Enqueuing automated review for %s #%d (commit: %s, action: %s)",
        owner_repo, pull_number, head_sha[:7], action
    )

    background_tasks.add_task(
        service.review_pull_request,
        owner=owner,
        repo=repo,
        pull_number=pull_number,
        installation_id=installation_id,
        dry_run=False,
    )

    return {
        "status": "enqueued",
        "repo": owner_repo,
        "pull_number": pull_number,
        "head_sha": head_sha,
        "action": action,
    }
