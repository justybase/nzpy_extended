/* Sales Network MIS — single-page frontend.
 * Report pages come from /api/report/{id}; exports via /api/export/... (xlspy).
 * Sales ledger: /api/ledger (server-side pagination + filtering).
 * People views: /api/people (advisor panels, my branch / my results).
 */
"use strict";

const MENU = [
  { sep: "Reports" },
  { id: "overview", label: "Overview", type: "report" },
  { id: "loans", label: "Loans", type: "report" },
  { id: "investments", label: "Investments", type: "report" },
  { id: "insurance", label: "Insurance", type: "report" },
  { id: "current_accounts", label: "Current accounts (ROR)", type: "report" },
  { id: "ror_balances", label: "ROR balances", type: "report" },
  { id: "savings_accounts", label: "Savings accounts", type: "report" },
  { id: "term_deposits", label: "Term deposits", type: "report" },
  { id: "clients", label: "Client acquisition & churn", type: "report" },
  { id: "penetration", label: "Product penetration", type: "report" },
  { id: "campaigns", label: "Campaign effectiveness", type: "report" },
  { sep: "Sales force" },
  { id: "advisors", label: "Advisor performance", type: "advisors" },
  { sep: "My views" },
  { id: "my_branch", label: "My branch", type: "personal", scope: "branch" },
  { id: "my_results", label: "My results", type: "personal", scope: "advisor" },
  { sep: "Detail" },
  { id: "ledger", label: "Sales ledger", type: "ledger" },
];

const PALETTE = ["#1f4e9c", "#2ca02c", "#ff7f0e", "#d62728", "#9467bd",
                 "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
                 "#1f77b4", "#98df8a", "#ffbb78", "#c49c94", "#f7b6d2"];

const MONTHS = ["January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"];

const state = { page: "overview", from: null, to: null, dim: null };
const charts = [];      // active Chart.js instances
const sortState = {};   // tableId -> {col, dir}
let ALL_MONTHS = [];    // available months from /api/meta

// Row-level drill-through: which reports and which first columns are clickable
const DRILLABLE_PAGES = new Set([
  "loans", "investments", "insurance",
  "current_accounts", "savings_accounts", "term_deposits",
]);
const DRILL_TARGETS = { branch_code: "advisors", advisor_code: "sales", region_code: "branches" };
const DRILL_LABELS = { advisors: "Advisors", sales: "Sales", branches: "Branches" };
const drillState = { open: false, target: null, key: null, label: null, page: null };

// server-side paginated ledger state
const ledgerState = {
  q: "", group: "", channel: "", status: "",
  sort: "sale_date", dir: "desc", page: 1, pageSize: 50, qTimer: null,
};
// person / personal-view state
const personState = { code: null, month: null };

const $ = (id) => document.getElementById(id);

const fmtEur = new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR",
  minimumFractionDigits: 0, maximumFractionDigits: 2 });
const fmtInt = new Intl.NumberFormat("en-IE");

function monthLabel(ym) {
  const [y, m] = ym.split("-");
  return `${MONTHS[parseInt(m, 10) - 1]} ${y}`;
}

function formatValue(value, fmt) {
  if (value === null || value === undefined) return "—";
  switch (fmt) {
    case "eur": return fmtEur.format(value);
    case "int": return fmtInt.format(value);
    case "pct": return `${Number(value).toFixed(2)}%`;
    case "dec": return Number(value).toFixed(2);
    default: return String(value);
  }
}

function showToast(msg, ms = 5000) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.hidden = true; }, ms);
}

function showNotice(msg) {
  const n = $("notice");
  n.hidden = !msg;
  n.textContent = msg || "";
}

/* ------------------------------------------------------------------ menu */

function renderMenu(activeId) {
  const menu = $("menu");
  menu.innerHTML = "";
  for (const item of MENU) {
    if (item.sep) {
      const div = document.createElement("div");
      div.className = "menu-sep";
      div.textContent = item.sep;
      menu.appendChild(div);
      continue;
    }
    const btn = document.createElement("button");
    btn.className = "menu-item" + (item.id === activeId ? " active" : "");
    btn.innerHTML = `<span class="menu-label">${item.label}</span>`;
    btn.addEventListener("click", () => selectPage(item.id));
    menu.appendChild(btn);
  }
}

function menuItem(id) {
  return MENU.find((m) => m.id === id);
}

function selectPage(id) {
  state.page = id;
  renderMenu(id);
  const item = menuItem(id);
  $("page-title").textContent = item ? item.label : id;
  closeDrill();
  showNotice(null);
  if (item.type === "ledger") loadLedgerPage();
  else if (item.type === "advisors") loadAdvisorsPage();
  else if (item.type === "personal") loadPersonalPage(item.scope);
  else loadReportPage();
}

/* ------------------------------------------------------------------ fetch */

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) {
    const detail = body && body.detail ? body.detail : `HTTP ${res.status}`;
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return body;
}

/* ------------------------------------------------------------ layout */

function setLayout({ kpis = false, charts = false, synthetic = false,
                     analytic = false, custom = false, fromTo = true }) {
  $("kpis").hidden = !kpis;
  $("charts").hidden = !charts;
  $("card-synthetic").hidden = !synthetic;
  $("card-analytic").hidden = !analytic;
  $("custom-page").hidden = !custom;
  $("from-label").hidden = !fromTo;
  $("to-label").hidden = !fromTo;
  if (!kpis) $("kpis").innerHTML = "";
  if (!charts) { for (const c of charts) c.destroy(); charts.length = 0; }
}

/* ------------------------------------------------------------- reports */

async function loadReportPage() {
  setLayout({ kpis: true, charts: true, synthetic: true, analytic: true });
  const params = new URLSearchParams();
  if (state.from) params.set("from", state.from);
  if (state.to) params.set("to", state.to);
  if (state.dim) params.set("dim", state.dim);
  try {
    const payload = await fetchJSON(`/api/report/${state.page}?${params}`);
    renderReport(payload);
  } catch (err) {
    showNotice(`Could not load report "${state.page}": ${err.message}`);
    $("synthetic-table").innerHTML = "";
    $("analytic-table").innerHTML = "";
  }
}

function renderReport(payload) {
  $("page-subtitle").textContent =
    `${payload.subtitle} · ${monthLabel(payload.period.from)} – ${monthLabel(payload.period.to)}`;
  renderKpis($("kpis"), payload.kpis || []);
  renderCharts(payload.charts || []);
  renderDimSelect(payload.dims || [], payload.dim);
  renderTableCard("synthetic", payload.synthetic, payload);
  if (payload.dims && payload.dims.length && payload.analytic &&
      payload.analytic.columns && payload.analytic.columns.length) {
    $("card-analytic").hidden = false;
    const drill = computeDrill(payload);
    renderTableCard("analytic", payload.analytic, payload, drill);
    attachDrillHandler(drill);
  } else {
    $("card-analytic").hidden = true;
  }
}

function computeDrill(payload) {
  if (!DRILLABLE_PAGES.has(payload.id)) return null;
  const columns = (payload.analytic || {}).columns || [];
  const firstKey = columns.length ? columns[0].key : null;
  const target = DRILL_TARGETS[firstKey];
  if (!target) return null;
  return { page: payload.id, target, codeIdx: 0, labelIdx: 1 };
}

function attachDrillHandler(drill) {
  const container = $("analytic-table");
  container._drill = drill;
  if (container._drillListener) return;
  container.addEventListener("click", (e) => {
    const tr = e.target.closest("tr.drillable");
    if (!tr || !container._drill) return;
    openDrill(container._drill, tr.dataset.code, tr.dataset.label);
  });
  container._drillListener = true;
}

function renderKpis(grid, kpis) {
  grid.innerHTML = "";
  for (const kpi of kpis) {
    const card = document.createElement("div");
    card.className = "kpi";
    const value = formatValue(kpi.value, kpi.fmt);
    const neg = typeof kpi.value === "number" && kpi.value < 0;
    card.innerHTML = `
      <div class="kpi-label">${kpi.label}</div>
      <div class="kpi-value ${kpi.fmt === "pct" ? "pct" : ""} ${neg ? "neg" : ""}">${value}</div>`;
    grid.appendChild(card);
  }
}

function renderCharts(list) {
  for (const c of charts) c.destroy();
  charts.length = 0;
  const grid = $("charts");
  grid.innerHTML = "";
  if (!list.length) { grid.hidden = true; return; }
  grid.hidden = false;
  for (const spec of list) {
    const card = document.createElement("div");
    card.className = "chart-card";
    const head = document.createElement("div");
    head.className = "chart-head";
    const h3 = document.createElement("h3");
    h3.textContent = spec.title;
    const actions = document.createElement("div");
    actions.className = "card-actions";
    head.appendChild(h3);
    head.appendChild(actions);
    const box = document.createElement("div");
    box.className = "chart-box";
    const canvas = document.createElement("canvas");
    box.appendChild(canvas);
    card.appendChild(head);
    card.appendChild(box);
    grid.appendChild(card);
    const chart = makeChart(canvas, spec);
    charts.push(chart);
    const base = `${state.page}_${spec.id}`;
    addButton(actions, "PNG", () => downloadChartPNG(chart, base));
    addButton(actions, "PDF", () => downloadChartPDF(chart, base));
  }
}

/* ---------------------------------------------------- chart export (PNG/PDF) */

function addButton(container, text, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn small";
  b.textContent = text;
  b.addEventListener("click", onClick);
  container.appendChild(b);
}

function downloadChartPNG(chart, name) {
  const a = document.createElement("a");
  a.href = chart.toBase64Image("image/png");
  a.download = `${name}.png`;
  a.click();
}

function pdfLib() {
  if (!window.jspdf || !window.jspdf.jsPDF) {
    showToast("PDF export needs the jsPDF library (CDN). PNG export still works.");
    return null;
  }
  return window.jspdf.jsPDF;
}

function addChartImageToPDF(pdf, chart, topOffset, center) {
  const img = chart.toBase64Image("image/png");
  const props = pdf.getImageProperties(img);
  const pageW = 297, pageH = 210; // A4 landscape, mm
  const maxW = pageW - 20;
  const maxH = pageH - topOffset - 14;
  let w = maxW;
  let h = (w * props.height) / props.width;
  if (h > maxH) {
    h = maxH;
    w = (h * props.width) / props.height;
  }
  const x = center ? (pageW - w) / 2 : 10;
  pdf.addImage(img, "PNG", x, topOffset, w, h);
}

function downloadChartPDF(chart, name) {
  const jsPDF = pdfLib();
  if (!jsPDF) return;
  const pdf = new jsPDF({ orientation: "landscape", unit: "mm", format: "a4" });
  addChartImageToPDF(pdf, chart, 10, false);
  pdf.save(`${name}.pdf`);
}

function downloadAllChartsPDF() {
  const jsPDF = pdfLib();
  if (!jsPDF) return;
  if (!charts.length) return;
  const pdf = new jsPDF({ orientation: "landscape", unit: "mm", format: "a4" });
  const pageLabel = menuItem(state.page)?.label || state.page;
  charts.forEach((chart, i) => {
    if (i > 0) pdf.addPage();
    const canvas = chart.canvas;
    const card = canvas.closest(".chart-card");
    const title = card ? card.querySelector("h3").textContent : "Chart";
    pdf.setFontSize(12);
    pdf.text(`${pageLabel} — ${title}`, 10, 8);
    addChartImageToPDF(pdf, chart, 14, true);
  });
  pdf.save(`${state.page}_charts.pdf`);
}

function makeChart(canvas, spec) {
  const labels = spec.labels || [];
  const seriesList = spec.series || [];
  const colors = seriesList.map((_, i) => PALETTE[i % PALETTE.length]);
  let type = spec.type;
  const isHBar = type === "hbar";
  if (isHBar) type = "bar";

  let datasets;
  if (type === "doughnut") {
    datasets = [{
      data: seriesList[0] ? seriesList[0].data : [],
      backgroundColor: labels.map((_, i) => PALETTE[i % PALETTE.length]),
      borderWidth: 1,
    }];
  } else {
    datasets = seriesList.map((s, i) => ({
      label: s.name,
      data: s.data,
      borderColor: colors[i],
      backgroundColor: type === "line" ? colors[i] + "22" : colors[i] + "88",
      borderWidth: type === "line" ? 2 : 1,
      fill: false,
      tension: 0.3,
      pointRadius: type === "line" ? 2.5 : 0,
    }));
  }

  return new Chart(canvas, {
    type,
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: isHBar ? "y" : "x",
      plugins: {
        legend: {
          display: datasets.length > 1 || type === "doughnut",
          position: "bottom",
          labels: { boxWidth: 12, font: { size: 10 } },
        },
      },
      scales: type === "doughnut" ? {} : {
        x: { ticks: { maxRotation: 45, font: { size: 10 } }, grid: { display: false } },
        y: {
          beginAtZero: true,
          ticks: {
            font: { size: 10 },
            callback: (v) => (Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(1)}M`
                               : Math.abs(v) >= 1e3 ? `${(v / 1e3).toFixed(0)}k` : v),
          },
        },
      },
    },
  });
}

function renderDimSelect(dims, current) {
  const label = $("dim-label");
  if (!dims.length) { label.hidden = true; return; }
  label.hidden = false;
  const sel = $("select-dim");
  sel.innerHTML = "";
  for (const d of dims) {
    const opt = document.createElement("option");
    opt.value = d.id;
    opt.textContent = d.label;
    sel.appendChild(opt);
  }
  sel.value = current || dims[0].id;
  sel.onchange = () => {
    state.dim = sel.value;
    loadReportPage();
  };
}

function renderTableCard(kind, tableDef, payload, drill) {
  const sub = $(`${kind}-sub`);
  const actions = $(`${kind}-actions`);
  const columns = tableDef.columns || [];
  const rows = tableDef.rows || [];
  const dim = kind === "analytic" ? payload.dim : null;
  const hint = drill ? " — click a row to drill down" : "";
  sub.textContent = (dim
    ? `${rows.length} rows · breakdown by ${menuItem(payload.id)?.label || payload.id} dimension`
    : `${rows.length} rows`) + hint;
  actions.innerHTML = "";
  // a table without columns is a placeholder (e.g. overview has no analytic) —
  // the backend rejects its export with 400, so don't offer the buttons
  if (!columns.length) return;
  for (const fmt of ["xlsx", "xlsb"]) {
    const params = new URLSearchParams();
    if (state.from) params.set("from", state.from);
    if (state.to) params.set("to", state.to);
    if (dim) params.set("dim", dim);
    const a = document.createElement("a");
    a.className = "btn small primary";
    a.href = `/api/export/${payload.id}/${kind}/${fmt}?${params}`;
    a.textContent = fmt === "xlsx" ? "XLSX" : "XLSB";
    actions.appendChild(a);
  }
  renderTable($(`${kind}-table`), columns, rows, `${payload.id}:${kind}`, drill);
}

function renderTable(container, columns, rows, tableId, drill) {
  container.innerHTML = "";
  if (!columns.length || !rows.length) {
    container.innerHTML = `<div class="empty">No data for the selected period.</div>`;
    return;
  }
  const sort = sortState[tableId] || { col: -1, dir: 1 };

  const sorted = [...rows];
  if (sort.col >= 0 && sort.col < columns.length) {
    const col = columns[sort.col];
    const numeric = col.fmt !== "str";
    sorted.sort((a, b) => {
      const va = a[sort.col], vb = b[sort.col];
      if (va === null || va === undefined) return 1;
      if (vb === null || vb === undefined) return -1;
      if (numeric) return (va - vb) * sort.dir;
      return String(va).localeCompare(String(vb)) * sort.dir;
    });
  }

  const table = document.createElement("table");
  table.className = "grid";
  const thead = document.createElement("thead");
  const trh = document.createElement("tr");
  columns.forEach((c, i) => {
    const th = document.createElement("th");
    th.className = c.fmt === "str" ? "" : "num";
    const arrow = sort.col === i ? (sort.dir === 1 ? " ▲" : " ▼") : "";
    th.innerHTML = `${c.label}<span class="arrow">${arrow}</span>`;
    th.addEventListener("click", () => {
      const prev = sortState[tableId] || { col: -1, dir: 1 };
      sortState[tableId] = { col: i, dir: prev.col === i ? -prev.dir : 1 };
      renderTable(container, columns, rows, tableId);
    });
    trh.appendChild(th);
  });
  thead.appendChild(trh);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (const row of sorted) {
    const tr = document.createElement("tr");
    if (drill) {
      tr.className = "drillable";
      tr.title = `Show ${DRILL_LABELS[drill.target] || "details"} for ${row[drill.labelIdx]}`;
      tr.dataset.code = row[drill.codeIdx];
      tr.dataset.label = row[drill.labelIdx];
    }
    row.forEach((v, i) => {
      const td = document.createElement("td");
      td.className = columns[i].fmt === "str" ? "" : "num";
      td.textContent = formatValue(v, columns[i].fmt);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  container.appendChild(table);
}

/* -------------------------------------------------------------- drill-down */

async function openDrill(drill, code, label) {
  drillState.open = true;
  drillState.page = drill.page;
  drillState.target = drill.target;
  drillState.key = code;
  drillState.label = label;
  const params = new URLSearchParams({ key: code });
  if (state.from) params.set("from", state.from);
  if (state.to) params.set("to", state.to);
  try {
    const payload = await fetchJSON(`/api/drill/${drill.page}/${drill.target}?${params}`);
    renderDrill(payload);
  } catch (err) {
    showToast(`Drill-down failed: ${err.message}`);
    closeDrill();
  }
}

function renderDrill(payload) {
  const pageLabel = menuItem(payload.report_id)?.label || payload.report_id;
  $("drill-title").textContent = `${DRILL_LABELS[payload.target]} — ${payload.key}`;
  $("drill-sub").innerHTML =
    `<span class="drill-breadcrumb">${pageLabel} → ${payload.key}</span>` +
    ` · ${payload.rows.length} rows · ${monthLabel(payload.period.from)} – ${monthLabel(payload.period.to)}`;
  const actions = $("drill-actions");
  actions.innerHTML = "";
  for (const fmt of ["xlsx", "xlsb"]) {
    const params = new URLSearchParams({
      key: drillState.key, fmt,
      from: state.from || "", to: state.to || "",
    });
    const a = document.createElement("a");
    a.className = "btn small primary";
    a.href = `/api/drill/${drillState.page}/${drillState.target}?${params}`;
    a.textContent = fmt === "xlsx" ? "XLSX" : "XLSB";
    actions.appendChild(a);
  }
  renderTable($("drill-table"), payload.columns, payload.rows, "drill");
  const card = $("card-drill");
  card.hidden = false;
  card.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function closeDrill() {
  drillState.open = false;
  $("card-drill").hidden = true;
  $("drill-table").innerHTML = "";
}

/* ------------------------------------------------------- sales ledger */

function ledgerParams(extra) {
  const p = new URLSearchParams(extra || {});
  if (state.from) p.set("from", state.from);
  if (state.to) p.set("to", state.to);
  if (ledgerState.q) p.set("q", ledgerState.q);
  if (ledgerState.group) p.set("group", ledgerState.group);
  if (ledgerState.channel) p.set("channel", ledgerState.channel);
  if (ledgerState.status) p.set("status", ledgerState.status);
  p.set("sort", ledgerState.sort);
  p.set("dir", ledgerState.dir);
  p.set("page", ledgerState.page);
  p.set("page_size", ledgerState.pageSize);
  return p;
}

async function loadLedgerPage() {
  setLayout({ custom: true });
  $("page-subtitle").textContent = "Full sales detail — server-side pagination and filtering";
  try {
    const first = await fetchJSON("/api/ledger?" + ledgerParams());
    renderLedger(first);
  } catch (err) {
    showNotice(`Could not load the sales ledger: ${err.message}`);
    $("custom-page").innerHTML = "";
  }
}

function fillSelect(sel, values, current) {
  sel.innerHTML = "";
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "All";
  sel.appendChild(none);
  for (const v of values) {
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = v;
    sel.appendChild(opt);
  }
  sel.value = current || "";
}

function renderLedger(payload) {
  const page = $("custom-page");
  page.innerHTML = `
    <div class="ledger-toolbar">
      <input id="ledger-q" class="ledger-search" type="search"
             placeholder="Search customer, branch, advisor, product, status…"
             value="${escAttr(ledgerState.q)}">
      <label>Product group <select id="ledger-group"></select></label>
      <label>Channel <select id="ledger-channel"></select></label>
      <label>Status <select id="ledger-status"></select></label>
      <label>Rows <select id="ledger-size">
        <option value="25">25</option><option value="50">50</option>
        <option value="100">100</option><option value="200">200</option>
      </select></label>
      <span class="ledger-exports">
        <a class="btn small primary" id="ledger-export-xlsx" href="#">XLSX</a>
        <a class="btn small primary" id="ledger-export-xlsb" href="#">XLSB</a>
      </span>
    </div>
    <div class="card">
      <div class="table-wrap ledger-table" id="ledger-table"></div>
      <div class="ledger-foot">
        <span id="ledger-info"></span>
        <div class="pager">
          <button class="btn small" id="pg-first" type="button">First</button>
          <button class="btn small" id="pg-prev" type="button">Prev</button>
          <span id="ledger-pages"></span>
          <button class="btn small" id="pg-next" type="button">Next</button>
          <button class="btn small" id="pg-last" type="button">Last</button>
        </div>
      </div>
    </div>`;

  fillSelect($("ledger-group"), payload.filters.groups, ledgerState.group);
  fillSelect($("ledger-channel"), payload.filters.channels, ledgerState.channel);
  fillSelect($("ledger-status"), payload.filters.statuses, ledgerState.status);
  $("ledger-size").value = String(ledgerState.pageSize);

  const q = $("ledger-q");
  clearTimeout(ledgerState.qTimer);
  q.addEventListener("input", () => {
    clearTimeout(ledgerState.qTimer);
    ledgerState.q = q.value.trim();
    ledgerState.page = 1;
    ledgerState.qTimer = setTimeout(() => {
      fetchLedgerPage().catch((err) => showNotice(`Search failed: ${err.message}`));
    }, 300);
  });
  $("ledger-group").onchange = () => { ledgerState.group = $("ledger-group").value; ledgerState.page = 1; fetchLedgerPage(); };
  $("ledger-channel").onchange = () => { ledgerState.channel = $("ledger-channel").value; ledgerState.page = 1; fetchLedgerPage(); };
  $("ledger-status").onchange = () => { ledgerState.status = $("ledger-status").value; ledgerState.page = 1; fetchLedgerPage(); };
  $("ledger-size").onchange = () => {
    ledgerState.pageSize = parseInt($("ledger-size").value, 10);
    ledgerState.page = 1;
    fetchLedgerPage();
  };

  $("pg-first").onclick = () => { ledgerState.page = 1; fetchLedgerPage(); };
  $("pg-prev").onclick = () => { ledgerState.page = Math.max(1, ledgerState.page - 1); fetchLedgerPage(); };
  $("pg-next").onclick = () => { ledgerState.page = Math.min(payload.pages, ledgerState.page + 1); fetchLedgerPage(); };
  $("pg-last").onclick = () => { ledgerState.page = payload.pages; fetchLedgerPage(); };

  renderLedgerTable(payload);
}

function escAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;")
    .replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

async function fetchLedgerPage() {
  const payload = await fetchJSON("/api/ledger?" + ledgerParams());
  renderLedgerTable(payload);
}

function renderLedgerTable(payload) {
  // export links always reflect the current filters
  const x = $("ledger-export-xlsx"), b = $("ledger-export-xlsb");
  if (x && b) {
    x.href = "/api/ledger/export/xlsx?" + ledgerParams();
    b.href = "/api/ledger/export/xlsb?" + ledgerParams();
  }
  const container = $("ledger-table");
  container.innerHTML = "";
  const columns = payload.columns;
  const rows = payload.rows;
  if (!rows.length) {
    container.innerHTML = `<div class="empty">No rows match the current filters.</div>`;
    $("ledger-info").textContent = `0 of ${fmtInt.format(payload.total)} rows`;
    return;
  }
  const table = document.createElement("table");
  table.className = "grid";
  const thead = document.createElement("thead");
  const trh = document.createElement("tr");
  columns.forEach((c, i) => {
    const th = document.createElement("th");
    th.className = c.fmt === "str" ? "" : "num";
    const active = ledgerState.sort === c.key;
    const arrow = active ? (ledgerState.dir === "asc" ? " ▲" : " ▼") : "";
    th.innerHTML = `${c.label}<span class="arrow">${arrow}</span>`;
    th.addEventListener("click", () => {
      if (active) {
        ledgerState.dir = ledgerState.dir === "asc" ? "desc" : "asc";
      } else {
        ledgerState.sort = c.key;
        ledgerState.dir = c.key === "sale_date" ? "desc" : "asc";
      }
      ledgerState.page = 1;
      fetchLedgerPage().catch((err) => showNotice(`Sort failed: ${err.message}`));
    });
    trh.appendChild(th);
  });
  thead.appendChild(trh);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (const row of rows) {
    const tr = document.createElement("tr");
    row.forEach((v, i) => {
      const td = document.createElement("td");
      td.className = columns[i].fmt === "str" ? "" : "num";
      td.textContent = formatValue(v, columns[i].fmt);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  container.appendChild(table);

  const from = (payload.page - 1) * payload.page_size + 1;
  const to = from + rows.length - 1;
  $("ledger-info").textContent =
    `${fmtInt.format(from)}–${fmtInt.format(to)} of ${fmtInt.format(payload.total)} rows`;
  $("ledger-pages").textContent = `Page ${payload.page} of ${fmtInt.format(payload.pages)}`;
  $("pg-first").disabled = payload.page <= 1;
  $("pg-prev").disabled = payload.page <= 1;
  $("pg-next").disabled = payload.page >= payload.pages;
  $("pg-last").disabled = payload.page >= payload.pages;
}

/* ------------------------------------------------------ advisor panels */

async function loadAdvisorsPage() {
  setLayout({ charts: true, custom: true, fromTo: false });
  $("page-subtitle").textContent = "Select an advisor to see their profile, scores and ratings";
  const page = $("custom-page");
  page.innerHTML = `
    <div class="person-picker card">
      <label>Advisor
        <select id="advisor-select" class="person-select"></select>
      </label>
      <label class="check"><input type="checkbox" id="advisor-active" checked> Active only</label>
    </div>
    <div id="advisor-panel"></div>`;
  try {
    const data = await fetchJSON("/api/people/advisors");
    const advisors = data.advisors || [];
    const sel = $("advisor-select");
    sel.innerHTML = "";
    for (const a of advisors) {
      const opt = document.createElement("option");
      opt.value = a.code;
      opt.dataset.status = a.status || "";
      opt.textContent = `${a.name} — ${a.role || ""} · ${a.branch_name || a.branch_code}`;
      sel.appendChild(opt);
    }
    sel.onchange = () => { personState.code = sel.value; loadAdvisorPanel(); };
    $("advisor-active").onchange = () => { fillAdvisorSelect(); };
    personState.code = personState.code && advisors.some((a) => a.code === personState.code)
      ? personState.code : (advisors[0] ? advisors[0].code : null);
    sel.value = personState.code;
    fillAdvisorSelect();
    if (personState.code) await loadAdvisorPanel();
  } catch (err) {
    showNotice(`Could not load advisors: ${err.message}`);
  }
}

function fillAdvisorSelect() {
  const sel = $("advisor-select");
  const activeOnly = $("advisor-active").checked;
  for (const opt of sel.options) {
    opt.hidden = activeOnly && opt.dataset.status !== "ACTIVE";
  }
}

async function loadAdvisorPanel() {
  const panel = $("advisor-panel");
  panel.innerHTML = `<div class="empty">Loading…</div>`;
  try {
    const payload = await fetchJSON(`/api/people/advisors/${personState.code}`);
    renderAdvisorPanel(payload);
  } catch (err) {
    panel.innerHTML = `<div class="empty">Could not load advisor: ${err.message}</div>`;
  }
}

function renderAdvisorPanel(payload) {
  const info = payload.info;
  const s = payload.summary;
  const initials = (info.first_name || "?").charAt(0) + (info.last_name || "").charAt(0);
  const panel = $("advisor-panel");
  panel.innerHTML = `
    <div class="profile card">
      <div class="avatar">${escAttr(initials)}</div>
      <div class="profile-main">
        <div class="profile-name">${escAttr(info.name)}</div>
        <div class="profile-role">${escAttr(info.role || "")}</div>
        <div class="profile-meta">
          ${info.branch_name ? `${escAttr(info.branch_name)} (${escAttr(info.branch_code)})` : ""}
          ${info.city ? ` · ${escAttr(info.city)}` : ""}
          ${info.region_name ? ` · ${escAttr(info.region_name)}` : ""}
        </div>
        <div class="profile-meta muted">
          Hired ${info.hire_date || "—"}
          ${info.tenure_years != null ? ` · ${info.tenure_years} years of service` : ""}
          · ${escAttr(info.status || "")}
        </div>
      </div>
      <div class="profile-rating">
        <div class="rating-badge r${s.latest_rating || 0}">${s.latest_rating || "—"}<span>/5</span></div>
        <div class="rating-note">${escAttr(s.latest_note || "")}</div>
        <div class="rating-sub">latest month ${s.latest_month ? escAttr(monthLabel(s.latest_month)) : "—"}</div>
      </div>
    </div>
    <div class="score-grid">
      <div class="score card"><div class="score-label">Plan attainment</div>
        <div class="score-value">${s.latest_attainment != null ? formatValue(s.latest_attainment, "pct") : "—"}</div>
        <div class="score-sub">avg ${s.avg_attainment != null ? formatValue(s.avg_attainment, "pct") : "—"}</div></div>
      <div class="score card"><div class="score-label">Average rating</div>
        <div class="score-value">${s.avg_rating != null ? s.avg_rating : "—"}</div>
        <div class="score-sub">best month ${s.best_month ? escAttr(monthLabel(s.best_month)) : "—"}</div></div>
      <div class="score card"><div class="score-label">Sales score</div>
        <div class="score-value" id="scr-sales">—</div>
        <div class="score-sub">latest month</div></div>
      <div class="score card"><div class="score-label">Conversion</div>
        <div class="score-value" id="scr-conv">—</div>
        <div class="score-sub">latest month</div></div>
      <div class="score card"><div class="score-label">Quality</div>
        <div class="score-value" id="scr-qual">—</div>
        <div class="score-sub">latest month</div></div>
      <div class="score card"><div class="score-label">Activity</div>
        <div class="score-value" id="scr-act">—</div>
        <div class="score-sub">latest month</div></div>
    </div>
    <div class="card">
      <div class="card-head">
        <div>
          <h2>Monthly ratings</h2>
          <div class="card-sub">Plan vs achieved, KPI scores and rating per month</div>
        </div>
        <div class="card-actions" id="panel-actions"></div>
      </div>
      <div class="table-wrap" id="panel-table"></div>
    </div>`;

  const last = payload.rows.length ? payload.rows[payload.rows.length - 1] : null;
  if (last) {
    $("scr-sales").textContent = formatValue(last[5], "int");
    $("scr-conv").textContent = formatValue(last[6], "int");
    $("scr-qual").textContent = formatValue(last[7], "int");
    $("scr-act").textContent = formatValue(last[8], "int");
  }

  // export buttons for the ratings table (xlspy)
  const actions = $("panel-actions");
  for (const fmt of ["xlsx", "xlsb"]) {
    const a = document.createElement("a");
    a.className = "btn small primary";
    a.href = `/api/people/advisors/${encodeURIComponent(payload.code)}/export/${fmt}`;
    a.textContent = fmt === "xlsx" ? "XLSX" : "XLSB";
    actions.appendChild(a);
  }

  renderCharts(payload.charts || []);
  // the first column is the hidden 'ym' sort key — drop it for display
  const cols = payload.columns.slice(1);
  renderTable($("panel-table"), cols, payload.rows.map((r) => r.slice(1)),
              `panel:${payload.code}`);
}

/* --------------------------------------------- personal views (my branch / my results) */

async function loadPersonalPage(scope) {
  setLayout({ kpis: true, charts: true, custom: true, fromTo: false });
  const label = scope === "branch" ? "branch" : "advisor";
  $("page-subtitle").textContent =
    `Cumulative ${scope === "branch" ? "branch" : "advisor"} sales within the month — vs plan and vs previous month`;
  const page = $("custom-page");
  page.innerHTML = `
    <div class="person-picker card">
      <label>${scope === "branch" ? "Branch" : "Advisor"}
        <select id="entity-select" class="person-select"></select>
      </label>
      <label>Month <select id="entity-month"></select></label>
    </div>
    <div id="personal-table-card" class="card" hidden>
      <div class="card-head">
        <div>
          <h2>Day-by-day cumulative</h2>
          <div class="card-sub" id="personal-sub"></div>
        </div>
        <div class="card-actions" id="personal-actions"></div>
      </div>
      <div class="table-wrap" id="personal-table"></div>
    </div>`;

  const monthSel = $("entity-month");
  monthSel.innerHTML = "";
  for (const ym of ALL_MONTHS) {
    const opt = document.createElement("option");
    opt.value = ym;
    opt.textContent = monthLabel(ym);
    monthSel.appendChild(opt);
  }
  if (!personState.month || !ALL_MONTHS.includes(personState.month)) {
    personState.month = ALL_MONTHS[ALL_MONTHS.length - 1];
  }
  monthSel.value = personState.month;
  monthSel.onchange = () => { personState.month = monthSel.value; loadCumulative(scope); };

  try {
    const data = await fetchJSON(scope === "branch"
      ? "/api/people/branches" : "/api/people/advisors");
    const items = scope === "branch" ? (data.branches || []) : (data.advisors || []);
    const sel = $("entity-select");
    sel.innerHTML = "";
    for (const it of items) {
      const opt = document.createElement("option");
      opt.value = it.code;
      opt.textContent = scope === "branch"
        ? `${it.name} — ${it.city || ""}` : `${it.name} — ${it.role || ""}`;
      sel.appendChild(opt);
    }
    sel.onchange = () => { personState.code = sel.value; loadCumulative(scope); };
    personState.code = personState.code && items.some((i) => i.code === personState.code)
      ? personState.code : (items[0] ? items[0].code : null);
    sel.value = personState.code;
    if (personState.code) await loadCumulative(scope);
  } catch (err) {
    showNotice(`Could not load ${label}s: ${err.message}`);
  }
}

async function loadCumulative(scope) {
  try {
    const payload = await fetchJSON(
      `/api/people/cumulative?scope=${scope}&code=${encodeURIComponent(personState.code)}`
      + `&month=${personState.month}`);
    renderCumulative(payload);
  } catch (err) {
    showNotice(`Could not load the cumulative view: ${err.message}`);
  }
}

function renderCumulative(payload) {
  renderKpis($("kpis"), payload.kpis || []);
  renderCharts(payload.charts || []);
  const card = $("personal-table-card");
  card.hidden = false;
  const info = payload.info;
  $("personal-sub").textContent =
    `${info.name} · ${monthLabel(payload.month)} · ${payload.rows.length} days`;
  const actions = $("personal-actions");
  actions.innerHTML = "";
  for (const fmt of ["xlsx", "xlsb"]) {
    const a = document.createElement("a");
    a.className = "btn small primary";
    a.href = `/api/people/cumulative/export/${fmt}?scope=${payload.scope}`
      + `&code=${encodeURIComponent(personState.code)}&month=${personState.month}`;
    a.textContent = fmt === "xlsx" ? "XLSX" : "XLSB";
    actions.appendChild(a);
  }
  renderTable($("personal-table"), payload.columns, payload.rows,
              `${payload.scope}:${payload.month}`, null);
}

/* ------------------------------------------------------------------ cache */

async function refreshCache() {
  const btn = $("cache-refresh");
  btn.disabled = true;
  btn.textContent = "Reloading…";
  try {
    await fetchJSON("/api/cache/refresh", { method: "POST" });
    showToast("Tables reloaded from Netezza. Report cache cleared.");
    await initMeta();
    selectPage(state.page);
  } catch (err) {
    showToast(`Refresh failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Reload data";
  }
}

function renderCacheInfo(meta) {
  const c = meta.cache;
  const tables = Object.values(c.tables || {});
  const total = Object.keys(c.tables || {}).length;
  const rows = tables.reduce((acc, t) => acc + (t.rows || 0), 0);
  const loadedAt = tables.length
    ? new Date(tables[0].loaded_at).toLocaleTimeString("en-IE") : "—";
  let html = `${total} tables in memory<br>${fmtInt.format(rows)} rows<br>loaded ${loadedAt} · TTL ${c.ttl_seconds / 60}m<br>hits ${fmtInt.format(c.hits)} / misses ${fmtInt.format(c.misses)}`;
  if (c.last_error) html += `<br><span class="err">last error: ${c.last_error}</span>`;
  $("cache-info").innerHTML = html;
}

/* ------------------------------------------------------------------ init */

async function initMeta() {
  const meta = await fetchJSON("/api/meta");
  ALL_MONTHS = meta.months || [];
  const fromSel = $("select-from");
  const toSel = $("select-to");
  fromSel.innerHTML = "";
  toSel.innerHTML = "";
  for (const ym of ALL_MONTHS) {
    const o1 = document.createElement("option");
    o1.value = ym; o1.textContent = monthLabel(ym);
    const o2 = o1.cloneNode();
    fromSel.appendChild(o1);
    toSel.appendChild(o2);
  }
  if (ALL_MONTHS.length) {
    if (!state.from || !ALL_MONTHS.includes(state.from)) {
      state.from = ALL_MONTHS[Math.max(0, ALL_MONTHS.length - 12)];
    }
    if (!state.to || !ALL_MONTHS.includes(state.to)) {
      state.to = ALL_MONTHS[ALL_MONTHS.length - 1];
    }
  }
  fromSel.value = state.from;
  toSel.value = state.to;
  fromSel.onchange = () => { state.from = fromSel.value; selectPage(state.page); };
  toSel.onchange = () => { state.to = toSel.value; selectPage(state.page); };
  renderCacheInfo(meta);
}

(async function main() {
  $("cache-refresh").addEventListener("click", refreshCache);
  $("drill-close").addEventListener("click", closeDrill);
  $("btn-charts-pdf").addEventListener("click", downloadAllChartsPDF);
  try {
    await initMeta();
  } catch (err) {
    showNotice(`Cannot reach the backend (${err.message}). Is the server running with seeded tables?`);
  }
  selectPage("overview");
})();