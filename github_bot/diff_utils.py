"""Diff and patch analysis utilities for GitHub PR reviews."""
from __future__ import annotations

import fnmatch
import os
import re
from typing import List, Optional, Set, Tuple

# Standard lockfile names to exclude from review
LOCKFILES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pipfile.lock",
    "cargo.lock",
    "go.sum",
    "composer.lock",
}

# Directories typically containing generated or external vendor code
VENDOR_DIRS = (
    "node_modules/",
    "vendor/",
    "dist/",
    "build/",
    ".next/",
    ".nuxt/",
    "target/",
    "bin/",
    "obj/",
)

# Binary or media file extensions that should never be reviewed
BINARY_OR_ASSET_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
    ".woff", ".woff2", ".ttf", ".eot",
    ".zip", ".tar", ".gz", ".7z", ".rar",
    ".pdf", ".exe", ".dll", ".so", ".dylib",
    ".class", ".pyc", ".pyo",
    ".min.js", ".min.css", ".map",
}

# Mapping common file extensions to programming languages
EXTENSION_LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".sql": "sql",
    ".sh": "bash",
    ".bash": "bash",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".sass": "sass",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
    ".toml": "toml",
    ".xml": "xml",
}


def detect_language(filename: str) -> str:
    """Detects programming language from file extension, defaulting to plaintext."""
    name_lower = filename.lower()
    for ext, lang in EXTENSION_LANGUAGE_MAP.items():
        if name_lower.endswith(ext):
            return lang
    return "plaintext"


def is_reviewable_file(
    filename: str,
    patch: Optional[str],
    status: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    """
    Determines if a pull request file is eligible for automated code review.

    Returns:
        (is_eligible, skip_reason)
    """
    if not patch or not patch.strip():
        return False, "File has no text diff or patch."

    if status == "removed":
        return False, "File was deleted."

    name_lower = filename.lower()
    basename = os.path.basename(name_lower)

    # Check lock files
    if basename in LOCKFILES:
        return False, f"Lockfile ({basename}) is excluded from automated review."

    # Check vendor and build directories
    for v_dir in VENDOR_DIRS:
        if v_dir in name_lower:
            return False, f"File in vendor/build directory ({v_dir}) is excluded."

    # Check minified or binary extensions
    for ext in BINARY_OR_ASSET_EXTS:
        if name_lower.endswith(ext):
            return False, f"Binary or asset extension ({ext}) is excluded."

    return True, None


def get_commentable_lines(patch: str) -> Set[int]:
    """
    Parses a unified diff patch string and returns the set of valid new-file line numbers.

    GitHub's Pull Request Reviews API strictly requires inline comments on side="RIGHT"
    to belong to line numbers that appear as added ('+') or context (' ') lines in the diff hunks.
    Lines outside hunks, deleted-only lines, or file-level issues cannot receive inline comments.
    """
    commentable: Set[int] = set()
    if not patch:
        return commentable

    hunk_regex = re.compile(r"^@@\s+-[0-9]+(?:,[0-9]+)?\s+\+([0-9]+)(?:,[0-9]+)?\s+@@")

    curr_new_line = 0
    in_hunk = False

    for line in patch.splitlines():
        hunk_match = hunk_regex.match(line)
        if hunk_match:
            curr_new_line = int(hunk_match.group(1))
            in_hunk = True
            continue

        if not in_hunk:
            continue

        if line.startswith("+") or line.startswith(" "):
            commentable.add(curr_new_line)
            curr_new_line += 1
        elif line.startswith("-"):
            # Deletions only advance old-file counter; new-file line is not advanced
            pass
        elif line.startswith("\\"):
            # e.g. "\ No newline at end of file"
            pass

    return commentable


def matches_any_glob(filepath: str, patterns: List[str]) -> bool:
    """Returns True if filepath matches any of the glob patterns."""
    if not patterns:
        return False
    norm_path = filepath.replace("\\", "/").strip()
    for pat in patterns:
        clean_pat = pat.strip()
        if fnmatch.fnmatch(norm_path, clean_pat) or fnmatch.fnmatch(os.path.basename(norm_path), clean_pat):
            return True
    return False


def extract_surrounding_window(
    full_code: str,
    target_lines: Set[int],
    window: int = 15,
    max_total_lines: int = 250,
) -> str:
    """
    Extracts a window of code around target lines with 1-based line numbers.
    If full_code is under max_total_lines, returns the entire annotated file.
    """
    lines = full_code.splitlines()
    if not lines:
        return full_code

    if len(lines) <= max_total_lines or not target_lines:
        max_w = len(str(len(lines)))
        return "\n".join(f"{str(idx).rjust(max_w)} | {line_text}" for idx, line_text in enumerate(lines, start=1))

    # Determine line ranges to include
    included_indices: Set[int] = set()
    for t_line in target_lines:
        start_idx = max(0, t_line - 1 - window)
        end_idx = min(len(lines), t_line + window)
        for i in range(start_idx, end_idx):
            included_indices.add(i)

    max_w = len(str(len(lines)))
    output: List[str] = []
    prev_idx = -2

    for idx in sorted(included_indices):
        if idx > prev_idx + 1 and prev_idx != -2:
            output.append("...")
        output.append(f"{str(idx + 1).rjust(max_w)} | {lines[idx]}")
        prev_idx = idx

    return "\n".join(output)
