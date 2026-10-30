"""Safe orchestrator for running static analyzers in an isolated temporary directory."""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Set

from analysis.bandit_runner import run_bandit
from analysis.models import StaticAnalysisFinding
from analysis.semgrep_runner import run_semgrep

logger = logging.getLogger(__name__)


def is_safe_relative_path(rel_path: str, base_dir: Path) -> Optional[Path]:
    """
    Validates that rel_path is not absolute, contains no '..' traversal segments,
    and resolves strictly within base_dir. Returns resolved Path or None.
    """
    if not rel_path or os.path.isabs(rel_path):
        return None

    # Check for path traversal segments
    parts = Path(rel_path).parts
    if ".." in parts or "." in parts[:-1]:
        return None

    try:
        resolved = (base_dir / rel_path).resolve()
        base_resolved = base_dir.resolve()
        # Verify resolved path stays inside base directory
        if not str(resolved).startswith(str(base_resolved) + os.sep) and resolved != base_resolved:
            return None
        return resolved
    except Exception:
        return None


def run_static_analysis(
    files: Dict[str, str],
    commentable_lines_map: Dict[str, Set[int]],
) -> List[StaticAnalysisFinding]:
    """
    Safely writes PR files to a temporary directory, executes Bandit & Semgrep,
    cleans up the directory in a finally block, and retains only findings on modified lines.

    Args:
        files: Mapping of relative filename -> file content string.
        commentable_lines_map: Mapping of relative filename -> set of commentable line numbers in diff.

    Returns:
        List of verified StaticAnalysisFinding strictly matching modified lines.
    """
    if not files:
        return []

    temp_dir = tempfile.TemporaryDirectory(prefix="reviewer_analysis_")
    try:
        temp_path = Path(temp_dir.name)

        # 1. Write files securely with traversal safeguards
        written_count = 0
        for rel_path, content in files.items():
            target_path = is_safe_relative_path(rel_path, temp_path)
            if target_path is None:
                logger.warning("Rejected suspicious file path during static analysis prep: %s", rel_path)
                continue

            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(content, encoding="utf-8", errors="replace")
                written_count += 1
            except Exception as write_err:
                logger.warning("Failed to write temp file for static analysis (%s): %s", rel_path, write_err)

        if written_count == 0:
            return []

        # 2. Execute Bandit & Semgrep
        all_findings: List[StaticAnalysisFinding] = []
        all_findings.extend(run_bandit(temp_path))
        all_findings.extend(run_semgrep(temp_path))

        # 3. Retain only findings on changed/commentable lines
        filtered_findings: List[StaticAnalysisFinding] = []
        for f in all_findings:
            # Normalize path delimiters
            norm_file = f.file.replace("\\", "/")
            valid_lines = commentable_lines_map.get(norm_file, set())
            if f.line in valid_lines:
                filtered_findings.append(f)
            else:
                logger.debug(
                    "Dropped static finding on unchanged line %s:%d (%s)",
                    norm_file, f.line, f.rule_id
                )

        return filtered_findings

    finally:
        # Guarantee cleanup in all circumstances
        try:
            temp_dir.cleanup()
        except Exception as cleanup_err:
            logger.debug("Temp directory cleanup notice: %s", cleanup_err)

