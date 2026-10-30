"""SQLite database layer for analytics, reviews history, and user feedback."""
from __future__ import annotations

import datetime
import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app import config

logger = logging.getLogger(__name__)


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Creates (or re‑opens) a SQLite connection and guarantees that the schema
    exists. This makes the module safe to use in scripts or tests that never
    invoke the FastAPI ``startup`` event.

    The function always runs the same CREATE TABLE IF NOT EXISTS statements as
    ``init_db``; because they are idempotent the overhead is negligible and the
    schema is ensured on first use.
    """
    target_path = Path(db_path or config.DB_PATH).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(target_path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    # Ensure tables exist – safe to run on every connection.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS reviews (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            repo TEXT,
            pr_number INTEGER,
            head_sha TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            model TEXT NOT NULL,
            duration_ms INTEGER NOT NULL,
            llm_call_count INTEGER DEFAULT 1,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            high_count INTEGER DEFAULT 0,
            medium_count INTEGER DEFAULT 0,
            low_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS issues (
            id TEXT PRIMARY KEY,
            review_id TEXT NOT NULL,
            file_path TEXT,
            line_number INTEGER,
            severity TEXT NOT NULL,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            explanation TEXT NOT NULL,
            confidence REAL,
            feedback TEXT DEFAULT NULL,
            feedback_at TIMESTAMP DEFAULT NULL,
            FOREIGN KEY (review_id) REFERENCES reviews(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS github_comment_links (
            comment_id INTEGER PRIMARY KEY,
            issue_id TEXT NOT NULL,
            repo TEXT NOT NULL,
            pr_number INTEGER NOT NULL,
            FOREIGN KEY (issue_id) REFERENCES issues(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_reviews_created ON reviews(created_at);
        CREATE INDEX IF NOT EXISTS idx_issues_review_id ON issues(review_id);
        CREATE INDEX IF NOT EXISTS idx_github_links_pr ON github_comment_links(repo, pr_number);
    """)
    return conn
    """Creates a new SQLite database connection with WAL mode and foreign keys enabled."""
    target_path = Path(db_path or config.DB_PATH).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(target_path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """Initializes tables and indices for review history and issue feedback."""
    with get_connection(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS reviews (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                repo TEXT,
                pr_number INTEGER,
                head_sha TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                model TEXT NOT NULL,
                duration_ms INTEGER NOT NULL,
                llm_call_count INTEGER DEFAULT 1,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                high_count INTEGER DEFAULT 0,
                medium_count INTEGER DEFAULT 0,
                low_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS issues (
                id TEXT PRIMARY KEY,
                review_id TEXT NOT NULL,
                file_path TEXT,
                line_number INTEGER,
                severity TEXT NOT NULL,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                explanation TEXT NOT NULL,
                confidence REAL,
                feedback TEXT DEFAULT NULL,
                feedback_at TIMESTAMP DEFAULT NULL,
                FOREIGN KEY (review_id) REFERENCES reviews(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS github_comment_links (
                comment_id INTEGER PRIMARY KEY,
                issue_id TEXT NOT NULL,
                repo TEXT NOT NULL,
                pr_number INTEGER NOT NULL,
                FOREIGN KEY (issue_id) REFERENCES issues(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_reviews_created ON reviews(created_at);
            CREATE INDEX IF NOT EXISTS idx_issues_review_id ON issues(review_id);
            CREATE INDEX IF NOT EXISTS idx_github_links_pr ON github_comment_links(repo, pr_number);
        """)
    logger.info("Initialized database at %s", db_path or config.DB_PATH)


def get_reviews_count_today(db_path: Optional[str] = None) -> int:
    """Returns the total number of reviews executed today (UTC)."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM reviews WHERE date(created_at) = date('now')"
        )
        row = cursor.fetchone()
        return row[0] if row else 0


def record_review(
    source: str,
    model: str,
    duration_ms: int,
    llm_call_count: int = 1,
    repo: Optional[str] = None,
    pr_number: Optional[int] = None,
    head_sha: Optional[str] = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    high_count: int = 0,
    medium_count: int = 0,
    low_count: int = 0,
    review_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> str:
    """
    Records review metadata in the database.
    PRIVACY: Never stores raw source code, diffs, tokens, or secrets.
    """
    r_id = review_id or str(uuid.uuid4())
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO reviews (
                id, source, repo, pr_number, head_sha, model, duration_ms,
                llm_call_count, prompt_tokens, completion_tokens,
                high_count, medium_count, low_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r_id, source, repo, pr_number, head_sha, model, duration_ms,
                llm_call_count, prompt_tokens, completion_tokens,
                high_count, medium_count, low_count
            ),
        )
    return r_id


def record_issue(
    review_id: str,
    severity: str,
    category: str,
    title: str,
    explanation: str,
    file_path: Optional[str] = None,
    line_number: Optional[int] = None,
    confidence: Optional[float] = None,
    issue_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> str:
    """Records an individual review issue linked to a review."""
    i_id = issue_id or str(uuid.uuid4())
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO issues (
                id, review_id, file_path, line_number, severity,
                category, title, explanation, confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                i_id, review_id, file_path, line_number, severity.lower(),
                category.lower(), title, explanation, confidence
            ),
        )
    return i_id


def record_feedback(
    issue_id: str,
    feedback: str,
    db_path: Optional[str] = None,
) -> bool:
    """
    Records helpful / not_helpful feedback for a specific issue.
    Returns True if an issue was updated.
    """
    clean_fb = feedback.lower().strip()
    if clean_fb not in ("helpful", "not_helpful"):
        raise ValueError(f"Invalid feedback '{feedback}'; must be 'helpful' or 'not_helpful'")

    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE issues
            SET feedback = ?, feedback_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (clean_fb, issue_id),
        )
        return cursor.rowcount > 0


def link_github_comment(
    comment_id: int,
    issue_id: str,
    repo: str,
    pr_number: int,
    db_path: Optional[str] = None,
) -> None:
    """Links a published GitHub review comment ID to its internal issue ID."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO github_comment_links (
                comment_id, issue_id, repo, pr_number
            ) VALUES (?, ?, ?, ?)
            """,
            (comment_id, issue_id, repo.lower(), pr_number),
        )


def get_github_comments_for_pr(
    repo: str,
    pr_number: int,
    db_path: Optional[str] = None,
) -> List[Tuple[int, str]]:
    """Returns a list of (comment_id, issue_id) for a specific GitHub pull request."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "SELECT comment_id, issue_id FROM github_comment_links WHERE repo = ? AND pr_number = ?",
            (repo.lower(), pr_number),
        )
        return [(row["comment_id"], row["issue_id"]) for row in cursor.fetchall()]


def get_dashboard_stats(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Computes aggregated dashboard statistics for visualization."""
    with get_connection(db_path) as conn:
        # 1. Total Reviews
        cur = conn.execute("SELECT COUNT(*) FROM reviews")
        total_reviews = cur.fetchone()[0]

        # 2. Total Issues & Feedback
        cur = conn.execute("""
            SELECT
                COUNT(*) as total_issues,
                SUM(CASE WHEN feedback = 'helpful' THEN 1 ELSE 0 END) as helpful_count,
                SUM(CASE WHEN feedback = 'not_helpful' THEN 1 ELSE 0 END) as not_helpful_count
            FROM issues
        """)
        row = cur.fetchone()
        total_issues = row["total_issues"] or 0
        helpful_count = row["helpful_count"] or 0
        not_helpful_count = row["not_helpful_count"] or 0
        total_feedback = helpful_count + not_helpful_count
        helpfulness_rate = round((helpful_count / total_feedback) * 100, 1) if total_feedback > 0 else None

        # 3. Average duration & reviews today
        cur = conn.execute("""
            SELECT
                AVG(duration_ms) as avg_duration,
                SUM(CASE WHEN date(created_at) = date('now') THEN 1 ELSE 0 END) as today_reviews
            FROM reviews
        """)
        row = cur.fetchone()
        avg_duration = round(row["avg_duration"]) if row and row["avg_duration"] else 0
        today_reviews = row["today_reviews"] or 0 if row else 0

        # 4. Severity breakdown
        cur = conn.execute("""
            SELECT severity, COUNT(*) as count
            FROM issues
            GROUP BY severity
        """)
        severity_counts = {"high": 0, "medium": 0, "low": 0}
        for r in cur.fetchall():
            s = r["severity"].lower()
            if s in severity_counts:
                severity_counts[s] = r["count"]

        # 5. Category breakdown
        cur = conn.execute("""
            SELECT category, COUNT(*) as count
            FROM issues
            GROUP BY category
        """)
        category_counts: Dict[str, int] = {}
        for r in cur.fetchall():
            category_counts[r["category"].lower()] = r["count"]

        # 6. Reviews over time (last 14 days)
        cur = conn.execute("""
            SELECT date(created_at) as review_date, COUNT(*) as count
            FROM reviews
            WHERE created_at >= date('now', '-13 days')
            GROUP BY date(created_at)
            ORDER BY review_date ASC
        """)
        daily_trends = [{"date": r["review_date"], "count": r["count"]} for r in cur.fetchall()]

        # 7. Recent 10 reviews
        cur = conn.execute("""
            SELECT id, source, repo, pr_number, model, duration_ms, created_at,
                   high_count, medium_count, low_count
            FROM reviews
            ORDER BY created_at DESC
            LIMIT 10
        """)
        recent_reviews = [
            {
                "id": r["id"],
                "source": r["source"],
                "target": f"{r['repo']} #{r['pr_number']}" if r["repo"] and r["pr_number"] else "Web Paste",
                "model": r["model"],
                "duration_ms": r["duration_ms"],
                "created_at": r["created_at"],
                "high": r["high_count"],
                "medium": r["medium_count"],
                "low": r["low_count"],
                "total_issues": r["high_count"] + r["medium_count"] + r["low_count"],
            }
            for r in cur.fetchall()
        ]

        return {
            "total_reviews": total_reviews,
            "today_reviews": today_reviews,
            "total_issues": total_issues,
            "helpful_count": helpful_count,
            "not_helpful_count": not_helpful_count,
            "helpfulness_rate": helpfulness_rate,
            "avg_duration_ms": avg_duration,
            "severity_counts": severity_counts,
            "category_counts": category_counts,
            "daily_trends": daily_trends,
            "recent_reviews": recent_reviews,
        }

