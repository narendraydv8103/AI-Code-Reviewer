"""Pydantic schemas for the FastAPI layer."""
from __future__ import annotations

from typing import List, Literal, Optional
from pydantic import BaseModel, Field

from reviewer.models import ReviewIssue


class ReviewRequest(BaseModel):
    """API request payload for submitting code or diff to review."""
    code: str = Field(
        ...,
        description="The source code snippet or unified git diff to be reviewed."
    )
    language: str = Field(
        default="plaintext",
        description="The programming language identifier (e.g. python, javascript, go, rust)."
    )
    mode: Literal["code", "diff"] = Field(
        default="code",
        description="Review mode: 'code' for complete snippets/files, 'diff' for git diffs."
    )


class ReviewResponse(BaseModel):
    """API response payload returned to the frontend."""
    review_id: Optional[str] = Field(
        default=None,
        description="Unique identifier for the review in analytics database."
    )
    summary: str = Field(
        ...,
        description="High-level overview of findings and recommendations."
    )
    issues: List[ReviewIssue] = Field(
        default_factory=list,
        description="Detailed review issues categorized by severity and issue type."
    )
    total_issues: int = Field(
        ...,
        description="Total count of issues detected."
    )


class FeedbackRequest(BaseModel):
    """API request payload for submitting helpful/not_helpful user feedback on an issue."""
    issue_id: str = Field(
        ...,
        min_length=1,
        description="The unique identifier of the issue being rated."
    )
    feedback: Literal["helpful", "not_helpful"] = Field(
        ...,
        description="User rating: 'helpful' or 'not_helpful'."
    )
