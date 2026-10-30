"""FastAPI web server for AI Code Reviewer."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import config, database, security
from app.schemas import FeedbackRequest, ReviewRequest, ReviewResponse
from github_bot.webhook import router as github_webhook_router
from reviewer.engine import (
    ReviewAuthError,
    ReviewEngine,
    ReviewEngineError,
    ReviewParsingError,
    ReviewTimeoutError,
    ReviewTruncationError,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(
    title="AI Code Reviewer",
    description="Automated code and pull request review engine.",
    version="2.0.0"
)

# Enable CORS for local testing flexibility
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static folder
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Mount GitHub Webhooks router
app.include_router(github_webhook_router)

# Shared review engine instance
review_engine = ReviewEngine()


@app.on_event("startup")
async def on_startup():
    """Initializes the database schema and WAL mode on application start."""
    try:
        database.init_db()
        logger.info("Database initialized successfully at %s", config.DB_PATH)
    except Exception as e:
        logger.error("Failed to initialize database on startup: %s", e)


# -------------------------------------------------------------
# Frontend HTML Routes
# -------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def serve_landing():
    """Serves the marketing and demo landing page."""
    landing_file = STATIC_DIR / "landing.html"
    if landing_file.is_file():
        return FileResponse(str(landing_file))
    # Fallback to index if landing not created yet
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/app", include_in_schema=False)
async def serve_app():
    """Serves the interactive code review workspace application."""
    index_file = STATIC_DIR / "index.html"
    if not index_file.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Frontend interface (index.html) not found."
        )
    return FileResponse(str(index_file))


@app.get("/dashboard", include_in_schema=False)
async def serve_dashboard(_: None = Depends(security.verify_dashboard_auth)):
    """Serves the visual metrics and feedback dashboard."""
    dashboard_file = STATIC_DIR / "dashboard.html"
    if not dashboard_file.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard interface (dashboard.html) not found."
        )
    return FileResponse(str(dashboard_file))


# -------------------------------------------------------------
# API Endpoints
# -------------------------------------------------------------

@app.get("/api/health")
async def health_check():
    """
    Healthcheck endpoint for Docker, Kubernetes, and uptime monitoring.
    Verifies database connectivity and returns daily review usage.
    """
    try:
        today_count = database.get_reviews_count_today()
        db_status = "connected"
    except Exception as e:
        logger.error("Database healthcheck failed: %s", e)
        db_status = f"unhealthy: {e}"

    provider = config.get_active_provider()
    has_key = config.has_active_api_key()
    is_healthy = (db_status == "connected") and has_key

    res = {
        "status": "healthy" if is_healthy else "degraded",
        "database": db_status,
        "provider": provider,
        "model": config.get_active_model(),
        "reviews_today": today_count if db_status == "connected" else None,
        "max_reviews_per_day": config.MAX_REVIEWS_PER_DAY,
    }
    if not has_key:
        if provider == "gemini":
            res["message"] = "Gemini API key is not configured. Add GEMINI_API_KEY to your .env file."
        else:
            res["message"] = "Anthropic API key is not configured. Add ANTHROPIC_API_KEY to your .env file."

    return res


@app.get("/api/config")
async def get_client_config():
    """Returns frontend runtime configurations."""
    return {
        "provider": config.get_active_provider(),
        "default_model": config.get_active_model(),
        "has_api_key": config.has_active_api_key(),
        "max_code_chars": config.MAX_CODE_CHARS,
        "max_reviews_per_day": config.MAX_REVIEWS_PER_DAY,
        "has_access_token": bool(config.ACCESS_TOKEN.strip()),
    }


@app.get("/api/dashboard/stats")
async def get_dashboard_metrics(_: None = Depends(security.verify_dashboard_auth)):
    """Returns aggregated review counts, category/severity distributions, and feedback stats."""
    try:
        return database.get_dashboard_stats()
    except Exception as e:
        logger.exception("Failed to retrieve dashboard stats: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate dashboard statistics: {e}"
        )


@app.post("/api/feedback")
async def submit_feedback(
    payload: FeedbackRequest,
    request: Request,
):
    """
    Records helpful / not_helpful user feedback on a review issue card.
    """
    security.verify_access_token(request)
    try:
        updated = database.record_feedback(payload.issue_id, payload.feedback)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Issue with ID '{payload.issue_id}' not found."
            )
        return {"status": "success", "issue_id": payload.issue_id, "feedback": payload.feedback}
    except ValueError as val_err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(val_err))
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        logger.exception("Failed to record feedback: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to record feedback: {e}"
        )


@app.post("/api/review", response_model=ReviewResponse)
async def review_code(
    payload: ReviewRequest,
    request: Request,
):
    """
    Submits source code or a git diff for structured automated code review.

    Hardening:
    - Enforces IP rate limiting.
    - Enforces MAX_REVIEWS_PER_DAY quota.
    - Enforces optional ACCESS_TOKEN authorization.
    - Records review metrics in SQLite (excluding raw source code).
    """
    # 1. Security & Quota verification
    security.check_rate_limit(request)
    security.check_daily_quota()
    security.verify_access_token(request)

    # 2. Input validation
    cleaned_code = payload.code.strip()
    if not cleaned_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Code snippet or diff cannot be empty."
        )

    if len(payload.code) > config.MAX_CODE_CHARS:
        raise HTTPException(
            status_code=getattr(status, "HTTP_413_CONTENT_TOO_LARGE", 413),
            detail=f"Input size ({len(payload.code):,} characters) exceeds the maximum limit of {config.MAX_CODE_CHARS:,} characters."
        )

    start_time = time.time()
    try:
        result = review_engine.review(
            code=payload.code,
            language=payload.language,
            mode=payload.mode
        )
        duration_ms = int((time.time() - start_time) * 1000)

        # 3. Record in analytics database
        high_c = sum(1 for i in result.issues if i.severity.lower() == "high")
        med_c = sum(1 for i in result.issues if i.severity.lower() == "medium")
        low_c = sum(1 for i in result.issues if i.severity.lower() == "low")

        review_id = database.record_review(
            source="web",
            model=review_engine.model,
            duration_ms=duration_ms,
            llm_call_count=1,
            high_count=high_c,
            medium_count=med_c,
            low_count=low_c,
        )

        # Assign unique issue IDs and record issues in DB
        for issue in result.issues:
            issue_id = database.record_issue(
                review_id=review_id,
                severity=issue.severity,
                category=issue.category,
                title=issue.title,
                explanation=issue.explanation,
                file_path=None,
                line_number=issue.line,
                confidence=issue.confidence,
            )
            issue.id = issue_id

        return ReviewResponse(
            review_id=review_id,
            summary=result.summary,
            issues=result.issues,
            total_issues=len(result.issues)
        )

    except ReviewAuthError as e:
        logger.error("Authentication failure: %s", e.message)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=e.message)
    except ReviewTimeoutError as e:
        logger.warning("Timeout during review: %s", e.message)
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=e.message)
    except (ReviewTruncationError, ReviewParsingError) as e:
        logger.warning("Engine parsing or truncation failure: %s", e.message)
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except ReviewEngineError as e:
        logger.error("Engine execution failure: %s", e.message)
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        logger.exception("Unexpected error during code review: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected server error occurred: {str(e)}"
        )
