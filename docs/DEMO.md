# AI Code Reviewer - 3–5 Minute Product Demo Script

This script walks through an end-to-end demonstration of **AI Code Reviewer** for team members, stakeholders, or recorded technical presentations.

---

## Prerequisites & Quick Setup

Start the local server (or run via Docker):

```bash
# Option A: Local Python
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Option B: Docker Compose
docker compose up -d
```

Open your browser to:
- **Landing Page:** [http://localhost:8000/](http://localhost:8000/)
- **Reviewer App:** [http://localhost:8000/app](http://localhost:8000/app)
- **Analytics Dashboard:** [http://localhost:8000/dashboard](http://localhost:8000/dashboard)

---

## Act 1: The Interactive Web Workspace (1.5 Minutes)

**Presenter Says:**
> *"Welcome to AI Code Reviewer. It functions as an automated senior staff engineer on your team, catching critical vulnerabilities, performance traps, and subtle logic errors before code merges. Let's start with the interactive developer workspace."*

### Step 1: Navigate to the Workspace
1. Go to [http://localhost:8000/app](http://localhost:8000/app).
2. Point out the dark code editor, line gutters, character counter, and language selector.

### Step 2: Paste Buggy Code with Deliberate SQL Injection
Paste the following sample into the editor (or click the **"Sample Buggy Code"** button):

```python
import sqlite3
from typing import List

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
    
    return {
        "id": user[0],
        "username": user[1],
        "email": user[2],
        "tags": tags
    }
```

### Step 3: Trigger the Review
1. Click **"Review code"**.
2. Point out the loading state and fast response latency.
3. Show the results panel:
   - **Executive Summary:** Highlights critical concerns.
   - **Severity Chips:** High (`CRITICAL / HIGH`), Medium, and Low counts.
   - **Filter Controls:** Click "HIGH" to instantly isolate the SQL injection defect.
   - **Suggested Fix:** Click the **"Copy fix"** button to copy the parameterized query replacement.

---

## Act 2: Developer Feedback Loop (1 Minute)

**Presenter Says:**
> *"Developer trust requires continuous calibration. Every issue card includes immediate one-click feedback buttons so developers can confirm helpful findings or flag false positives."*

### Step 1: Submit Feedback
1. Scroll to the SQL injection card.
2. In the card footer, locate the **"👍 Helpful"** and **"👎 Not helpful"** buttons.
3. Click **"👍 Helpful"**.
4. Highlight that:
   - Both buttons temporarily disable while in flight.
   - The button highlights green with a check confirmation (`Thanks for the feedback`).
   - Duplicate submissions are prevented for the session.

---

## Act 3: Unified Git Diff Review (1 Minute)

**Presenter Says:**
> *"Reviewing whole files is great, but code reviews live in git diffs. AI Code Reviewer understands unified diffs, hunk boundaries, and maps comments to exact new-file line numbers."*

### Step 1: Switch to Diff Mode
1. Click the **"Diff"** mode button at the top of the editor.
2. Paste the following unified diff (or click **"Sample Diff"**):

```diff
diff --git a/services/payment.py b/services/payment.py
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
```

3. Click **"Review code"**.
4. Observe how the engine accurately flags the negative balance risk mapped directly to the diff line.

---

## Act 4: GitHub PR Bot Automation (30 Seconds)

**Presenter Says:**
> *"In production, AI Code Reviewer acts as a GitHub App. It listens for `pull_request` webhooks, runs Bandit and Semgrep static analysis, merges findings with Claude Sonnet 5.5, and publishes inline review comments directly on GitHub pull requests."*

1. Explain the fallback mechanism: If GitHub rejects inline comments (HTTP 422 line-out-of-diff), the bot automatically falls back to an exhaustive Markdown summary review so developers never lose feedback.
2. Point to repository configuration via `.reviewer.yml` for custom rules and path ignore globs.

---

## Act 5: Real-Time Observability Dashboard (30 Seconds)

**Presenter Says:**
> *"Engineering managers and platform teams can monitor review volume, latency, and model helpfulness in the built-in analytics dashboard."*

1. Open [http://localhost:8000/dashboard](http://localhost:8000/dashboard).
2. Point out:
   - **Total Reviews & Daily Volume:** Track team adoption over time.
   - **Helpfulness Rate:** Live percentage of developer feedback (`X helpful · Y not helpful`).
   - **Issue Category Breakdown:** Chart showing security vs bug vs performance distribution.
   - **Latency KPI:** Real-time median and average engine latency.

---

## Conclusion

**Presenter Says:**
> *"That is AI Code Reviewer: zero friction for developers in the web UI, automated CI/CD guardrails via GitHub PR bot, and full observability for the team."*

