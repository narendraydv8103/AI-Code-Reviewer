/**
 * AI Code Reviewer - Frontend Application
 */

document.addEventListener("DOMContentLoaded", () => {
  // Application State
  const state = {
    mode: "code",
    language: "python",
    maxChars: 60000,
    allIssues: [],
    currentFilter: "all",
    accessToken: "",
    sessionFeedback: new Map(), // issue_id -> 'helpful' | 'not_helpful'
  };

  function getAuthHeaders() {
    const headers = {
      "Content-Type": "application/json",
    };
    const token = state.accessToken || sessionStorage.getItem("access_token");
    if (token) {
      headers["X-API-Key"] = token;
    }
    return headers;
  }

  // DOM Elements
  const codeInput = document.getElementById("code-input");
  const lineGutter = document.getElementById("line-gutter");
  const charCounter = document.getElementById("char-counter");
  const languageSelect = document.getElementById("language-select");
  const modeCodeBtn = document.getElementById("mode-code-btn");
  const modeDiffBtn = document.getElementById("mode-diff-btn");
  const editorModeBadge = document.getElementById("editor-mode-badge");
  const editorPanelTitle = document.getElementById("editor-panel-title");
  const reviewBtn = document.getElementById("review-btn");
  const btnText = document.getElementById("btn-text");

  // Sample Buttons
  const loadSampleBtn = document.getElementById("load-sample-btn");
  const loadDiffSampleBtn = document.getElementById("load-diff-sample-btn");
  const clearBtn = document.getElementById("clear-btn");

  // Error Banner
  const errorBanner = document.getElementById("error-banner");
  const errorMessage = document.getElementById("error-message");
  const errorCloseBtn = document.getElementById("error-close-btn");

  // Results Containers
  const resultsEmptyState = document.getElementById("results-empty-state");
  const resultsLoadingState = document.getElementById("results-loading-state");
  const resultsContent = document.getElementById("results-content");
  const summaryText = document.getElementById("summary-text");
  const summaryVerdict = document.getElementById("summary-verdict");
  const statTotalChip = document.getElementById("stat-total-chip");
  const issuesList = document.getElementById("issues-list");

  // Filter Buttons
  const filterChips = document.querySelectorAll(".filter-chip");
  const countAll = document.getElementById("count-all");
  const countHigh = document.getElementById("count-high");
  const countMedium = document.getElementById("count-medium");
  const countLow = document.getElementById("count-low");

  // -------------------------------------------------------------
  // Sample Data Snippets
  // -------------------------------------------------------------
  const SAMPLE_BUGGY_CODE = `import sqlite3
from typing import List

# Database helper with security and logic issues
def get_user_profile(user_id: str, tags: List[str] = []):
    """Fetches user details and appends default tags."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    
    # Security: SQL injection vulnerability via raw string formatting
    query = f"SELECT id, username, email FROM accounts WHERE id = '{user_id}'"
    cursor.execute(query)
    user = cursor.fetchone()
    
    # Logic bug: mutable default argument 'tags' retains state across calls
    tags.append("active")
    
    # Missing null check on database result
    result = {
        "id": user[0],
        "username": user[1],
        "email": user[2],
        "tags": tags
    }
    
    # Performance: connection is never closed
    return result
`;

  const SAMPLE_DIFF = `diff --git a/services/payment.py b/services/payment.py
index a1b2c3d..e4f5g6h 100644
--- a/services/payment.py
+++ b/services/payment.py
@@ -14,6 +14,14 @@ def process_transaction(user_id, amount, currency="USD"):
     if amount <= 0:
         raise ValueError("Transaction amount must be positive")
     
+    # Process discount coupon without input validation or type casting
+    coupon_code = get_active_coupon(user_id)
+    if coupon_code:
+        discount = calculate_discount(coupon_code, amount)
+        # Bug: off-by-one or negative balance possible if discount > amount
+        amount = amount - discount
+        record_coupon_usage(coupon_code, user_id)
+    
     gateway_response = charge_card(user_id, amount, currency)
     return gateway_response
`;

  // -------------------------------------------------------------
  // Initialize Client Configurations
  // -------------------------------------------------------------
  async function fetchConfig() {
    try {
      const res = await fetch("/api/config");
      if (res.ok) {
        const data = await res.json();
        if (data.max_code_chars) {
          state.maxChars = data.max_code_chars;
          updateCharCounter();
        }
        if (data.provider) {
          state.provider = data.provider;
          const providerName = data.provider === "gemini" ? "Google Gemini" : "Anthropic Claude";
          const subtext = document.getElementById("loading-subtext");
          if (subtext) {
            subtext.textContent = `${providerName} is analyzing logic, security vulnerabilities, edge cases, and missing tests.`;
          }
        }
      }
    } catch {
      // Fallback silently to defaults
    }
  }
  fetchConfig();

  // -------------------------------------------------------------
  // Editor & Gutter Line Number Sync
  // -------------------------------------------------------------
  function updateLineNumbers() {
    const text = codeInput.value;
    const lineCount = (text.split("\n")).length || 1;
    const lines = [];
    for (let i = 1; i <= lineCount; i++) {
      lines.push(i);
    }
    lineGutter.textContent = lines.join("\n");
  }

  function updateCharCounter() {
    const count = codeInput.value.length;
    charCounter.textContent = `${count.toLocaleString()} / ${state.maxChars.toLocaleString()} chars`;

    charCounter.classList.remove("warning", "danger");
    if (count > state.maxChars) {
      charCounter.classList.add("danger");
    } else if (count > state.maxChars * 0.85) {
      charCounter.classList.add("warning");
    }
  }

  // Synchronize vertical scroll between editor and gutter
  codeInput.addEventListener("scroll", () => {
    lineGutter.scrollTop = codeInput.scrollTop;
  });

  codeInput.addEventListener("input", () => {
    updateLineNumbers();
    updateCharCounter();
  });

  // Tab key indentation handling in textarea
  codeInput.addEventListener("keydown", (e) => {
    if (e.key === "Tab") {
      e.preventDefault();
      const start = codeInput.selectionStart;
      const end = codeInput.selectionEnd;

      // Insert 4 spaces
      codeInput.value =
        codeInput.value.substring(0, start) +
        "    " +
        codeInput.value.substring(end);

      codeInput.selectionStart = codeInput.selectionEnd = start + 4;
      updateLineNumbers();
      updateCharCounter();
    }
  });

  // -------------------------------------------------------------
  // Mode & Language Handling
  // -------------------------------------------------------------
  function setMode(newMode) {
    state.mode = newMode;
    if (newMode === "diff") {
      modeDiffBtn.classList.add("active");
      modeCodeBtn.classList.remove("active");
      editorModeBadge.textContent = "git diff";
      editorPanelTitle.textContent = "Git Diff";
      codeInput.placeholder = "Paste your unified git diff here (e.g. diff --git a/... b/...) or click 'Sample Diff'...";
    } else {
      modeCodeBtn.classList.add("active");
      modeDiffBtn.classList.remove("active");
      editorModeBadge.textContent = "full code";
      editorPanelTitle.textContent = "Source Code";
      codeInput.placeholder = "Paste your source code here or click 'Sample Code' to try an example...";
    }
  }

  modeCodeBtn.addEventListener("click", () => setMode("code"));
  modeDiffBtn.addEventListener("click", () => setMode("diff"));

  languageSelect.addEventListener("change", (e) => {
    state.language = e.target.value;
  });

  // Sample actions
  loadSampleBtn.addEventListener("click", () => {
    setMode("code");
    languageSelect.value = "python";
    state.language = "python";
    codeInput.value = SAMPLE_BUGGY_CODE.trim();
    updateLineNumbers();
    updateCharCounter();
  });

  loadDiffSampleBtn.addEventListener("click", () => {
    setMode("diff");
    languageSelect.value = "python";
    state.language = "python";
    codeInput.value = SAMPLE_DIFF.trim();
    updateLineNumbers();
    updateCharCounter();
  });

  clearBtn.addEventListener("click", () => {
    codeInput.value = "";
    updateLineNumbers();
    updateCharCounter();
    codeInput.focus();
  });

  // -------------------------------------------------------------
  // Error Notification Helpers
  // -------------------------------------------------------------
  function showError(msg) {
    errorMessage.textContent = msg;
    errorBanner.style.display = "flex";
    errorBanner.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function hideError() {
    errorBanner.style.display = "none";
  }

  errorCloseBtn.addEventListener("click", hideError);

  // -------------------------------------------------------------
  // Submit Code Review
  // -------------------------------------------------------------
  reviewBtn.addEventListener("click", async () => {
    hideError();
    const rawCode = codeInput.value;

    if (!rawCode.trim()) {
      showError("Please enter or paste code or a git diff to review.");
      codeInput.focus();
      return;
    }

    if (rawCode.length > state.maxChars) {
      showError(
        `Input size (${rawCode.length.toLocaleString()} characters) exceeds maximum allowed limit of ${state.maxChars.toLocaleString()} characters (HTTP 413).`
      );
      return;
    }

    // Set UI to loading state
    reviewBtn.disabled = true;
    reviewBtn.classList.add("loading");
    btnText.textContent = "Reviewing...";
    resultsEmptyState.style.display = "none";
    resultsContent.style.display = "none";
    resultsLoadingState.style.display = "flex";

    try {
      const response = await fetch("/api/review", {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({
          code: rawCode,
          language: state.language,
          mode: state.mode,
        }),
      });

      if (!response.ok) {
        let errMessage = `Error ${response.status}: ${response.statusText}`;
        try {
          const errData = await response.json();
          if (errData.detail) {
            errMessage = errData.detail;
          }
        } catch {
          // ignore json parse error on response
        }

        if (response.status === 413) {
          errMessage = `Request Entity Too Large (413): ${errMessage}`;
        } else if (response.status === 504) {
          errMessage = `Gateway Timeout (504): The review request timed out. Please try with a smaller snippet.`;
        }

        showError(errMessage);
        resultsLoadingState.style.display = "none";
        resultsEmptyState.style.display = "flex";
        return;
      }

      const data = await response.json();
      renderReviewResults(data);

    } catch (err) {
      showError(`Network connection error: ${err.message || "Failed to reach server"}`);
      resultsLoadingState.style.display = "none";
      resultsEmptyState.style.display = "flex";
    } finally {
      reviewBtn.disabled = false;
      reviewBtn.classList.remove("loading");
      btnText.textContent = "Review code";
    }
  });

  // -------------------------------------------------------------
  // Render Results
  // -------------------------------------------------------------
  function renderReviewResults(data) {
    resultsLoadingState.style.display = "none";
    resultsContent.style.display = "block";

    summaryText.textContent = data.summary || "No summary provided.";
    const total = (data.issues && data.issues.length) || 0;
    state.allIssues = data.issues || [];

    // Verdict heading
    if (total === 0) {
      summaryVerdict.textContent = "Clean Review (LGTM)";
      statTotalChip.textContent = "0 issues detected";
      statTotalChip.style.color = "#059669";
    } else {
      summaryVerdict.textContent = "Issues Detected";
      statTotalChip.textContent = `${total} issue${total === 1 ? "" : "s"} found`;
      statTotalChip.style.color = "var(--color-ink)";
    }

    // Calculate severity counts
    const counts = { all: total, high: 0, medium: 0, low: 0 };
    state.allIssues.forEach((issue) => {
      const sev = (issue.severity || "").toLowerCase();
      if (counts[sev] !== undefined) {
        counts[sev]++;
      }
    });

    countAll.textContent = counts.all;
    countHigh.textContent = counts.high;
    countMedium.textContent = counts.medium;
    countLow.textContent = counts.low;

    // Default filter to "all"
    setFilter("all");
  }

  // -------------------------------------------------------------
  // Filtering & Issue Cards Generation
  // -------------------------------------------------------------
  function setFilter(filter) {
    state.currentFilter = filter;
    filterChips.forEach((chip) => {
      if (chip.dataset.filter === filter) {
        chip.classList.add("active");
      } else {
        chip.classList.remove("active");
      }
    });

    renderIssueCards();
  }

  filterChips.forEach((chip) => {
    chip.addEventListener("click", () => {
      setFilter(chip.dataset.filter);
    });
  });

  function escapeHtml(str) {
    if (!str) return "";
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function renderIssueCards() {
    issuesList.innerHTML = "";

    const filtered = state.allIssues.filter((issue) => {
      if (state.currentFilter === "all") return true;
      return (issue.severity || "").toLowerCase() === state.currentFilter;
    });

    if (filtered.length === 0) {
      const emptyCard = document.createElement("div");
      emptyCard.className = "issue-card";
      emptyCard.innerHTML = `
        <div style="text-align: center; padding: 24px 12px; color: var(--color-muted);">
          <strong>No ${state.currentFilter === "all" ? "" : state.currentFilter} issues to display.</strong>
        </div>
      `;
      issuesList.appendChild(emptyCard);
      return;
    }

    filtered.forEach((issue, idx) => {
      const card = document.createElement("article");
      card.className = "issue-card";

      const sev = (issue.severity || "low").toLowerCase();
      const sevText = sev.toUpperCase(); // Explicit text requirement
      const lineText = issue.line !== null && issue.line !== undefined ? `Line ${issue.line}` : "General / Architectural";
      const categoryText = issue.category || "General";
      const confBadgeHtml = (issue.confidence !== null && issue.confidence !== undefined)
        ? `<span class="confidence-badge">${Math.round(issue.confidence * 100)}% confidence</span>`
        : "";

      let fixBlockHtml = "";
      if (issue.suggested_fix && issue.suggested_fix.trim()) {
        const fixId = `fix-code-${idx}`;
        fixBlockHtml = `
          <div class="suggested-fix-container">
            <div class="suggested-fix-header">
              <span class="suggested-fix-label">Suggested Fix</span>
              <button type="button" class="btn-copy-fix" data-target="${fixId}">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                  <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                </svg>
                <span>Copy fix</span>
              </button>
            </div>
            <pre class="suggested-fix-code"><code id="${fixId}">${escapeHtml(issue.suggested_fix)}</code></pre>
          </div>
        `;
      }

      card.innerHTML = `
        <div class="issue-meta-row">
          <span class="severity-chip ${sev}">
            <span class="severity-dot"></span>
            ${sevText}
          </span>
          <span class="category-badge">${escapeHtml(categoryText)}</span>
          <span class="line-badge">${escapeHtml(lineText)}</span>
          ${confBadgeHtml}
        </div>
        <h3 class="issue-title">${escapeHtml(issue.title)}</h3>
        <p class="issue-explanation">${escapeHtml(issue.explanation)}</p>
        ${fixBlockHtml}
      `;

      if (issue.id) {
        const feedbackDiv = document.createElement("div");
        feedbackDiv.className = "issue-feedback";
        feedbackDiv.dataset.issueId = issue.id;

        const promptSpan = document.createElement("span");
        promptSpan.className = "feedback-prompt";
        promptSpan.textContent = "Was this helpful?";

        const actionsDiv = document.createElement("div");
        actionsDiv.className = "feedback-actions";

        const helpfulBtn = document.createElement("button");
        helpfulBtn.type = "button";
        helpfulBtn.className = "btn-feedback btn-helpful";
        helpfulBtn.setAttribute("aria-label", "Mark issue as helpful");
        helpfulBtn.innerHTML = '<span aria-hidden="true">👍</span> <span>Helpful</span>';

        const notHelpfulBtn = document.createElement("button");
        notHelpfulBtn.type = "button";
        notHelpfulBtn.className = "btn-feedback btn-not-helpful";
        notHelpfulBtn.setAttribute("aria-label", "Mark issue as not helpful");
        notHelpfulBtn.innerHTML = '<span aria-hidden="true">👎</span> <span>Not helpful</span>';

        const statusSpan = document.createElement("span");
        statusSpan.className = "feedback-status";
        statusSpan.setAttribute("role", "status");
        statusSpan.setAttribute("aria-live", "polite");

        const existingFeedback = state.sessionFeedback.get(issue.id);
        if (existingFeedback) {
          helpfulBtn.disabled = true;
          notHelpfulBtn.disabled = true;
          if (existingFeedback === "helpful") {
            helpfulBtn.classList.add("active");
            helpfulBtn.setAttribute("aria-pressed", "true");
          } else {
            notHelpfulBtn.classList.add("active");
            notHelpfulBtn.setAttribute("aria-pressed", "true");
          }
          statusSpan.textContent = "Thanks for the feedback";
          statusSpan.className = "feedback-status success";
        }

        async function handleFeedbackClick(choice, btn) {
          if (state.sessionFeedback.has(issue.id) || helpfulBtn.disabled) {
            return;
          }

          helpfulBtn.disabled = true;
          notHelpfulBtn.disabled = true;
          statusSpan.textContent = "Submitting feedback...";
          statusSpan.className = "feedback-status in-flight";

          try {
            const res = await fetch("/api/feedback", {
              method: "POST",
              headers: getAuthHeaders(),
              body: JSON.stringify({
                issue_id: issue.id,
                feedback: choice,
              }),
            });

            if (!res.ok) {
              let errorMsg = `Submission failed (${res.status})`;
              try {
                const errData = await res.json();
                if (errData.detail) errorMsg = errData.detail;
              } catch {}
              statusSpan.textContent = errorMsg;
              statusSpan.className = "feedback-status error";
              helpfulBtn.disabled = false;
              notHelpfulBtn.disabled = false;
              return;
            }

            state.sessionFeedback.set(issue.id, choice);
            btn.classList.add("active");
            btn.setAttribute("aria-pressed", "true");
            statusSpan.textContent = "Thanks for the feedback";
            statusSpan.className = "feedback-status success";
          } catch (networkErr) {
            statusSpan.textContent = `Error: ${networkErr.message || "Failed to reach server"}`;
            statusSpan.className = "feedback-status error";
            helpfulBtn.disabled = false;
            notHelpfulBtn.disabled = false;
          }
        }

        helpfulBtn.addEventListener("click", () => handleFeedbackClick("helpful", helpfulBtn));
        notHelpfulBtn.addEventListener("click", () => handleFeedbackClick("not_helpful", notHelpfulBtn));

        actionsDiv.appendChild(helpfulBtn);
        actionsDiv.appendChild(notHelpfulBtn);

        feedbackDiv.appendChild(promptSpan);
        feedbackDiv.appendChild(actionsDiv);
        feedbackDiv.appendChild(statusSpan);

        card.appendChild(feedbackDiv);
      }

      issuesList.appendChild(card);
    });

    // Attach clipboard copy listeners
    document.querySelectorAll(".btn-copy-fix").forEach((btn) => {
      btn.addEventListener("click", () => {
        const targetId = btn.getAttribute("data-target");
        const codeElement = document.getElementById(targetId);
        if (codeElement) {
          navigator.clipboard.writeText(codeElement.innerText).then(() => {
            const originalHtml = btn.innerHTML;
            btn.classList.add("copied");
            btn.innerHTML = `
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
              <span>Copied!</span>
            `;
            setTimeout(() => {
              btn.classList.remove("copied");
              btn.innerHTML = originalHtml;
            }, 2000);
          });
        }
      });
    });
  }

  // Initial Line Numbers
  updateLineNumbers();
  updateCharCounter();
});

