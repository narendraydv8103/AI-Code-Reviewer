"""Bandit static security analyzer for Python code."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import List

from analysis.models import StaticAnalysisFinding

logger = logging.getLogger(__name__)


def run_bandit(target_dir: Path, timeout: float = 20.0) -> List[StaticAnalysisFinding]:
    """
    Executes Bandit with built-in rules against Python files in target_dir.

    Returns an empty list with a log message if Bandit is not installed or times out.
    """
    bandit_bin = shutil.which("bandit")
    if not bandit_bin:
        logger.info("Bandit is not installed or not in PATH; skipping Bandit analysis.")
        return []

    cmd = [
        bandit_bin,
        "-r",
        str(target_dir),
        "-f",
        "json",
        "-q",
        "--exit-zero",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Bandit analysis timed out after %s seconds; skipping.", timeout)
        return []
    except Exception as e:
        logger.warning("Failed to execute Bandit: %s; skipping.", e)
        return []

    if not proc.stdout or not proc.stdout.strip():
        return []

    findings: List[StaticAnalysisFinding] = []
    try:
        data = json.loads(proc.stdout)
        results = data.get("results", [])
        for item in results:
            abs_filename = item.get("filename", "")
            try:
                rel_file = os.path.relpath(abs_filename, str(target_dir))
            except ValueError:
                rel_file = abs_filename

            line = item.get("line_number")
            if line is None:
                continue

            findings.append(
                StaticAnalysisFinding(
                    tool="Bandit",
                    file=rel_file,
                    line=int(line),
                    rule_id=str(item.get("test_id", "bandit")),
                    message=str(item.get("issue_text", "")),
                    severity=str(item.get("issue_severity", "MEDIUM")).lower(),
                )
            )
    except Exception as e:
        logger.warning("Failed to parse Bandit JSON output: %s", e)

    return findings

