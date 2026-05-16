import"./modulepreload-polyfill-B5Qt9EMX.js";import{r as shell}from"./employee-shell-3VBpu83q.js";

const REVIEW_API = "/loan-review";

const STATUS_BADGES = {
  draft: '#94a3b8', submitted: '#2563eb', processing: '#d97706',
  underwriting: '#d97706', manual_review: '#dc2626', approved: '#16a34a',
  rejected: '#dc2626', disbursed: '#16a34a', cancelled: '#64748b'
};

(async () => {
  const container = await shell("Loan Queue", "loan-queue");

  container.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:12px">
      <div>
        <h2 style="font-size:1.25rem;font-weight:600;margin:0">📋 Loan Applications</h2>
        <p style="color:var(--nb-text-secondary);font-size:0.85rem;margin:4px 0 0">All loan applications across all statuses</p>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <select id="statusFilter" style="padding:8px 12px;border:1px solid var(--nb-border);border-radius:6px;font-size:0.85rem">
          <option value="">All Statuses</option>
          <option value="manual_review">Pending Review</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
          <option value="processing">Processing</option>
          <option value="underwriting">Underwriting</option>
          <option value="disbursed">Disbursed</option>
        </select>
        <input type="text" id="searchBox" placeholder="🔍 Search customer/ID..." style="padding:8px 12px;border:1px solid var(--nb-border);border-radius:6px;font-size:0.85rem;width:220px">
        <button id="refreshBtn" class="nb-btn nb-btn-secondary" style="font-size:0.85rem">↻ Refresh</button>
      </div>
    </div>
    <div id="statsBar" style="display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap"></div>
    <div id="queueBody"><div style="padding:20px;color:var(--nb-text-muted)">Loading applications...</div></div>
  `;

  let allApps = [];

  function renderStats(apps) {
    const counts = {};
    apps.forEach(a => counts[a.status] = (counts[a.status] || 0) + 1);
    const total = apps.length;
    const tile = (val, label, color, filter) => `<div class="nb-card stat-tile" data-filter="${filter}" style="padding:12px 18px;flex:1;min-width:140px;cursor:pointer;border:2px solid transparent;transition:all 0.15s"><div style="font-size:1.4rem;font-weight:700;color:${color}">${val}</div><div style="font-size:0.72rem;color:var(--nb-text-muted);text-transform:uppercase">${label}</div></div>`;
    document.getElementById("statsBar").innerHTML =
      tile(total, 'Total Applications', 'var(--nb-accent)', '') +
      tile(counts.manual_review||0, 'Pending Review', '#dc2626', 'manual_review') +
      tile((counts.approved||0)+(counts.disbursed||0), 'Approved', '#16a34a', 'approved') +
      tile(counts.rejected||0, 'Rejected', '#dc2626', 'rejected') +
      tile((counts.processing||0)+(counts.underwriting||0), 'In Progress', '#d97706', 'in_progress');

    // Wire up clicks
    document.querySelectorAll('.stat-tile').forEach(el => {
      el.addEventListener('click', () => {
        const f = el.dataset.filter;
        document.querySelectorAll('.stat-tile').forEach(t => t.style.border='2px solid transparent');
        el.style.border = '2px solid var(--nb-accent)';
        const select = document.getElementById('statusFilter');
        // Reset combined filters
        if (f === 'in_progress' || f === 'approved') {
          // Custom filter: include multiple statuses
          window._customFilter = f === 'in_progress' ? ['processing','underwriting'] : ['approved','disbursed'];
          select.value = '';
        } else {
          window._customFilter = null;
          select.value = f || '';
        }
        applyFilters();
      });
    });
  }

  function displayStatus(a) {
    // Map status → display label (decision_type drives a separate AUTO badge)
    const status = (a.status || '').toLowerCase();
    if (status === 'approved' || status === 'disbursed') return 'APPROVED';
    if (status === 'rejected') return 'REJECTED';
    if (status === 'manual_review') return 'PENDING REVIEW';
    if (status === 'underwriting') return 'UNDERWRITING';
    if (status === 'processing') return 'PROCESSING';
    if (status === 'submitted') return 'SUBMITTED';
    return (a.status || '?').replace(/_/g,' ').toUpperCase();
  }

  function isAuto(a) {
    const dt = (a.decision_type || '').toLowerCase();
    return dt === 'auto_approve' || dt === 'auto_decline';
  }

  function renderTable(apps) {
    if (!apps.length) {
      document.getElementById("queueBody").innerHTML = `<div class="nb-card" style="text-align:center;padding:40px;color:var(--nb-text-muted)">No applications found</div>`;
      return;
    }
    const rows = apps.map(a => {
      const badge = STATUS_BADGES[a.status] || '#94a3b8';
      const customer = (a.first_name || '') + ' ' + (a.last_name || '');
      return `
        <tr style="border-bottom:1px solid var(--nb-border);cursor:pointer" onclick="window.location.href='/employee/credit/application-review.html?id=${a.application_id}'">
          <td style="padding:12px 8px;font-family:monospace;font-size:0.78rem">${a.application_id}</td>
          <td style="padding:12px 8px"><strong>${customer.trim() || '?'}</strong><br><span style="color:var(--nb-text-muted);font-size:0.72rem;font-family:monospace">${a.customer_id}</span> <button onclick="event.stopPropagation();copyCustomerId('${a.customer_id}')" title="Copy & filter by this customer" style="background:none;border:none;color:var(--nb-accent);cursor:pointer;font-size:0.75rem;padding:2px 4px">📋</button></td>
          <td style="padding:12px 8px;text-transform:capitalize">${(a.loan_type||'').replace('_',' ')}</td>
          <td style="padding:12px 8px;text-align:right">BHD ${(parseFloat(a.amount)||0).toLocaleString('en',{minimumFractionDigits:0,maximumFractionDigits:0})}</td>
          <td style="padding:12px 8px;text-align:center">${a.duration || a.tenure_months || '-'} mo</td>
          <td style="padding:12px 8px;text-align:center">${a.underwriting_score && a.underwriting_score !== '' ? a.underwriting_score : '-'}</td>
          <td style="padding:12px 8px"><div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap"><span style="background:${badge};color:white;padding:3px 10px;border-radius:12px;font-size:0.72rem;font-weight:600;letter-spacing:0.3px">${displayStatus(a)}</span>${isAuto(a) ? '<span style="background:#1e293b;color:#fbbf24;padding:2px 7px;border-radius:4px;font-size:0.62rem;font-weight:700;letter-spacing:0.4px;border:1px solid #fbbf24">⚡ AUTO</span>' : ''}</div>${a.disbursement_status === 'success' ? '<div style="font-size:0.65rem;color:#16a34a;font-weight:600;margin-top:4px">💰 Disbursed</div>' : (a.disbursement_status === 'failed' ? '<div style="font-size:0.65rem;color:#dc2626;font-weight:600;margin-top:4px">⚠ Disbursement Failed</div>' : (a.disbursement_status === 'reversed' ? '<div style="font-size:0.65rem;color:var(--nb-text-muted);margin-top:4px">↩ Reversed</div>' : ''))}</td>
          <td style="padding:12px 8px;font-size:0.78rem;color:var(--nb-text-muted)">${a.created_at ? new Date(a.created_at).toLocaleDateString('en-BH') : '-'}</td>
          <td style="padding:12px 8px;text-align:right">
            <a href="/employee/credit/application-review.html?id=${a.application_id}" onclick="event.stopPropagation()" style="background:#1a3a5c;color:#fff;padding:6px 12px;border-radius:6px;text-decoration:none;font-size:0.78rem;font-weight:500">🔍 Review</a>
          </td>
        </tr>
      `;
    }).join("");

    document.getElementById("queueBody").innerHTML = `
      <div class="nb-card" style="padding:0;overflow-x:auto">
        <table style="width:100%;border-collapse:collapse;font-size:0.85rem">
          <thead style="background:var(--nb-bg);font-size:0.75rem;text-transform:uppercase;color:var(--nb-text-muted);user-select:none">
            <tr>
              <th data-sort="application_id" style="padding:12px 8px;text-align:left;cursor:pointer">Application ID <span class="sort-ind"></span></th>
              <th data-sort="customer" style="padding:12px 8px;text-align:left;cursor:pointer">Customer <span class="sort-ind"></span></th>
              <th data-sort="loan_type" style="padding:12px 8px;text-align:left;cursor:pointer">Type <span class="sort-ind"></span></th>
              <th data-sort="amount" style="padding:12px 8px;text-align:right;cursor:pointer">Amount <span class="sort-ind"></span></th>
              <th data-sort="duration" style="padding:12px 8px;text-align:center;cursor:pointer">Duration <span class="sort-ind"></span></th>
              <th data-sort="underwriting_score" style="padding:12px 8px;text-align:center;cursor:pointer">Score <span class="sort-ind"></span></th>
              <th data-sort="status" style="padding:12px 8px;text-align:left;cursor:pointer">Status <span class="sort-ind"></span></th>
              <th data-sort="created_at" style="padding:12px 8px;text-align:left;cursor:pointer">Submitted <span class="sort-ind"></span></th>
              <th style="padding:12px 8px;text-align:right">Action</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  // Sort state
  let sortField = 'created_at';
  let sortDir = 'desc';

  function getSortValue(a, field) {
    if (field === 'customer') return ((a.first_name||'') + ' ' + (a.last_name||'')).trim().toLowerCase() || (a.customer_id||'').toLowerCase();
    if (field === 'amount' || field === 'duration' || field === 'underwriting_score') {
      const n = parseFloat(a[field]);
      return isNaN(n) ? -Infinity : n;
    }
    return (a[field] || '').toString().toLowerCase();
  }

  function applySort(arr) {
    return [...arr].sort((a, b) => {
      const va = getSortValue(a, sortField);
      const vb = getSortValue(b, sortField);
      if (va < vb) return sortDir === 'asc' ? -1 : 1;
      if (va > vb) return sortDir === 'asc' ? 1 : -1;
      return 0;
    });
  }

  function updateSortIndicators() {
    document.querySelectorAll('th[data-sort]').forEach(th => {
      const ind = th.querySelector('.sort-ind');
      if (!ind) return;
      if (th.dataset.sort === sortField) {
        ind.textContent = sortDir === 'asc' ? ' ↑' : ' ↓';
        ind.style.color = 'var(--nb-accent)';
      } else {
        ind.textContent = ' ⇅';
        ind.style.color = 'var(--nb-text-muted)';
        ind.style.opacity = '0.4';
      }
    });
  }

  function attachSortHandlers() {
    document.querySelectorAll('th[data-sort]').forEach(th => {
      th.addEventListener('click', () => {
        const field = th.dataset.sort;
        if (sortField === field) {
          sortDir = sortDir === 'asc' ? 'desc' : 'asc';
        } else {
          sortField = field;
          sortDir = field === 'amount' || field === 'underwriting_score' || field === 'created_at' ? 'desc' : 'asc';
        }
        applyFilters();
      });
    });
  }

  function applyFilters() {
    const statusFilter = document.getElementById("statusFilter").value;
    const search = document.getElementById("searchBox").value.toLowerCase();
    let filtered = allApps;
    if (window._customFilter && Array.isArray(window._customFilter)) {
      filtered = filtered.filter(a => window._customFilter.includes(a.status));
    } else if (statusFilter) {
      filtered = filtered.filter(a => a.status === statusFilter);
    }
    if (search) filtered = filtered.filter(a =>
      (a.application_id||'').toLowerCase().includes(search) ||
      (a.customer_id||'').toLowerCase().includes(search) ||
      ((a.first_name||'')+' '+(a.last_name||'')).toLowerCase().includes(search)
    );
    filtered = applySort(filtered);
    renderTable(filtered);
    setTimeout(() => { updateSortIndicators(); attachSortHandlers(); }, 0);
  }

  window.copyCustomerId = (cid) => {
    if (navigator.clipboard) navigator.clipboard.writeText(cid).catch(()=>{});
    const sb = document.getElementById('searchBox');
    if (sb) { sb.value = cid; }
    applyFilters();
    const note = document.createElement('div');
    note.textContent = '✓ Copied: ' + cid;
    note.style.cssText = 'position:fixed;top:20px;right:20px;background:#16a34a;color:white;padding:10px 18px;border-radius:8px;font-size:0.85rem;font-weight:600;z-index:9999;box-shadow:0 4px 12px rgba(0,0,0,0.15)';
    document.body.appendChild(note);
    setTimeout(() => note.remove(), 2000);
  };

  async function loadQueue() {
    try {
      const res = await fetch(`${REVIEW_API}/loans/all`, { credentials: "include" });
      if (!res.ok) {
        document.getElementById("queueBody").innerHTML = `<div class="nb-card" style="padding:20px;color:#dc2626">Unable to load: ${res.status}</div>`;
        return;
      }
      const data = await res.json();
      allApps = data.applications || [];
      renderStats(allApps);
      applyFilters();
    } catch (e) {
      document.getElementById("queueBody").innerHTML = `<div class="nb-card" style="padding:20px;color:#dc2626">Error: ${e.message}</div>`;
    }
  }

  document.getElementById("refreshBtn").addEventListener("click", loadQueue);
  document.getElementById("statusFilter").addEventListener("change", () => { window._customFilter = null; document.querySelectorAll(".stat-tile").forEach(t => t.style.border="2px solid transparent"); applyFilters(); });
  document.getElementById("searchBox").addEventListener("input", applyFilters);
  loadQueue();
})();
