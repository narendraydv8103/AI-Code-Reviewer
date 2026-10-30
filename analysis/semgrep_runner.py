"""Semgrep static analyzer using built-in standard rulesets."""
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


def run_semgrep(target_dir: Path, timeout: float = 30.0) -> List[StaticAnalysisFinding]:
    """
    Executes Semgrep using built-in standard rulesets (p/default).

    Requirements:
    - Runs with --metrics=off and --disable-version-check.
    - If rule download fails or machine is offline, skips Semgrep with a log message;
      never fails or blocks the review.
    """
    semgrep_bin = shutil.which("semgrep")
    if not semgrep_bin:
        logger.info("Semgrep is not installed or not in PATH; skipping Semgrep analysis.")
        return []

    cmd = [
        semgrep_bin,
        "--config",
        "p/default",
        "--json",
        "--disable-version-check",
        "--metrics=off",
        str(target_dir),
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
        logger.warning("Semgrep analysis timed out after %s seconds; skipping.", timeout)
        return []
    except Exception as e:
        logger.warning("Failed to execute Semgrep: %s; skipping.", e)
        return []

    # Semgrep exits with 0 on clean or findings, or non-zero on failure/network errors
    if proc.returncode != 0 and (not proc.stdout or not proc.stdout.strip()):
        stderr_sample = (proc.stderr or "").strip().splitlines()
        err_msg = stderr_sample[0] if stderr_sample else f"exit code {proc.returncode}"
        logger.info("Semgrep execution skipped (offline or rule download failed: %s).", err_msg)
        return []

    if not proc.stdout or not proc.stdout.strip():
        return []

    findings: List[StaticAnalysisFinding] = []
    try:
        data = json.loads(proc.stdout)
        results = data.get("results", [])
        for item in results:
            abs_path = item.get("path", "")
            try:
                rel_file = os.path.relpath(abs_path, str(target_dir))
            except ValueError:
                rel_file = abs_path

            start_info = item.get("start", {})
            line = start_info.get("line")
            if line is None:
                continue

            extra = item.get("extra", {})
            findings.append(
                StaticAnalysisFinding(
                    tool="Semgrep",
                    file=rel_file,
                    line=int(line),
                    rule_id=str(item.get("check_id", "semgrep")),
                    message=str(extra.get("message", "")),
                    severity=str(extra.get("severity", "WARNING")).lower(),
                )
            )
    except Exception as e:
        logger.warning("Failed to parse Semgrep JSON output: %s; skipping.", e)

    return findings

