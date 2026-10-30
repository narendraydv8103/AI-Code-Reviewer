"""Lightweight GitHub REST API client using httpx."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class GitHubAPIError(Exception):
    """Base exception for GitHub REST API errors."""
    def __init__(self, message: str, status_code: int = 500, response_text: str = ""):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response_text = response_text


class GitHubReviewCommentError(GitHubAPIError):
    """Raised specifically when GitHub rejects review comments (e.g. 422 Unprocessable Entity)."""
    pass


class GitHubClient:
    """Synchronous / reusable HTTP client for interacting with the GitHub REST API."""

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.github.com",
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "AI-Code-Reviewer-Bot",
            },
            timeout=timeout,
        )

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def get_pull_request(self, owner: str, repo: str, pull_number: int) -> Dict[str, Any]:
        """Fetches pull request metadata."""
        url = f"/repos/{owner}/{repo}/pulls/{pull_number}"
        response = self.client.get(url)
        if response.status_code != 200:
            raise GitHubAPIError(
                f"Failed to fetch PR #{pull_number} on {owner}/{repo}: {response.text}",
                status_code=response.status_code,
                response_text=response.text,
            )
        return response.json()

    def get_file_content(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: Optional[str] = None
    ) -> Optional[str]:
        """
        Fetches the text content of a file from repository at a given ref/commit SHA.
        Returns None if file does not exist (HTTP 404).
        """
        import base64
        clean_path = path.lstrip("/")
        url = f"/repos/{owner}/{repo}/contents/{clean_path}"
        params = {"ref": ref} if ref else {}
        response = self.client.get(url, params=params)

        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise GitHubAPIError(
                f"Failed to fetch file '{path}' at ref '{ref}' on {owner}/{repo}: {response.text}",
                status_code=response.status_code,
                response_text=response.text,
            )

        data = response.json()
        if isinstance(data, dict) and "content" in data and data.get("encoding") == "base64":
            try:
                raw_bytes = base64.b64decode(data["content"])
                return raw_bytes.decode("utf-8", errors="replace")
            except Exception as e:
                logger.warning("Failed to decode base64 file content for %s: %s", path, e)
                return None
        return None

    def get_pull_request_files(self, owner: str, repo: str, pull_number: int) -> List[Dict[str, Any]]:
        """
        Fetches the changed files in a pull request.
        Paginates up to 100 files per page.
        """
        files: List[Dict[str, Any]] = []
        page = 1
        per_page = 100

        while True:
            url = f"/repos/{owner}/{repo}/pulls/{pull_number}/files"
            response = self.client.get(url, params={"per_page": per_page, "page": page})
            if response.status_code != 200:
                raise GitHubAPIError(
                    f"Failed to fetch files for PR #{pull_number} on {owner}/{repo}: {response.text}",
                    status_code=response.status_code,
                    response_text=response.text,
                )
            page_files = response.json()
            if not page_files:
                break
            files.extend(page_files)
            if len(page_files) < per_page:
                break
            page += 1

        return files

    def create_pull_request_review(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        commit_id: str,
        body: str,
        comments: Optional[List[Dict[str, Any]]] = None,
        event: str = "COMMENT",
    ) -> Dict[str, Any]:
        """
        Publishes a single pull request review with a summary body and optional inline comments.

        Raises GitHubReviewCommentError on HTTP 422 (e.g. line outside diff hunk).
        """
        url = f"/repos/{owner}/{repo}/pulls/{pull_number}/reviews"
        payload: Dict[str, Any] = {
            "commit_id": commit_id,
            "body": body,
            "event": event,
        }
        if comments:
            payload["comments"] = comments

        logger.info(
            "Submitting PR review to %s/%s #%d (commit: %s, %d inline comments)",
            owner, repo, pull_number, commit_id[:7], len(comments or [])
        )
        response = self.client.post(url, json=payload)

        if response.status_code == 422:
            raise GitHubReviewCommentError(
                f"GitHub rejected pull request review (422 Unprocessable Entity): {response.text}",
                status_code=422,
                response_text=response.text,
            )

        if response.status_code not in (200, 201):
            raise GitHubAPIError(
                f"Failed to create PR review (HTTP {response.status_code}): {response.text}",
                status_code=response.status_code,
                response_text=response.text,
            )

        return response.json()

    def get_comment_reactions(
        self,
        owner: str,
        repo: str,
        comment_id: int
    ) -> List[Dict[str, Any]]:
        """
        Fetches reactions on a specific pull request review comment.
        Used to record developer feedback (+1 / -1) when a PR closes.
        """
        url = f"/repos/{owner}/{repo}/pulls/comments/{comment_id}/reactions"
        response = self.client.get(url)
        if response.status_code == 404:
            return []
        if response.status_code != 200:
            raise GitHubAPIError(
                f"Failed to fetch reactions for comment #{comment_id} on {owner}/{repo}: {response.text}",
                status_code=response.status_code,
                response_text=response.text,
            )
        return response.json()

