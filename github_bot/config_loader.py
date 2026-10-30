"""Per-repository configuration loader for .reviewer.yml."""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import yaml
from pydantic import BaseModel, Field, ValidationError

from github_bot.client import GitHubClient
from reviewer.models import Category, Severity

logger = logging.getLogger(__name__)


class ReviewerConfig(BaseModel):
    """Configuration options parsed from .reviewer.yml in the base branch."""
    min_severity: Severity = Field(
        default="low",
        description="Minimum severity required to report an issue."
    )
    enabled_categories: List[Category] = Field(
        default_factory=lambda: ["bug", "security", "performance", "style", "tests"],
        description="Categories enabled for code review."
    )
    ignore_path_globs: List[str] = Field(
        default_factory=list,
        description="Glob patterns of file paths to exclude from reviews."
    )
    max_comments: int = Field(
        default=15,
        ge=1,
        le=50,
        description="Maximum number of inline comments posted per PR review."
    )
    min_confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Minimum confidence score required to report an issue."
    )
    custom_guidelines: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Optional custom repository guidelines appended to the review prompt."
    )


def load_repo_config(
    client: GitHubClient,
    owner: str,
    repo: str,
    base_ref: str,
) -> Tuple[ReviewerConfig, Optional[str]]:
    """
    Fetches and validates .reviewer.yml strictly from the BASE/default branch.

    Returns:
        (ReviewerConfig, Optional[warning_message_if_invalid])
    """
    config_content: Optional[str] = None
    # Check .reviewer.yml first, then .reviewer.yaml
    for candidate in [".reviewer.yml", ".reviewer.yaml"]:
        try:
            content = client.get_file_content(owner, repo, candidate, ref=base_ref)
            if content is not None:
                config_content = content
                break
        except Exception as e:
            logger.debug("Could not fetch %s on %s/%s at %s: %s", candidate, owner, repo, base_ref, e)

    # Missing file -> use default silently
    if config_content is None or not config_content.strip():
        return ReviewerConfig(), None

    try:
        raw_data = yaml.safe_load(config_content)
        if not isinstance(raw_data, dict):
            msg = "`.reviewer.yml` is not a valid YAML mapping; using default settings."
            logger.warning("%s on %s/%s", msg, owner, repo)
            return ReviewerConfig(), msg

        validated_config = ReviewerConfig.model_validate(raw_data)
        return validated_config, None

    except yaml.YAMLError as y_err:
        msg = f"`.reviewer.yml` has syntax errors ({y_err}); using default settings."
        logger.warning("%s on %s/%s", msg, owner, repo)
        return ReviewerConfig(), msg
    except ValidationError as v_err:
        msg = f"`.reviewer.yml` failed schema validation ({v_err.errors()[0]['msg'] if v_err.errors() else v_err}); using default settings."
        logger.warning("%s on %s/%s", msg, owner, repo)
        return ReviewerConfig(), msg
    except Exception as err:
        msg = f"Failed to load `.reviewer.yml` ({err}); using default settings."
        logger.warning("%s on %s/%s", msg, owner, repo)
        return ReviewerConfig(), msg

