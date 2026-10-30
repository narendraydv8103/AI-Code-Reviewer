/**
 * Dashboard Telemetry & Analytics Controller
 */

document.addEventListener("DOMContentLoaded", async () => {
  const kpiTotalReviews = document.getElementById("kpi-total-reviews");
  const kpiTodayReviews = document.getElementById("kpi-today-reviews");
  const kpiHelpfulnessRate = document.getElementById("kpi-helpfulness-rate");
  const kpiFeedbackCounts = document.getElementById("kpi-feedback-counts");
  const kpiTotalIssues = document.getElementById("kpi-total-issues");
  const kpiSeveritySplit = document.getElementById("kpi-severity-split");
  const kpiAvgLatency = document.getElementById("kpi-avg-latency");
  const recentReviewsTbody = document.getElementById("recent-reviews-tbody");

  try {
    const res = await fetch("/api/dashboard/stats");
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    }

    const data = await res.json();

    // 1. Render KPIs
    kpiTotalReviews.textContent = data.total_reviews.toLocaleString();
    kpiTodayReviews.textContent = `${data.today_reviews} executed today`;

    if (data.helpfulness_rate !== null) {
      kpiHelpfulnessRate.textContent = `${data.helpfulness_rate}%`;
      kpiFeedbackCounts.textContent = `${data.helpful_count} helpful &bull; ${data.not_helpful_count} unhelpful`;
    } else {
      kpiHelpfulnessRate.textContent = "N/A";
      kpiFeedbackCounts.textContent = "No user feedback yet";
    }

    kpiTotalIssues.textContent = data.total_issues.toLocaleString();
    const sev = data.severity_counts || { high: 0, medium: 0, low: 0 };
    kpiSeveritySplit.innerHTML = `<span class="chip-h">H: ${sev.high}</span> &bull; <span class="chip-m">M: ${sev.medium}</span> &bull; <span class="chip-l">L: ${sev.low}</span>`;

    kpiAvgLatency.textContent = `${data.avg_duration_ms.toLocaleString()} ms`;

    // 2. Render Chart 1: Daily Trends
    const trendsCtx = document.getElementById("reviewsTrendChart").getContext("2d");
    const dates = (data.daily_trends || []).map((t) => t.date);
    const counts = (data.daily_trends || []).map((t) => t.count);

    new Chart(trendsCtx, {
      type: "line",
      data: {
        labels: dates.length ? dates : ["Today"],
        datasets: [{
          label: "Reviews",
          data: counts.length ? counts : [data.today_reviews],
          borderColor: "#2563EB",
          backgroundColor: "rgba(37, 99, 235, 0.08)",
          fill: true,
          tension: 0.3,
          pointBackgroundColor: "#2563EB",
          pointRadius: 4,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { beginAtZero: true, ticks: { precision: 0 } },
          x: { grid: { display: false } }
        }
      }
    });

    // 3. Render Chart 2: Category Breakdown
    const catCtx = document.getElementById("categoryChart").getContext("2d");
    const categories = Object.keys(data.category_counts || {});
    const catValues = Object.values(data.category_counts || {});

    new Chart(catCtx, {
      type: "doughnut",
      data: {
        labels: categories.length ? categories.map((c) => c.toUpperCase()) : ["NO DATA"],
        datasets: [{
          data: catValues.length ? catValues : [1],
          backgroundColor: [
            "#DC2626", // Bug (red)
            "#EA580C", // Security (amber/orange)
            "#2563EB", // Performance (blue)
            "#059669", // Style (green)
            "#7C3AED", // Tests (purple)
          ]
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "right" }
        }
      }
    });

    // 4. Render Recent Reviews Table
    recentReviewsTbody.innerHTML = "";
    const reviews = data.recent_reviews || [];
    if (reviews.length === 0) {
      recentReviewsTbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted">No reviews recorded yet. Submit a review via the app or GitHub bot.</td></tr>`;
      return;
    }

    reviews.forEach((r) => {
      const tr = document.createElement("tr");
      const dateFormatted = new Date(r.created_at).toLocaleString();
      tr.innerHTML = `
        <td><span class="badge-source ${r.source.toLowerCase()}">${r.source}</span></td>
        <td><strong>${escapeHtml(r.target)}</strong></td>
        <td><code>${escapeHtml(r.model)}</code></td>
        <td>${r.duration_ms.toLocaleString()} ms</td>
        <td>
          <div class="badge-issue-counts">
            <span class="chip-h">${r.high}H</span>
            <span class="chip-m">${r.medium}M</span>
            <span class="chip-l">${r.low}L</span>
          </div>
        </td>
        <td class="text-muted">${dateFormatted}</td>
      `;
      recentReviewsTbody.appendChild(tr);
    });

  } catch (err) {
    recentReviewsTbody.innerHTML = `<tr><td colspan="6" class="text-center" style="color: #B42318;">Error loading metrics: ${err.message}</td></tr>`;
  }
});

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

