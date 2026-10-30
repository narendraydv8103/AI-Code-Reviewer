# AI Code Reviewer

An automated code review system that reviews raw source code, unified `git diff`s, and **GitHub Pull Requests**, returning structured, actionable feedback (logic bugs, security vulnerabilities, performance bottlenecks, code style, and missing test coverage).

Powered by **FastAPI** and **Anthropic Claude**.

---

## Features

### 🌐 Phase 1: Interactive Web App
- **Multi-Mode Analysis**:
  - **Full Code Mode**: Ingests code snippets and attributes findings to exact 1-based source line numbers.
  - **Git Diff Mode**: Parses unified diffs, tracking new-file line numbers from `@@ -a,b +c,d @@` hunk headers.
- **Strict Structured Feedback**:
  - Validated with Pydantic into issues with `severity` (`high`, `medium`, `low`), `category` (`bug`, `security`, `performance`, `style`, `tests`), `line`, `title`, `explanation`, `suggested_fix`, and `confidence`.
  - Includes a concise overall summary of the review.
- **Prompt Injection Defense**:
  - Isolates user code and diffs in strict delimiters (`<user_code>` / `<user_diff>`).
  - Instructs the model to treat all enclosed content strictly as passive data, never as commands or instructions.
- **Resilient AI Engine**:
  - Handles malformed LLM responses with an automated 1-shot self-correcting retry.
  - Enforces API timeouts and returns friendly error messages for rate limits, token truncations, and connection issues.
  - Returns HTTP 413 for inputs exceeding size limits (60,000 characters).
- **Clean Single-Page UI**:
  - Engineered with vanilla HTML5, CSS3, and JavaScript (zero build tools or npm dependencies needed).
  - Dark editor panel (`#0B1220`) with dynamic line numbers, Tab-indentation support, and live character counter.
  - Displays confidence score badges on cards when present (`95% confidence`).
  - Filter issues by severity (`All`, `High`, `Medium`, `Low`).
  - Color-coded severity chips with explicit text, category tags, line indicators, and 1-click "Copy fix" buttons.
  - Responsive layout (stacks to a single column on mobile).

### 🤖 Phase 2: GitHub PR Review Bot
- **Automated Pull Request Reviews**:
  - GitHub App integration listening for `pull_request` webhooks (`opened`, `synchronize`, `reopened`).
  - Immediate webhook response (`202 Accepted`) with asynchronous review execution via FastAPI background tasks.
- **Webhook Security**:
  - HMAC-SHA256 signature verification (`X-Hub-Signature-256`) using constant-time comparison. Rejects forged requests with `401 Unauthorized`.
- **Smart Diff & Line Mapping**:
  - Parses diff hunks to identify valid new-file lines (`+` and context).
  - Emits GitHub inline comments strictly on diff lines, preventing GitHub's `422 Unprocessable Entity` rejection.
  - File-level issues or comments on non-modified lines are placed into the PR review summary body.
  - Automatic fallback: If GitHub rejects inline comments, the review falls back to posting the summary with all comments included in the markdown body.
- **Safety & Noise Filtering**:
  - Skips draft PRs and PRs authored by bots.
  - Automatically ignores lockfiles (`package-lock.json`, `poetry.lock`, etc.), vendor directories, binary files, and deleted files.
  - File count (`MAX_FILES`) and total diff character (`MAX_DIFF_CHARS`) caps.
  - In-memory commit SHA lock prevents duplicate or concurrent reviews on the same commit.
  - Suggested fixes use standard fenced code blocks rather than GitHub suggestion blocks to avoid accidental one-click breakage.
- **Standalone CLI**:
  - `python -m github_bot.review_pr <owner/repo> <pr_number> [--dry-run]` to test reviews locally without waiting for webhooks.

### 🧠 Phase 3: Smarter & Less Noisy Reviews
- **Rich Repository Context**:
  - Automatically incorporates PR title, description, base README excerpt, and surrounding code windows into the review prompt.
  - Enclosed in strict XML delimiters (`<pr_metadata>`, `<readme_context>`, `<surrounding_code>`) with prompt injection defenses.
- **Safe Static Analysis Hints (Bandit & Semgrep)**:
  - Runs local static analysis tools in isolated temporary directories with strict timeouts.
  - Semgrep runs with `--metrics=off` and built-in rulesets (`p/default`); if offline or rule downloads fail, it skips gracefully without blocking the review.
  - Never executes code from the PR or loads repo-provided configuration files / plugins.
  - Validates file paths to prevent directory traversal (`..` and absolute paths).
  - Keeps **only** findings on modified lines and feeds them to Claude as unverified hints to check against the code.
- **Large Diff Chunking & Deduplication**:
  - Automatically splits diffs exceeding 12,000 characters along `@@` hunk boundaries while retaining diff headers.
  - Reviews chunks separately and deduplicates overlapping findings (same file/line/category or near-identical titles), keeping the highest-ranked issue.
  - Prioritizes security-sensitive files (auth, db, crypto, payment) under constrained `MAX_LLM_CALLS_PER_PR` budgets.
- **Per-Repository Configuration (`.reviewer.yml`)**:
  - Always loaded from the **target BASE branch** (e.g. `main`), preventing pull requests from tampering with review rules.
  - Supports `min_severity`, `enabled_categories`, `ignore_path_globs`, `max_comments`, `min_confidence`, and `custom_guidelines`.
  - Validated with Pydantic; falls back safely to defaults with a summary notice if the file is invalid.
- **Noise Reduction & Confidence Thresholds**:
  - Filters out trivial style nits unless the style category is explicitly requested.
  - Drops issues below configurable `min_confidence` (default `0.5`).
  - Caps inline comments per PR, prioritizing by severity and confidence.
- **Fork PR Resilience**:
  - If fetching full file contents from a fork fails, falls back gracefully to patch-only analysis.

---

## Project Structure

```text
.
├── .env.example                  # Environment variables template
├── .gitignore                    # Git ignore rules
├── requirements.txt              # Python dependencies
├── README.md                     # Project documentation
├── analysis/                     # Phase 3: Safe static analysis
│   ├── __init__.py
│   ├── models.py                 # StaticAnalysisFinding dataclass
│   ├── bandit_runner.py          # Bandit runner with JSON parsing
│   ├── semgrep_runner.py         # Semgrep runner with --metrics=off
│   └── runner.py                 # Tempdir isolation & changed-lines filter
├── app/
│   ├── __init__.py
│   ├── config.py                 # Configuration and environment settings
│   ├── schemas.py                # FastAPI Pydantic schemas (ReviewRequest, ReviewResponse)
│   ├── main.py                   # FastAPI application, routes, static mount, webhook mount
│   └── static/
│       ├── index.html            # Web UI markup
│       ├── style.css             # UI styling & confidence badge styling
│       └── app.js                # Vanilla JS: editor sync, API calls, dynamic rendering
├── github_bot/
│   ├── __init__.py
│   ├── auth.py                   # GitHub App JWT & installation token manager
│   ├── client.py                 # Lightweight REST client with httpx
│   ├── config_loader.py          # Phase 3: .reviewer.yml loader & schema validation
│   ├── diff_utils.py             # Patch parsing, commentable lines, file exclusions
│   ├── publisher.py              # Review formatting, comment prioritization & 422 fallback
│   ├── service.py                # End-to-end PR review orchestration & deduplication
│   ├── webhook.py                # HMAC signature verification & webhook router
│   └── review_pr.py              # CLI test runner
├── reviewer/
│   ├── __init__.py
│   ├── chunking.py               # Phase 3: Patch chunker, hunk splitter & deduplicator
│   ├── engine.py                 # Anthropic Claude engine with 1-shot retry logic
│   ├── models.py                 # Domain models with confidence scoring
│   └── prompts.py                # Delimited prompt structure (PR, README, Hints)
└── tests/
    ├── test_reviewer.py          # Phase 1 tests (prompts, engine retry, FastAPI routes)
    ├── test_github_bot.py        # Phase 2 tests (signatures, diff parsing, fallbacks, CLI)
    └── test_phase3.py            # Phase 3 tests (chunking, .reviewer.yml, hints, dedupe)
```

---

## Installing Static Analysis Tools (Optional)

Bandit and Semgrep are optional. If they are not installed or in your `PATH`, the review bot logs an informational message and proceeds with full AI code review without hints:

### Bandit (Python Security AST Linter)
```bash
pip install bandit
```

### Semgrep (Multi-Language Pattern Scanner)
```bash
# Via pip:
pip install semgrep

# Or on macOS via Homebrew:
brew install semgrep
```

---

## Per-Repository Configuration (`.reviewer.yml`)

Place a `.reviewer.yml` file in the root of your repository on your **default / base branch** (e.g. `main`).

### Annotated Example `.reviewer.yml`

```yaml
# .reviewer.yml - AI Code Reviewer configuration

# Minimum severity required to report an issue: 'low', 'medium', or 'high' (default: low)
min_severity: low

# Enabled review categories (omit 'style' to eliminate style nits)
enabled_categories:
  - bug
  - security
  - performance
  - tests
  # - style

# Path glob patterns to exclude from code review
ignore_path_globs:
  - "tests/**"
  - "docs/**"
  - "migrations/**"
  - "**/*.generated.py"

# Maximum number of inline comments posted per PR review (default: 15)
max_comments: 10

# Minimum confidence score (0.0 to 1.0) required to report an issue (default: 0.5)
min_confidence: 0.6

# Custom guidelines appended to the prompt (size-capped to 2,000 characters)
custom_guidelines: |
  - All SQL queries must use parameterized placeholders; never format raw strings.
  - Require type annotations for all new public functions.
  - Ensure any new REST API endpoints include docstrings documenting error codes.
```

---

## Getting Started

### 1. Prerequisites

- Python 3.9+ (Python 3.11+ recommended)
- An [Anthropic API Key](https://console.anthropic.com/)
- (Optional) GitHub App credentials

### 2. Setup Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure Environment Variables

```bash
cp .env.example .env
```

Edit `.env`:
```env
# Anthropic LLM
ANTHROPIC_API_KEY=sk-ant-api03-...
ANTHROPIC_MODEL=claude-sonnet-5-5

# GitHub App (Required for Phase 2 & 3 bot)
GITHUB_APP_ID=123456
GITHUB_PRIVATE_KEY_PATH=/path/to/your-app-private-key.pem
GITHUB_WEBHOOK_SECRET=your_webhook_secret_here

# Bot Limits & Safeguards
MIN_SEVERITY=low
MAX_FILES=20
MAX_DIFF_CHARS=100000
MAX_LLM_CALLS_PER_PR=8
MIN_CONFIDENCE=0.5
```

### 4. Run the Web Server

```bash
uvicorn app.main:app --reload --port 8000
```

- Open the Web UI: **[http://localhost:8000](http://localhost:8000)**
- Interactive API Docs: **[http://localhost:8000/docs](http://localhost:8000/docs)**
- GitHub Webhook Endpoint: **`POST http://localhost:8000/webhooks/github`**

---

## Testing with the CLI

You can run automated reviews directly against any pull request:

```bash
# Safe simulation: prints review body, inline comments, and hints without posting to GitHub
python -m github_bot.review_pr owner/repo 1 --dry-run

# Live review: publishes review directly to the PR
python -m github_bot.review_pr owner/repo 1

# Force re-review: bypasses the head commit SHA deduplication cache
python -m github_bot.review_pr owner/repo 1 --force
```

---

## Running the Automated Test Suite

Run all unit and integration tests across all three phases:

```bash
.venv/bin/python3 -m unittest discover tests
```

Expected output:
```text
Ran 48 tests in 0.06s
OK
```
