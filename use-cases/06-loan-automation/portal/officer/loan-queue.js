import"./modulepreload-polyfill-B5Qt9EMX.js";import{r as shell}from"./employee-shell-H9ktEmdl.js";

const REVIEW_API = "/loan-review";

(async () => {
  const container = await shell("Loan Queue", "loan-queue");

  container.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:var(--nb-space-lg)">
      <div>
        <h2 style="font-size:1.25rem;font-weight:600;margin:0">📋 Loan Review Queue</h2>
        <p style="color:var(--nb-text-secondary);font-size:0.85rem;margin:4px 0 0">Manage loan applications</p>
      </div>
      <button id="refreshBtn" class="nb-btn nb-btn-secondary" style="font-size:0.8rem">↻ Refresh</button>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:var(--nb-space-md)">
      <button id="tabPending" onclick="switchTab('pending')" style="padding:6px 16px;border-radius:6px;border:1px solid #1a3a5c;background:#1a3a5c;color:#fff;cursor:pointer;font-size:0.8rem;font-weight:600">⏳ Pending Review</button>
      <button id="tabAll" onclick="switchTab('all')" style="padding:6px 16px;border-radius:6px;border:1px solid #ccc;background:#fff;color:#333;cursor:pointer;font-size:0.8rem;font-weight:500">📋 All Applications</button>
    </div>
    <div id="queueBody">Loading...</div>
  `;

  let currentTab = 'pending';

  window.switchTab = (tab) => {
    currentTab = tab;
    document.getElementById('tabPending').style.cssText = tab === 'pending'
      ? 'padding:6px 16px;border-radius:6px;border:1px solid #1a3a5c;background:#1a3a5c;color:#fff;cursor:pointer;font-size:0.8rem;font-weight:600'
      : 'padding:6px 16px;border-radius:6px;border:1px solid #ccc;background:#fff;color:#333;cursor:pointer;font-size:0.8rem;font-weight:500';
    document.getElementById('tabAll').style.cssText = tab === 'all'
      ? 'padding:6px 16px;border-radius:6px;border:1px solid #1a3a5c;background:#1a3a5c;color:#fff;cursor:pointer;font-size:0.8rem;font-weight:600'
      : 'padding:6px 16px;border-radius:6px;border:1px solid #ccc;background:#fff;color:#333;cursor:pointer;font-size:0.8rem;font-weight:500';
    loadQueue();
  };

  function statusBadge(status, auto) {
    const colors = {
      'PENDING_REVIEW': 'background:#fff3cd;color:#856404',
      'APPROVED': 'background:#d4edda;color:#155724',
      'REJECTED': 'background:#f8d7da;color:#721c24',
      'processing': 'background:#cce5ff;color:#004085',
      'SUBMITTED': 'background:#e2e3e5;color:#383d41',
    };
    const style = colors[status] || 'background:#e2e3e5;color:#383d41';
    const label = (status || '').replace('_', ' ');
    const autoTag = auto ? ' <span style="font-size:0.65rem;opacity:0.8">⚡AUTO</span>' : '';
    return `<span style="${style};padding:2px 8px;border-radius:10px;font-size:0.75rem;font-weight:600">${label}${autoTag}</span>`;
  }

  function loanTypeBadge(type) {
    if (type === 'instant_money') return '<span style="background:#e8f5e9;color:#2e7d32;padding:2px 8px;border-radius:10px;font-size:0.7rem;font-weight:600">⚡ Instant Money</span>';
    return '<span style="background:#e3f2fd;color:#1565c0;padding:2px 8px;border-radius:10px;font-size:0.7rem;font-weight:600">👤 Personal</span>';
  }

  function renderCard(a, showActions) {
    const isPending = a.status === 'PENDING_REVIEW';
    const actions = showActions && isPending ? `
      <div style="display:flex;gap:8px;flex-shrink:0">
        <a href="/employee/credit/application-review.html?id=${a.application_id}"
           style="background:#1a3a5c;color:#fff;padding:8px 16px;border-radius:6px;font-size:0.85rem;font-weight:500;text-decoration:none;display:inline-flex;align-items:center">
          🔍 Review
        </a>
        <button onclick="decide('${a.application_id}','APPROVED')"
          style="background:#38A169;color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:0.85rem;font-weight:500">✓ Approve</button>
        <button onclick="decide('${a.application_id}','REJECTED')"
          style="background:#E53E3E;color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:0.85rem;font-weight:500">✗ Reject</button>
      </div>` : `
      <a href="/employee/credit/application-review.html?id=${a.application_id}"
         style="background:#6c757d;color:#fff;padding:8px 16px;border-radius:6px;font-size:0.85rem;font-weight:500;text-decoration:none;display:inline-flex;align-items:center">
        🔍 View Report
      </a>`;

    return `
      <div class="nb-card" style="margin-bottom:var(--nb-space-md);padding:var(--nb-space-lg)">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px">
          <div>
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
              <a href="/employee/credit/application-review.html?id=${a.application_id}"
                 style="font-weight:600;font-size:1rem;color:#1a3a5c;text-decoration:none;border-bottom:1px dashed #1a3a5c">${a.application_id}</a>
              ${loanTypeBadge(a.loan_type)}
              ${statusBadge(a.status, a.auto_decided)}
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
            ${a.decision_reason ? `<div style="color:var(--nb-text-secondary);font-size:0.8rem;margin-top:4px;font-style:italic">${a.decision_reason}</div>` : ""}
          </div>
          ${actions}
        </div>
      </div>`;
  }

  async function loadQueue() {
    document.getElementById("queueBody").innerHTML = `<div style="color:var(--nb-text-muted);padding:var(--nb-space-lg)">Loading applications...</div>`;
    try {
      const endpoint = currentTab === 'pending' ? '/loans/pending' : '/loans/all';
      const res = await fetch(`${REVIEW_API}${endpoint}`, { credentials: "include" });
      const data = await res.json();
      const apps = data.applications || [];

      if (!apps.length) {
        document.getElementById("queueBody").innerHTML = `
          <div class="nb-card" style="text-align:center;padding:var(--nb-space-2xl);color:var(--nb-text-muted)">
            ${currentTab === 'pending' ? '✅ No pending applications' : 'No applications found'}
          </div>`;
        return;
      }

      document.getElementById("queueBody").innerHTML = apps.map(a => renderCard(a, currentTab === 'pending')).join("");
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
