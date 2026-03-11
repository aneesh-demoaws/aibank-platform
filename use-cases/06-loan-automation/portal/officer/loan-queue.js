import"./modulepreload-polyfill-B5Qt9EMX.js";import{r as shell}from"./employee-shell-H9ktEmdl.js";

const REVIEW_API = "/loan-review";

(async () => {
  const container = await shell("Loan Queue", "loan-queue");

  container.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:var(--nb-space-lg)">
      <div>
        <h2 style="font-size:1.25rem;font-weight:600;margin:0">📋 Loan Review Queue</h2>
        <p style="color:var(--nb-text-secondary);font-size:0.85rem;margin:4px 0 0">Pending applications requiring officer review</p>
      </div>
      <button id="refreshBtn" class="nb-btn nb-btn-secondary" style="font-size:0.8rem">↻ Refresh</button>
    </div>
    <div id="queueBody">Loading...</div>
  `;

  async function loadQueue() {
    document.getElementById("queueBody").innerHTML = `<div style="color:var(--nb-text-muted);padding:var(--nb-space-lg)">Loading applications...</div>`;
    try {
      const res = await fetch(`${REVIEW_API}/loans/pending`, { credentials: "include" });
      const data = await res.json();
      const apps = data.applications || [];

      if (!apps.length) {
        document.getElementById("queueBody").innerHTML = `
          <div class="nb-card" style="text-align:center;padding:var(--nb-space-2xl);color:var(--nb-text-muted)">
            ✅ No pending applications
          </div>`;
        return;
      }

      document.getElementById("queueBody").innerHTML = apps.map(a => `
        <div class="nb-card" style="margin-bottom:var(--nb-space-md);padding:var(--nb-space-lg)">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px">
            <div>
              <div style="font-weight:600;font-size:1rem">
                <a href="/employee/credit/application-review.html?id=${a.application_id}"
                   style="color:#1a3a5c;text-decoration:none;border-bottom:1px dashed #1a3a5c"
                   title="Open full review">${a.application_id}</a>
              </div>
              <div style="color:var(--nb-text-secondary);font-size:0.85rem;margin-top:4px">
                Customer: <strong>${a.customer_id}</strong> &nbsp;·&nbsp;
                ${a.employer_name ? `Employer: <strong>${a.employer_name}</strong> &nbsp;·&nbsp;` : ""}
                Salary: <strong>${a.basic_salary ? a.basic_salary + " BHD" : "—"}</strong>
              </div>
              <div style="color:var(--nb-text-secondary);font-size:0.85rem;margin-top:2px">
                Amount: <strong>${a.amount} BHD</strong> &nbsp;·&nbsp;
                Tenure: <strong>${a.tenure_months} months</strong> &nbsp;·&nbsp;
                Submitted: <strong>${a.submitted_at ? new Date(a.submitted_at).toLocaleDateString("en-BH") : "—"}</strong>
              </div>
            </div>
            <div style="display:flex;gap:8px;flex-shrink:0">
              <a href="/employee/credit/application-review.html?id=${a.application_id}"
                 style="background:#1a3a5c;color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:0.85rem;font-weight:500;text-decoration:none;display:inline-flex;align-items:center">
                🔍 Review
              </a>
              <button onclick="decide('${a.application_id}','APPROVED')"
                style="background:#38A169;color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:0.85rem;font-weight:500">
                ✓ Approve
              </button>
              <button onclick="decide('${a.application_id}','REJECTED')"
                style="background:#E53E3E;color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:0.85rem;font-weight:500">
                ✗ Reject
              </button>
            </div>
          </div>
        </div>
      `).join("");
    } catch (e) {
      document.getElementById("queueBody").innerHTML = `<div style="color:#E53E3E;padding:var(--nb-space-lg)">Error loading queue: ${e.message}</div>`;
    }
  }

  window.decide = async (appId, decision) => {
    const notes = prompt(`${decision} — add notes (optional):`);
    if (notes === null) return;
    try {
      const res = await fetch(`${REVIEW_API}/decisions`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ application_id: appId, decision, notes: notes || "" }),
      });
      const data = await res.json();
      if (data.success) {
        alert(`✅ Application ${appId} ${decision}`);
        loadQueue();
      } else {
        alert(`Error: ${data.error}`);
      }
    } catch (e) {
      alert(`Error: ${e.message}`);
    }
  };

  document.getElementById("refreshBtn").addEventListener("click", loadQueue);
  loadQueue();
})();
