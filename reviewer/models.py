"""Core domain models for code review."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

Severity = Literal["low", "medium", "high"]
Category = Literal["bug", "security", "performance", "style", "tests"]


class ReviewIssue(BaseModel):
    """Individual code review issue identified by the LLM."""
    id: Optional[str] = Field(
        default=None,
        description="Unique identifier for the issue (used for feedback tracking)."
    )
    line: Optional[int] = Field(
        default=None,
        description="Line number where issue originates. For diff mode, use new-file line numbers from @@ headers, or null if unknown."
    )
    severity: Severity = Field(
        ...,
        description="Severity level of the issue: 'low', 'medium', or 'high'."
    )
    category: Category = Field(
        ...,
        description="Issue category: 'bug', 'security', 'performance', 'style', or 'tests'."
    )
    title: str = Field(
        ...,
        min_length=1,
        description="Concise, clear title of the issue."
    )
    explanation: str = Field(
        ...,
        min_length=1,
        description="Detailed technical explanation of what is wrong and why."
    )
    suggested_fix: Optional[str] = Field(
        default=None,
        description="Concrete replacement code or specific fix instructions, or null if not applicable."
    )
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Confidence score from 0.0 to 1.0 estimating certainty that this is an actionable true-positive defect."
    )


class ReviewResult(BaseModel):
    """Complete code review output containing summary and list of issues."""
    summary: str = Field(
        ...,
        min_length=1,
        description="Short overall summary evaluating code quality and highlighting primary concerns."
    )
    issues: List[ReviewIssue] = Field(
        default_factory=list,
        description="List of detected review issues."
    )

