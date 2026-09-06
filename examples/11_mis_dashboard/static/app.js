/* Sales Network MIS — single-page frontend.
 * Report pages come from /api/report/{id}; exports via /api/export/... (xlspy).
 * Sales ledger: /api/ledger (server-side pagination + filtering).
 * People views: /api/people (advisor panels, my branch / my results).
 */
"use strict";

const MENU = [
  { sep: "Reports" },
  { id: "overview", label: "Overview", type: "report" },
  { id: "daily_performance", label: "Daily performance", type: "performance" },
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
  { id: "champions", label: "Champions League", type: "league" },
  { sep: "Organization" },
  { id: "org_history", label: "Organization history", type: "hierarchy" },
  { sep: "My views" },
  { id: "my_branch", label: "My branch", type: "personal", scope: "branch" },
  { id: "my_results", label: "My results", type: "personal", scope: "advisor" },
  { id: "daily_brief", label: "Daily brief", type: "brief" },
  { sep: "Detail" },
  { id: "ledger", label: "Sales ledger", type: "ledger" },
  { sep: "Governance" },
  { id: "data_quality", label: "Data quality", type: "quality" },
];

const PALETTE = ["#167d8d", "#6775c5", "#d9a44a", "#c86c73", "#6d9b85",
                 "#956daa", "#4f8eb8", "#a08667", "#9da851", "#52a9a0",
                 "#53668a", "#b58fac", "#c48851", "#808995", "#a4baa0"];

const MONTHS = ["January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"];

const state = { page: "overview", from: null, to: null, dim: null,
                asOf: null, attribution: "historical" };
const charts = [];      // active Chart.js instances
window.addEventListener("beforeprint", () => {
  for (const chart of charts) chart.resize();
});
window.addEventListener("afterprint", () => {
  for (const chart of charts) chart.resize();
});
const sortState = {};   // tableId -> {col, dir}
let pageRequestId = 0;  // prevents an older response replacing a newer view
let ledgerRequestId = 0; // stale guard for ledger pagination/filter/sort
let drillRequestId = 0; // stale guard for drill-down
let panelRequestId = 0; // stale guard for advisor panels
let cumulativeRequestId = 0; // stale guard for cumulative views
let briefRequestId = 0; // stale guard for daily brief
let ALL_MONTHS = [];    // available months from /api/meta
let ALL_SNAPSHOTS = []; // exact reporting cut-offs from the audit ledger
let currentUser = null; // authenticated/effective session user
const USER_ROLES = [
  "MIS_SQL_DEVELOPER", "NETWORK_HEAD", "HQ_FULL_ACCESS", "APP_TESTER",
  "REGIONAL_DIRECTOR", "AREA_MANAGER", "BRANCH_DIRECTOR", "BRANCH_MANAGER",
  "CUSTOMER_ADVISOR", "ADVISOR",
];

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
const briefState = { scope: "advisor", code: null, month: null };

const $ = (id) => document.getElementById(id);

$("menu-toggle").addEventListener("click", () => {
  const open = $("sidebar").classList.toggle("menu-open");
  $("menu-toggle").setAttribute("aria-expanded", String(open));
});

// Keep precise amounts in tables and expose them on the compact KPI value.
function formatKpiValue(value, fmt) {
  if (typeof value === "number" && Math.abs(value) >= 1000000 && ["eur", "int"].includes(fmt)) {
    return new Intl.NumberFormat("en-IE", {
      notation: "compact", maximumFractionDigits: 2,
      ...(fmt === "eur" ? { style: "currency", currency: "EUR" } : {}),
    }).format(value);
  }
  return formatValue(value, fmt);
}

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
    if (item.id === "data_quality" && currentUser && !currentUser.can_view_global_quality) {
      continue;
    }
    if (item.sep) {
      const div = document.createElement("div");
      div.className = "menu-sep";
      div.textContent = item.sep;
      menu.appendChild(div);
      continue;
    }
    const btn = document.createElement("button");
    btn.className = "menu-item" + (item.id === activeId ? " active" : "");
    if (item.id === activeId) btn.setAttribute("aria-current", "page");
    const labelSpan = document.createElement("span");
    labelSpan.className = "menu-label";
    labelSpan.textContent = item.label;
    btn.appendChild(labelSpan);
    btn.addEventListener("click", () => selectPage(item.id));
    menu.appendChild(btn);
  }
}

function menuItem(id) {
  return MENU.find((m) => m.id === id);
}

function syncUrl() {
  const params = new URLSearchParams();
  params.set("page", state.page);
  if (state.from) params.set("from", state.from);
  if (state.to) params.set("to", state.to);
  if (state.dim) params.set("dim", state.dim);
  if (state.asOf) params.set("as_of", state.asOf);
  params.set("attribution", state.attribution);
  history.replaceState(null, "", window.location.pathname + "?" + params.toString());
}

function restoreUrlState() {
  const params = new URLSearchParams(window.location.search);
  const page = params.get("page");
  if (page && menuItem(page)) state.page = page;
  state.from = params.get("from") || state.from;
  state.to = params.get("to") || state.to;
  state.dim = params.get("dim") || state.dim;
  state.asOf = params.get("as_of") || state.asOf;
  state.attribution = params.get("attribution") || state.attribution;
}

function selectPage(id) {
  if (id === "data_quality" && currentUser && !currentUser.can_view_global_quality) {
    id = "overview";
  }
  $("sidebar").classList.remove("menu-open");
  $("menu-toggle").setAttribute("aria-expanded", "false");
  state.page = id;
  syncUrl();
  renderMenu(id);
  const item = menuItem(id);
  $("page-title").textContent = item ? item.label : id;
  closeDrill();
  showNotice(null);
  if (item.type === "ledger") loadLedgerPage();
  else if (item.type === "advisors") loadAdvisorsPage();
  else if (item.type === "performance") loadPerformancePage();
  else if (item.type === "hierarchy") loadHierarchyPage();
  else if (item.type === "league") loadLeaguePage();
  else if (item.type === "quality") loadQualityPage();
  else if (item.type === "personal") loadPersonalPage(item.scope);
  else if (item.type === "brief") loadBriefPage();
  else loadReportPage();
}

/* ------------------------------------------------------------------ fetch */

async function fetchJSON(url, opts = {}) {
  const res = await fetch(url, { credentials: "same-origin", ...opts });
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) {
    if (res.status === 401 && !url.endsWith("/api/auth/login")) {
      showLogin("Your session has expired. Please sign in again.");
    }
    const detail = body && body.detail ? body.detail : `HTTP ${res.status}`;
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return body;
}

function showLogin(message = "") {
  currentUser = null;
  $("app").hidden = true;
  $("login-view").hidden = false;
  $("user-box").hidden = true;
  $("login-error").textContent = message;
  $("login-error").hidden = !message;
  $("login-password").value = "";
  $("login-username").focus();
}

function showDashboard() {
  $("login-view").hidden = true;
  $("app").hidden = false;
  $("user-box").hidden = false;
}

function wireDemoAccounts() {
  document.querySelectorAll(".demo-account-button").forEach((button) => {
    button.addEventListener("click", () => {
      $("login-username").value = button.dataset.demoUsername || "";
      $("login-password").value = button.dataset.demoPassword || "";
      $("login-error").hidden = true;
      $("login-submit").focus();
    });
  });
}

async function submitLogin(event) {
  event.preventDefault();
  const button = $("login-submit");
  const error = $("login-error");
  button.disabled = true;
  error.hidden = true;
  try {
    await fetchJSON("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: $("login-username").value,
        password: $("login-password").value,
      }),
    });
    await initSession();
    await initMeta();
    selectPage(state.page || "overview");
  } catch (err) {
    showLogin(err.status === 401 ? "Invalid username or password." :
      `Cannot sign in (${err.message}).`);
  } finally {
    button.disabled = false;
  }
}

async function signOut() {
  try {
    await fetchJSON("/api/auth/logout", { method: "POST" });
  } catch { /* clearing the local UI is still the right logout fallback */ }
  showLogin("You have signed out.");
}

/* ------------------------------------------------------------ layout */

function setLayout({ kpis = false, charts: showCharts = false, synthetic = false,
                     analytic = false, custom = false, fromTo = true,
                     globalPdf = false, insights = false }) {
  $("kpis").hidden = !kpis;
  $("charts").hidden = !showCharts;
  $("insights").hidden = !insights;
  $("card-synthetic").hidden = !synthetic;
  $("card-analytic").hidden = !analytic;
  $("custom-page").hidden = !custom;
  $("from-label").hidden = !fromTo;
  $("to-label").hidden = !fromTo;
  $("preset-label").hidden = !fromTo;
  $("dim-label").hidden = true;
  $("btn-charts-pdf").hidden = !globalPdf;
  if (!kpis) $("kpis").innerHTML = "";
  if (!insights) $("insights").innerHTML = "";
  // 'charts' is the global array — the parameter is renamed to avoid shadowing
  if (!showCharts) { for (const c of charts) c.destroy(); charts.length = 0; }
}

/* ------------------------------------------------------------- reports */

async function loadReportPage() {
  setLayout({ kpis: true, charts: true, synthetic: true, analytic: true,
              globalPdf: true, insights: true });
  const requestId = ++pageRequestId;
  const params = new URLSearchParams();
  if (state.from) params.set("from", state.from);
  if (state.to) params.set("to", state.to);
  if (state.dim) params.set("dim", state.dim);
  if (state.asOf) params.set("as_of", state.asOf);
  params.set("attribution", state.attribution);
  try {
    const payload = await fetchJSON(`/api/report/${state.page}?${params}`);
    if (requestId !== pageRequestId) return;
    renderReport(payload);
  } catch (err) {
    if (requestId !== pageRequestId) return;
    showNotice(`Could not load report "${state.page}": ${err.message}`);
    $("synthetic-table").innerHTML = "";
    $("analytic-table").innerHTML = "";
    $("insights").innerHTML = "";
  }
}

function scopeNote() {
  if (!currentUser || currentUser.role === "ANALYST") return "";
  return ` · data scoped to your ${currentUser.role_label}`;
}

function renderReport(payload) {
  $("page-subtitle").textContent =
    `${payload.subtitle} · ${monthLabel(payload.period.from)} – ${monthLabel(payload.period.to)}`
    + (payload.comparison
      ? ` · compared with ${monthLabel(payload.comparison.from)} – ${monthLabel(payload.comparison.to)}`
      : "") + scopeNote();
  renderKpis($("kpis"), payload.kpis || []);
  renderInsights(payload.insights || []);
  renderCharts(payload.charts || []);
  renderDimSelect(payload.dims || [], payload.dim);
  if (payload.freshness && payload.freshness.loaded_at) {
    const stale = payload.freshness.stale ? " · stale data" : "";
    $("page-subtitle").textContent += ` · refreshed ${new Date(payload.freshness.loaded_at).toLocaleTimeString("en-IE")}${stale}`;
  }
  if (payload.reporting_context && payload.reporting_context.as_of) {
    $("page-subtitle").textContent +=
      ` · as of ${payload.reporting_context.as_of} · ${payload.reporting_context.attribution} structure`;
  }
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
    card.className = `kpi status-${kpi.status || "neutral"}`;
    const value = formatValue(kpi.value, kpi.fmt);
    const neg = typeof kpi.value === "number" && kpi.value < 0;
    const label = document.createElement("div");
    label.className = "kpi-label";
    label.textContent = kpi.label;
    const valueEl = document.createElement("div");
    valueEl.className = `kpi-value ${kpi.fmt === "pct" ? "pct" : ""} ${neg ? "neg" : ""}`;
    valueEl.textContent = formatKpiValue(kpi.value, kpi.fmt);
    valueEl.title = value;
    valueEl.setAttribute("aria-label", value);
    card.appendChild(label);
    card.appendChild(valueEl);
    if (kpi.delta !== null && kpi.delta !== undefined) {
      const delta = document.createElement("div");
      const improving = typeof kpi.delta === "number" && kpi.delta >= 0;
      delta.className = `kpi-delta ${improving ? "good" : "negative"}`;
      const deltaText = formatValue(kpi.delta, kpi.delta_fmt || kpi.fmt);
      const pctText = kpi.delta_pct === null || kpi.delta_pct === undefined
        ? "" : ` (${kpi.delta_pct >= 0 ? "+" : ""}${Number(kpi.delta_pct).toFixed(2)}%)`;
      delta.textContent = `${kpi.delta_label || "vs previous period"}: ${deltaText}${pctText}`;
      card.appendChild(delta);
    }
    if (kpi.target !== null && kpi.target !== undefined) {
      const target = document.createElement("div");
      target.className = "kpi-target";
      target.textContent = `Target: ${formatValue(kpi.target, kpi.target_fmt || kpi.fmt)}`;
      card.appendChild(target);
    }
    grid.appendChild(card);
  }
}

function renderInsights(insights) {
  const grid = $("insights");
  grid.innerHTML = "";
  if (!insights.length) {
    grid.hidden = true;
    return;
  }
  grid.hidden = false;
  for (const item of insights) {
    const card = document.createElement("article");
    card.className = `insight ${item.severity || "neutral"}`;
    const title = document.createElement("div");
    title.className = "insight-title";
    title.textContent = item.title;
    const detail = document.createElement("div");
    detail.className = "insight-detail";
    detail.textContent = item.detail;
    card.appendChild(title);
    card.appendChild(detail);
    if (item.action) {
      const action = document.createElement("div");
      action.className = "insight-action";
      action.textContent = item.action;
      card.appendChild(action);
    }
    grid.appendChild(card);
  }
}

function hasChartLib() {
  return typeof window.Chart !== "undefined";
}

function renderCharts(list) {
  for (const c of charts) c.destroy();
  charts.length = 0;
  const grid = $("charts");
  grid.innerHTML = "";
  if (!list.length) { grid.hidden = true; return; }
  grid.hidden = false;
  if (!hasChartLib()) {
    grid.innerHTML = `<div class="empty">Charts are unavailable — the Chart.js CDN failed to load. Tables below still work.</div>`;
    return;
  }
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
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", spec.title || "Chart");
    box.appendChild(canvas);
    card.appendChild(head);
    card.appendChild(box);
    grid.appendChild(card);
    const chart = makeChart(canvas, spec);
    if (chart) {
      charts.push(chart);
      const base = `${state.page}_${spec.id}`;
      addButton(actions, "PNG", () => downloadChartPNG(chart, base));
      addButton(actions, "PDF", () => downloadChartPDF(chart, base));
    }
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
  if (!chart) {
    showToast("Chart export is unavailable — the Chart.js CDN failed to load.");
    return;
  }
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
  if (!chart) {
    showToast("Chart export is unavailable — the Chart.js CDN failed to load.");
    return;
  }
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
  if (!hasChartLib()) return null;
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
      borderWidth: 3,
      borderColor: "#ffffff",
      hoverOffset: 5,
    }];
  } else {
    datasets = seriesList.map((s, i) => ({
      label: s.name,
      data: s.data,
      borderColor: colors[i],
      backgroundColor: type === "line" ? colors[i] + "16" : colors[i] + "dd",
      borderRadius: type === "bar" ? 4 : 0,
      maxBarThickness: 36,
      borderWidth: type === "line" ? 2 : 1,
      fill: false,
      tension: 0.3,
      pointRadius: type === "line" ? 2 : 0,
      pointHoverRadius: 5,
    }));
  }

  return new Chart(canvas, {
    type,
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: type === "doughnut" ? "72%" : undefined,
      animation: { duration: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 350 },
      indexAxis: isHBar ? "y" : "x",
      plugins: {
        legend: {
          display: datasets.length > 1 || type === "doughnut",
          position: "bottom",
          labels: { boxWidth: 8, boxHeight: 8, usePointStyle: true, padding: 16,
                    color: "#64748b", font: { size: 11 } },
        },
      },
      scales: type === "doughnut" ? {} : (isHBar ? {
        x: {
          beginAtZero: true,
          ticks: { font: { size: 10 }, callback: compactChartNumber },
          grid: { display: false },
        },
        y: {
          type: "category",
          ticks: { autoSkip: false, font: { size: 10 } },
          grid: { display: false },
        },
      } : {
        x: { ticks: { maxRotation: 35, font: { size: 10 } }, grid: { display: false }, border: { display: false } },
        y: {
          beginAtZero: true,
          border: { display: false },
          grid: { color: "#edf1f4" },
          ticks: { font: { size: 10 }, callback: compactChartNumber },
        },
      }),
    },
  });
}

function compactChartNumber(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return v;
  return Math.abs(n) >= 1e6 ? `${(n / 1e6).toFixed(1)}M`
    : Math.abs(n) >= 1e3 ? `${(n / 1e3).toFixed(0)}k` : n;
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
    syncUrl();
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
    if (state.asOf) params.set("as_of", state.asOf);
    params.set("attribution", state.attribution);
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
  container.tabIndex = 0;
  container.setAttribute("role", "region");
  container.setAttribute("aria-label", `${tableId.replace(/[:_]/g, " ")} scrollable table`);
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
  table.setAttribute("aria-label", tableId.replace(/[:_]/g, " "));
  const thead = document.createElement("thead");
  const trh = document.createElement("tr");
  columns.forEach((c, i) => {
    const th = document.createElement("th");
    th.scope = "col";
    th.className = c.fmt === "str" ? "" : "num";
    th.tabIndex = 0;
    const isSorted = sort.col === i;
    th.setAttribute("aria-sort", isSorted ? (sort.dir === 1 ? "ascending" : "descending") : "none");
    const arrow = isSorted ? (sort.dir === 1 ? " ▲" : " ▼") : "";
    const labelEl = document.createElement("span");
    labelEl.textContent = c.label;
    th.appendChild(labelEl);
    const arrowEl = document.createElement("span");
    arrowEl.className = "arrow";
    arrowEl.setAttribute("aria-hidden", "true");
    arrowEl.textContent = arrow;
    th.appendChild(arrowEl);
    const toggleSort = () => {
      const prev = sortState[tableId] || { col: -1, dir: 1 };
      sortState[tableId] = { col: i, dir: prev.col === i ? -prev.dir : 1 };
      renderTable(container, columns, rows, tableId, drill);
    };
    th.addEventListener("click", toggleSort);
    th.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        toggleSort();
      }
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
  const pageId = pageRequestId;
  const requestId = ++drillRequestId;
  const params = new URLSearchParams({ key: code });
  if (state.from) params.set("from", state.from);
  if (state.to) params.set("to", state.to);
  if (state.asOf) params.set("as_of", state.asOf);
  params.set("attribution", state.attribution);
  try {
    const payload = await fetchJSON(`/api/drill/${drill.page}/${drill.target}?${params}`);
    if (requestId !== drillRequestId || pageId !== pageRequestId) return;
    renderDrill(payload);
  } catch (err) {
    if (requestId !== drillRequestId || pageId !== pageRequestId) return;
    showToast(`Drill-down failed: ${err.message}`);
    closeDrill();
  }
}

function renderDrill(payload) {
  const pageLabel = menuItem(payload.report_id)?.label || payload.report_id;
  $("drill-title").textContent = `${DRILL_LABELS[payload.target]} — ${payload.key}`;
  const drillSub = $("drill-sub");
  drillSub.textContent = "";
  const crumb = document.createElement("span");
  crumb.className = "drill-breadcrumb";
  crumb.textContent = `${pageLabel} → ${payload.key}`;
  drillSub.appendChild(crumb);
  let rest = ` · ${payload.rows.length} rows · ${monthLabel(payload.period.from)} – ${monthLabel(payload.period.to)}`;
  if (payload.reporting_context?.as_of) {
    rest += ` · as of ${payload.reporting_context.as_of} · ${payload.reporting_context.attribution} structure`;
  }
  drillSub.appendChild(document.createTextNode(rest));
  const actions = $("drill-actions");
  actions.innerHTML = "";
  for (const fmt of ["xlsx", "xlsb"]) {
    const params = new URLSearchParams({
      key: drillState.key, fmt, attribution: state.attribution,
    });
    if (state.from) params.set("from", state.from);
    if (state.to) params.set("to", state.to);
    if (state.asOf) params.set("as_of", state.asOf);
    const a = document.createElement("a");
    a.className = "btn small primary";
    a.href = `/api/drill/${drillState.page}/${drillState.target}?${params}`;
    a.textContent = fmt === "xlsx" ? "XLSX" : "XLSB";
    actions.appendChild(a);
  }
  renderTable($("drill-table"), payload.columns, payload.rows, "drill");
  const card = $("card-drill");
  card.hidden = false;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  card.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "nearest" });
}

function closeDrill() {
  drillState.open = false;
  $("card-drill").hidden = true;
  $("drill-table").innerHTML = "";
}

/* --------------------------------------------- point-in-time MIS pages */

function temporalParams(extra) {
  return new URLSearchParams({
    as_of: state.asOf || "",
    attribution: state.attribution,
    ...(extra || {}),
  });
}

function contextText(context) {
  const stateText = context.complete ? "reconciled" : "incomplete";
  const attribution = context.attribution
    ? ` · ${context.attribution} structure` : "";
  return `As of ${context.as_of} · data through ${context.data_through}`
    + `${attribution} · load ${context.load_id} · ${stateText}`;
}

function monthsThroughAsOf() {
  const snapshotMonth = state.asOf ? state.asOf.slice(0, 7) : null;
  return ALL_MONTHS.filter((ym) => !snapshotMonth || ym <= snapshotMonth);
}

async function loadPerformancePage(dim = "branch") {
  setLayout({ kpis: true, charts: true, custom: true, fromTo: false });
  const requestId = ++pageRequestId;
  const page = $("custom-page");
  page.innerHTML = `<div class="person-picker card">
    <label>Breakdown <select id="performance-dim">
      <option value="region">Region</option><option value="branch">Branch</option>
      <option value="advisor">Advisor</option></select></label>
  </div><section id="comparison-grid" class="comparison-grid"></section>
  <section class="card"><div class="card-head"><div><h2>Point-in-time breakdown</h2>
    <div class="card-sub">MTD actual and business-day paced plan</div></div></div>
    <div id="performance-table" class="table-wrap"></div></section>`;
  $("performance-dim").value = dim;
  $("performance-dim").onchange = () => loadPerformancePage($("performance-dim").value);
  try {
    const payload = await fetchJSON(`/api/performance?${temporalParams({ dim })}`);
    if (requestId !== pageRequestId) return;
    $("page-subtitle").textContent = contextText(payload.reporting_context) + scopeNote();
    renderKpis($("kpis"), payload.kpis || []);
    renderCharts(payload.charts || []);
    const comparisons = $("comparison-grid");
    comparisons.innerHTML = "";
    for (const code of ["DTD", "MTD", "MoM", "YTD", "YoY"]) {
      const value = payload.comparisons[code];
      if (!value) continue;
      const card = document.createElement("article");
      card.className = "comparison-card card";
      const delta = value.delta_pct === null ? "—"
        : `${value.delta_pct >= 0 ? "+" : ""}${value.delta_pct.toFixed(2)}%`;
      const codeEl = document.createElement("div");
      codeEl.className = "comparison-code";
      codeEl.textContent = code;
      const valueEl = document.createElement("div");
      valueEl.className = "comparison-value";
      valueEl.textContent = formatValue(value.current, "eur");
      const deltaEl = document.createElement("div");
      deltaEl.className = `comparison-delta ${value.delta_pct >= 0 ? "good" : "negative"}`;
      deltaEl.textContent = delta;
      const refEl = document.createElement("div");
      refEl.className = "card-sub";
      refEl.textContent = `vs ${value.reference_period.from} – ${value.reference_period.to}`;
      card.append(codeEl, valueEl, deltaEl, refEl);
      card.title = value.label;
      comparisons.appendChild(card);
    }
    renderTable($("performance-table"), payload.columns, payload.rows, `performance:${dim}`);
  } catch (err) {
    if (requestId !== pageRequestId) return;
    showNotice(`Could not load point-in-time performance: ${err.message}`);
  }
}

async function loadHierarchyPage() {
  setLayout({ custom: true, fromTo: false });
  const requestId = ++pageRequestId;
  try {
    const payload = await fetchJSON(`/api/hierarchy?${temporalParams()}`);
    if (requestId !== pageRequestId) return;
    $("page-subtitle").textContent = contextText(payload.reporting_context) + scopeNote();
    const regions = payload.regions.map((region) => `<details class="org-region" open>
      <summary>${escAttr(region.name)} <span>${region.branches.length} branches</span></summary>
      ${region.branches.map((branch) => `<details class="org-branch">
        <summary>${escAttr(branch.name)} <span>${branch.advisors.length} advisors</span></summary>
        <div class="org-advisors">${branch.advisors.map((advisor) =>
          `<span><b>${escAttr(advisor.name)}</b><small>${escAttr(advisor.role)} · since ${advisor.valid_from}</small></span>`
        ).join("")}</div></details>`).join("")}</details>`).join("");
    const changeColumns = [
      { key: "effective_date", label: "Effective date" }, { key: "advisor_code", label: "Code" },
      { key: "advisor_name", label: "Advisor" }, { key: "to_branch", label: "New branch" },
      { key: "reason", label: "Reason" },
    ];
    const changeRows = payload.changes.map((row) => changeColumns.map((column) => row[column.key]));
    $("custom-page").innerHTML = `<div class="org-grid"><section class="card org-tree">
      <div class="card-head"><div><h2>Hierarchy at snapshot</h2><div class="card-sub">Region → branch → advisor</div></div></div>
      ${regions || '<div class="empty">No entities in your scope.</div>'}</section>
      <section class="card"><div class="card-head"><div><h2>Assignment changes</h2>
      <div class="card-sub">SCD type 2 history effective by the selected date</div></div></div>
      <div id="org-change-table" class="table-wrap"></div></section></div>`;
    renderTable($("org-change-table"), changeColumns, changeRows, "org-changes");
  } catch (err) {
    if (requestId !== pageRequestId) return;
    showNotice(`Could not load organization history: ${err.message}`);
  }
}

async function loadLeaguePage(level = "advisor") {
  setLayout({ custom: true, fromTo: false });
  const requestId = ++pageRequestId;
  $("custom-page").innerHTML = `<div class="person-picker card"><label>League
    <select id="league-level"><option value="advisor">Advisors</option>
    <option value="branch">Branches</option></select></label></div>
    <section id="league-top3" class="comparison-grid" aria-label="Top 3" hidden></section>
    <div class="league-rules card" id="league-rules"></div>
    <section class="card"><div class="card-head"><div><h2>Champions League</h2>
    <div class="card-sub">Qualified leaders first; non-qualified entries remain explainable</div></div></div>
    <div id="league-table" class="table-wrap"></div></section>`;
  $("league-level").value = level;
  $("league-level").onchange = () => loadLeaguePage($("league-level").value);
  try {
    const payload = await fetchJSON(`/api/league?${temporalParams({ level })}`);
    if (requestId !== pageRequestId) return;
    $("page-subtitle").textContent = contextText(payload.reporting_context) + scopeNote();
    const rules = payload.rules;
    const rulesEl = $("league-rules");
    rulesEl.textContent = "";
    const boldScore = document.createElement("b");
    boldScore.textContent = "Transparent score:";
    rulesEl.append(
      boldScore,
      document.createTextNode(
        ` attainment ${rules.weights.attainment}% · PMTD growth ${rules.weights["PMTD growth"]}% · ` +
        `quality ${rules.weights.quality}% · activity ${rules.weights.activity}%  `),
    );
    const boldElig = document.createElement("b");
    boldElig.textContent = "Eligibility:";
    rulesEl.append(
      boldElig,
      document.createTextNode(
        ` ≥${rules.minimum_sales} sales, quality ≥${rules.minimum_quality}, ` +
        `attainment capped at ${rules.attainment_cap}%.`),
    );
    renderLeagueTop3(payload);
    renderTable($("league-table"), payload.columns, payload.rows, `league:${level}`);
  } catch (err) {
    if (requestId !== pageRequestId) return;
    showNotice(`Could not load Champions League: ${err.message}`);
  }
}

// Top 3 in the same quiet card language as the Daily performance comparisons:
// no medals, no colours — just rank, score and who holds it.
function renderLeagueTop3(payload) {
  const grid = $("league-top3");
  if (!grid) return;
  grid.innerHTML = "";
  const cols = payload.columns || [];
  const colIdx = (key) => cols.findIndex((c) => c.key === key);
  const iName = colIdx("name");
  const iScore = colIdx("score");
  const iElig = colIdx("eligible");
  const iAtt = colIdx("attainment");
  const top = (payload.rows || []).slice(0, 3);
  if (!top.length) {
    grid.hidden = true;
    return;
  }
  grid.hidden = false;
  top.forEach((row, i) => {
    const card = document.createElement("article");
    card.className = "comparison-card card";
    const codeEl = document.createElement("div");
    codeEl.className = "comparison-code";
    codeEl.textContent = `Rank ${i + 1}`;
    const valueEl = document.createElement("div");
    valueEl.className = "comparison-value";
    valueEl.textContent = `${Number(row[iScore]).toFixed(2)} pts`;
    const nameEl = document.createElement("div");
    nameEl.className = "card-sub";
    nameEl.textContent = String(row[iName]);
    const stateEl = document.createElement("div");
    stateEl.className = "card-sub";
    stateEl.textContent = row[iElig] === true
      ? `Qualified · attainment ${Number(row[iAtt]).toFixed(2)}%`
      : `Not qualified · attainment ${Number(row[iAtt]).toFixed(2)}%`;
    card.append(codeEl, valueEl, nameEl, stateEl);
    grid.appendChild(card);
  });
}

async function loadQualityPage() {
  setLayout({ custom: true, fromTo: false });
  const requestId = ++pageRequestId;
  try {
    const payload = await fetchJSON(`/api/quality?${temporalParams()}`);
    if (requestId !== pageRequestId) return;
    $("page-subtitle").textContent = contextText(payload.reporting_context);
    $("custom-page").innerHTML = `<div class="quality-banner status-${payload.overall_status.toLowerCase()}">
      Reporting gate: ${payload.overall_status}</div><div class="quality-grid">${payload.checks.map((check) =>
        `<article class="card quality-check status-${check.status.toLowerCase()}">
          <div class="quality-status">${check.status}</div><h2>${escAttr(check.label)}</h2>
          <div class="quality-value">${escAttr(String(check.value))}</div>
          <div class="card-sub">${escAttr(check.detail)}</div></article>`).join("")}</div>`;
  } catch (err) {
    if (requestId !== pageRequestId) return;
    showNotice(`Could not load data quality: ${err.message}`);
  }
}

/* ------------------------------------------------------- sales ledger */

function ledgerParams(extra) {
  const p = new URLSearchParams(extra || {});
  if (state.from) p.set("from", state.from);
  if (state.to) p.set("to", state.to);
  if (state.asOf) p.set("as_of", state.asOf);
  p.set("attribution", state.attribution);
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
  const requestId = ++pageRequestId;
  $("page-subtitle").textContent = "Full sales detail — server-side pagination and filtering";
  try {
    const first = await fetchJSON("/api/ledger?" + ledgerParams());
    if (requestId !== pageRequestId) return;
    renderLedger(first);
  } catch (err) {
    if (requestId !== pageRequestId) return;
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
  const pageId = pageRequestId;
  const requestId = ++ledgerRequestId;
  try {
    const payload = await fetchJSON("/api/ledger?" + ledgerParams());
    if (requestId !== ledgerRequestId || pageId !== pageRequestId) return;
    renderLedgerTable(payload);
  } catch (err) {
    if (requestId !== ledgerRequestId || pageId !== pageRequestId) return;
    showNotice(`Could not load the sales ledger: ${err.message}`);
  }
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
  container.tabIndex = 0;
  container.setAttribute("role", "region");
  container.setAttribute("aria-label", "Sales ledger scrollable table");
  const columns = payload.columns;
  const rows = payload.rows;
  if (!rows.length) {
    container.innerHTML = `<div class="empty">No rows match the current filters.</div>`;
    $("ledger-info").textContent = `0 of ${fmtInt.format(payload.total)} rows`;
    return;
  }
  const table = document.createElement("table");
  table.className = "grid";
  table.setAttribute("aria-label", "Sales ledger");
  const thead = document.createElement("thead");
  const trh = document.createElement("tr");
  columns.forEach((c, i) => {
    const th = document.createElement("th");
    th.scope = "col";
    th.className = c.fmt === "str" ? "" : "num";
    th.tabIndex = 0;
    const active = ledgerState.sort === c.key;
    th.setAttribute("aria-sort", active ? (ledgerState.dir === "asc" ? "ascending" : "descending") : "none");
    const arrow = active ? (ledgerState.dir === "asc" ? " ▲" : " ▼") : "";
    const labelEl = document.createElement("span");
    labelEl.textContent = c.label;
    th.appendChild(labelEl);
    const arrowEl = document.createElement("span");
    arrowEl.className = "arrow";
    arrowEl.setAttribute("aria-hidden", "true");
    arrowEl.textContent = arrow;
    th.appendChild(arrowEl);
    const toggleLedgerSort = () => {
      if (active) {
        ledgerState.dir = ledgerState.dir === "asc" ? "desc" : "asc";
      } else {
        ledgerState.sort = c.key;
        ledgerState.dir = c.key === "sale_date" ? "desc" : "asc";
      }
      ledgerState.page = 1;
      fetchLedgerPage().catch((err) => showNotice(`Sort failed: ${err.message}`));
    };
    th.addEventListener("click", toggleLedgerSort);
    th.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        toggleLedgerSort();
      }
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
  const requestId = ++pageRequestId;
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
    if (requestId !== pageRequestId) return;
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
    personState.code = resetCodeIfOutOfScope(advisors, personState.code);
    sel.value = personState.code;
    // role-based scope may lock the picker to a single advisor
    sel.disabled = advisors.length <= 1;
    if (advisors.length === 1) {
      sel.title = `Your role (${currentUser.role_label}) restricts this picker`;
    }
    fillAdvisorSelect();
    if (personState.code) await loadAdvisorPanel();
  } catch (err) {
    if (requestId !== pageRequestId) return;
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
  if (!panel) return;
  const pageId = pageRequestId;
  const requestId = ++panelRequestId;
  panel.innerHTML = `<div class="empty">Loading…</div>`;
  try {
    const params = state.asOf ? `?as_of=${encodeURIComponent(state.asOf)}` : "";
    const payload = await fetchJSON(`/api/people/advisors/${personState.code}${params}`);
    if (requestId !== panelRequestId || pageId !== pageRequestId) return;
    renderAdvisorPanel(payload);
  } catch (err) {
    if (requestId !== panelRequestId || pageId !== pageRequestId) return;
    panel.innerHTML = `<div class="empty">Could not load advisor: ${err.message}</div>`;
  }
}

function renderAdvisorPanel(payload) {
  const info = payload.info;
  const s = payload.summary;
  const initials = (info.first_name || "?").charAt(0) + (info.last_name || "").charAt(0);
  const panel = $("advisor-panel");
  if (payload.reporting_context?.as_of) {
    $("page-subtitle").textContent = `Advisor performance · ${contextText(payload.reporting_context)}`;
  }
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
    const asOf = state.asOf ? `?as_of=${encodeURIComponent(state.asOf)}` : "";
    a.href = `/api/people/advisors/${encodeURIComponent(payload.code)}/export/${fmt}${asOf}`;
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
  const requestId = ++pageRequestId;
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
  const selectableMonths = monthsThroughAsOf();
  for (const ym of selectableMonths) {
    const opt = document.createElement("option");
    opt.value = ym;
    opt.textContent = monthLabel(ym);
    monthSel.appendChild(opt);
  }
  if (!personState.month || !selectableMonths.includes(personState.month)) {
    personState.month = selectableMonths[selectableMonths.length - 1];
  }
  monthSel.value = personState.month;
  monthSel.onchange = () => { personState.month = monthSel.value; loadCumulative(scope); };

  try {
    const data = await fetchJSON(scope === "branch"
      ? "/api/people/branches" : "/api/people/advisors");
    if (requestId !== pageRequestId) return;
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
    personState.code = resetCodeIfOutOfScope(items, personState.code);
    sel.value = personState.code;
    sel.disabled = items.length <= 1;
    if (items.length === 1) {
      sel.title = `Your role (${currentUser.role_label}) restricts this picker`;
    }
    if (personState.code) await loadCumulative(scope);
  } catch (err) {
    if (requestId !== pageRequestId) return;
    showNotice(`Could not load ${label}s: ${err.message}`);
  }
}

async function loadCumulative(scope) {
  const pageId = pageRequestId;
  const requestId = ++cumulativeRequestId;
  try {
    const payload = await fetchJSON(
      `/api/people/cumulative?scope=${scope}&code=${encodeURIComponent(personState.code)}`
      + `&month=${personState.month}`
      + (state.asOf && state.asOf.startsWith(personState.month) ? `&as_of=${state.asOf}` : ""));
    if (requestId !== cumulativeRequestId || pageId !== pageRequestId) return;
    renderCumulative(payload);
  } catch (err) {
    if (requestId !== cumulativeRequestId || pageId !== pageRequestId) return;
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
      + `&code=${encodeURIComponent(personState.code)}&month=${personState.month}`
      + (state.asOf && state.asOf.startsWith(personState.month) ? `&as_of=${state.asOf}` : "");
    a.textContent = fmt === "xlsx" ? "XLSX" : "XLSB";
    actions.appendChild(a);
  }
  renderTable($("personal-table"), payload.columns, payload.rows,
              `${payload.scope}:${payload.month}`, null);
}

/* ------------------------------------------------------- session (roles) */

function renderUserBadge() {
  const badge = $("user-badge");
  if (!currentUser) { badge.hidden = true; return; }
  badge.hidden = false;
  badge.textContent = currentUser.is_impersonating
    ? `Acting as ${currentUser.name} · ${currentUser.role_label}`
    : `Signed in as ${currentUser.name} · ${currentUser.role_label}`;

  const acting = $("acting-as");
  if (currentUser.is_impersonating) {
    acting.hidden = false;
    acting.textContent = `Tester session: ${currentUser.authenticated_username}`;
  } else {
    acting.hidden = true;
    acting.textContent = "";
  }
}

async function initSession() {
  const me = await fetchJSON("/api/auth/me");
  currentUser = me.user;
  showDashboard();
  renderUserBadge();
  const sel = $("user-select");
  sel.innerHTML = "";
  sel.hidden = !currentUser.can_switch_persona;
  $("cache-refresh").hidden = !currentUser.can_refresh_cache;

  if (currentUser.can_switch_persona) {
    const users = await fetchJSON("/api/session/users");
    const roles = [...USER_ROLES,
      ...users.users.map((u) => u.role).filter((role) => !USER_ROLES.includes(role))];
    for (const role of [...new Set(roles)]) {
      const group = users.users.filter((u) => u.role === role);
      if (!group.length) continue;
      const og = document.createElement("optgroup");
      og.label = group[0].role_label;
      for (const u of group) {
        const opt = document.createElement("option");
        opt.value = u.code;
        opt.textContent = u.name;
        og.appendChild(opt);
      }
      sel.appendChild(og);
    }
    sel.value = currentUser.code;
  }
  sel.onchange = async () => {
    const previousCode = currentUser.code;
    try {
      const res = await fetchJSON("/api/session/user", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: sel.value }),
      });
      currentUser = res.user;
      renderUserBadge();
      showToast(`Signed in as ${currentUser.name} (${currentUser.role_label}). Scope updated.`);
      selectPage(state.page); // re-scope pickers on the current page
    } catch (err) {
      showToast(`Switch failed: ${err.message}`);
      sel.value = previousCode;
    }
  };
}

function resetCodeIfOutOfScope(items, code) {
  return items.some((i) => i.code === code) ? code : (items[0] ? items[0].code : null);
}

/* ------------------------------------------------ daily MIS brief (print) */

async function loadBriefPage() {
  setLayout({ custom: true, fromTo: false });
  const requestId = ++pageRequestId;
  $("page-subtitle").textContent =
    "One-page daily brief: your results plus the top network KPIs — ready to print";
  const page = $("custom-page");
  page.innerHTML = `
    <div class="person-picker card no-print">
      <label>Scope
        <select id="brief-scope">
          <option value="advisor">My results</option>
          <option value="branch">My branch</option>
        </select>
      </label>
      <label id="brief-entity-label">Advisor
        <select id="brief-entity" class="person-select"></select>
      </label>
      <label>Month <select id="brief-month"></select></label>
      <button class="btn primary" id="brief-print" type="button">Print / PDF</button>
    </div>
    <div class="brief" id="brief-root"></div>`;

  $("brief-print").addEventListener("click", () => window.print());

  const monthSel = $("brief-month");
  monthSel.innerHTML = "";
  const selectableMonths = monthsThroughAsOf();
  for (const ym of selectableMonths) {
    const opt = document.createElement("option");
    opt.value = ym;
    opt.textContent = monthLabel(ym);
    monthSel.appendChild(opt);
  }
  if (!briefState.month || !selectableMonths.includes(briefState.month)) {
    briefState.month = selectableMonths[selectableMonths.length - 1];
  }
  monthSel.value = briefState.month;
  monthSel.onchange = () => { briefState.month = monthSel.value; loadBrief(); };

  $("brief-scope").value = briefState.scope;
  $("brief-scope").onchange = async () => {
    briefState.scope = $("brief-scope").value;
    briefState.code = null; // re-pick in the new scope
    await fillBriefEntity();
  };
  await fillBriefEntity(requestId);
}

async function fillBriefEntity(pageId = pageRequestId) {
  const entityRequestId = ++briefRequestId;
  const scope = briefState.scope;
  const isAdvisor = scope === "advisor";
  $("brief-entity-label").firstChild.textContent = isAdvisor ? "Advisor" : "Branch";
  const sel = $("brief-entity");
  sel.innerHTML = "<option>Loading…</option>";
  try {
    const data = await fetchJSON(isAdvisor ? "/api/people/advisors" : "/api/people/branches");
    if (entityRequestId !== briefRequestId || pageId !== pageRequestId) return;
    const items = isAdvisor ? (data.advisors || []) : (data.branches || []);
    sel.innerHTML = "";
    for (const it of items) {
      const opt = document.createElement("option");
      opt.value = it.code;
      opt.textContent = isAdvisor
        ? `${it.name} — ${it.role || ""} · ${it.branch_name || it.branch_code}`
        : `${it.name} — ${it.city || ""}`;
      sel.appendChild(opt);
    }
    briefState.code = resetCodeIfOutOfScope(items, briefState.code);
    sel.value = briefState.code;
    sel.disabled = items.length <= 1;
    sel.onchange = () => { briefState.code = sel.value; loadBrief(); };
    if (briefState.code) await loadBrief(pageId, entityRequestId);
  } catch (err) {
    if (entityRequestId !== briefRequestId || pageId !== pageRequestId) return;
    $("brief-root").innerHTML = `<div class="empty">Could not load: ${err.message}</div>`;
  }
}

async function loadBrief(pageId, entityId) {
  const linked = pageId !== undefined;
  const myPageId = linked ? pageId : pageRequestId;
  const myId = linked ? entityId : ++briefRequestId;
  const root = $("brief-root");
  if (!root) return;
  root.innerHTML = `<div class="empty">Loading…</div>`;
  try {
    const overviewParams = new URLSearchParams({ from: briefState.month, to: briefState.month });
    const [cum, overview] = await Promise.all([
      fetchJSON(`/api/people/cumulative?scope=${briefState.scope}`
        + `&code=${encodeURIComponent(briefState.code)}&month=${briefState.month}`
        + (state.asOf && state.asOf.startsWith(briefState.month) ? `&as_of=${state.asOf}` : "")),
      fetchJSON(`/api/report/overview?${overviewParams}`
        + (state.asOf ? `&as_of=${encodeURIComponent(state.asOf)}` : "")
        + `&attribution=${encodeURIComponent(state.attribution)}`),
    ]);
    if (myId !== briefRequestId || myPageId !== pageRequestId) return;
    renderBrief(cum, overview);
  } catch (err) {
    if (myId !== briefRequestId || myPageId !== pageRequestId) return;
    root.innerHTML = `<div class="empty">Could not load the brief: ${err.message}</div>`;
  }
}

function renderBrief(cum, overview) {
  const info = cum.info;
  const rows = cum.rows;
  const last = rows.length ? rows[rows.length - 1] : null;
  const kpis = {};
  for (const k of cum.kpis) kpis[k.key] = k.value;
  const ov = {};
  for (const k of (overview.kpis || [])) ov[k.key] = k.value;

  const days = rows.length;
  const latestDay = last ? last[1] : days;
  const elapsedPacing = cum.reporting_context?.elapsed_pacing_days || days;
  const totalPacing = cum.reporting_context?.total_pacing_days || days;
  const dailyAvg = elapsedPacing ? round2(kpis.mtd / elapsedPacing) : null;
  const daysLeft = Math.max(0, totalPacing - elapsedPacing);
  const runRate = daysLeft > 0 && kpis.plan ? round2((kpis.plan - kpis.mtd) / daysLeft) : null;

  const root = $("brief-root");
  root.innerHTML = `
    <header class="brief-head">
      <div class="brief-brand">Sales Network MIS</div>
      <div class="brief-title">Daily MIS Brief — ${escAttr(info.name)}</div>
      <div class="brief-meta">
        ${briefState.scope === "advisor" ? escAttr(info.role || "") + " · " : ""}
        ${escAttr(info.branch_name || info.city || "")}
        ${info.region_name ? ` · ${escAttr(info.region_name)}` : ""}
        &nbsp;·&nbsp; ${escAttr(monthLabel(cum.month))}
        &nbsp;·&nbsp; as of day ${latestDay} of ${days}
      </div>
    </header>

    <div class="brief-net">
      <span class="brief-net-title">Network (top KPIs)</span>
      <span>Sales in period <b>${formatValue(ov.sales, "eur")}</b></span>
      <span>Loan volume <b>${formatValue(ov.loan_volume, "eur")}</b></span>
      <span>Clients acquired <b>${formatValue(ov.acquired, "int")}</b></span>
      <span>Campaign ROI <b>${formatValue(ov.campaign_roi, "pct")}</b></span>
    </div>

    <div class="brief-kpis">
      <div class="kpi"><div class="kpi-label">Month to date</div>
        <div class="kpi-value">${formatValue(kpis.mtd, "eur")}</div></div>
      <div class="kpi"><div class="kpi-label">Monthly plan</div>
        <div class="kpi-value">${formatValue(kpis.plan, "eur")}</div></div>
      <div class="kpi"><div class="kpi-label">Plan attainment</div>
        <div class="kpi-value pct">${formatValue(kpis.attainment, "pct")}</div></div>
      <div class="kpi"><div class="kpi-label">vs previous month</div>
        <div class="kpi-value">${formatValue(kpis.vs_prev, "pct")}</div></div>
      <div class="kpi"><div class="kpi-label">Daily average</div>
        <div class="kpi-value">${formatValue(dailyAvg, "eur")}</div></div>
      <div class="kpi"><div class="kpi-label">Run-rate to plan</div>
        <div class="kpi-value">${formatValue(runRate, "eur")}</div></div>
    </div>

    <div class="brief-chart"><canvas id="brief-canvas"></canvas></div>

    <div class="card brief-table-card">
      <div class="card-head"><div><h2>Day-by-day cumulative</h2>
        <div class="card-sub">Daily sales, cumulative, prorated plan and previous month at the same point</div></div></div>
      <div class="table-wrap" id="brief-table"></div>
    </div>

    <footer class="brief-foot">
      Generated ${new Date().toLocaleString("en-IE")} · data source: cached MIS_* tables (Netezza JUST_DATA) ·
      confidential — for internal use only
    </footer>`;

  // the brief owns its chart: drop any charts from the previous page
  for (const c of charts) c.destroy();
  charts.length = 0;
  const spec = cum.charts[0];
  if (spec && hasChartLib()) {
    $("brief-canvas").setAttribute("role", "img");
    $("brief-canvas").setAttribute("aria-label", spec.title || "Cumulative sales chart");
    const briefChart = makeChart($("brief-canvas"), spec);
    if (briefChart) charts.push(briefChart);
  }
  renderTable($("brief-table"), cum.columns, rows, "brief", null);
}

function round2(v) {
  return Math.round(v * 100) / 100;
}

/* ------------------------------------------------------------------ cache */

async function refreshCache() {
  const btn = $("cache-refresh");
  btn.disabled = true;
  btn.textContent = "Reloading…";
  try {
    await fetchJSON("/api/cache/refresh", { method: "POST" });
    showToast("Tables reloaded from Netezza. Derived caches cleared.");
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
  const loadedValues = tables.map((t) => t.loaded_at).filter(Boolean).sort();
  const loadedAt = loadedValues.length
    ? new Date(loadedValues[loadedValues.length - 1]).toLocaleTimeString("en-IE") : "—";
  const age = Number.isFinite(c.cache_age_seconds)
    ? `${Math.floor(c.cache_age_seconds / 3600)}h ${Math.floor(c.cache_age_seconds % 3600 / 60)}m`
    : "—";
  const checkedAt = c.control_checked_at
    ? new Date(c.control_checked_at).toLocaleTimeString("en-IE") : "—";
  const version = c.dataset_version == null ? "—" : `v${c.dataset_version}`;
  const box = $("cache-info");
  box.textContent = "";
  box.appendChild(document.createTextNode(
    `${total} tables in memory, ${fmtInt.format(rows)} rows, ` +
    `loaded ${loadedAt} · dataset ${version}, checked ${checkedAt}, age ${age} · ` +
    `hits ${fmtInt.format(c.hits)} / misses ${fmtInt.format(c.misses)}`));
  if (c.stale) {
    box.appendChild(document.createElement("br"));
    const stale = document.createElement("span");
    stale.className = "err";
    stale.textContent = `Warning: freshness not confirmed for ${Math.round((c.max_unconfirmed_seconds || 0) / 3600)}h.`;
    box.appendChild(stale);
  }
  if (c.last_error) {
    box.appendChild(document.createElement("br"));
    const err = document.createElement("span");
    err.className = "err";
    err.textContent = `last error: ${c.last_error}`;
    box.appendChild(err);
  }
  if (c.control_error) {
    box.appendChild(document.createElement("br"));
    const err = document.createElement("span");
    err.className = "err";
    err.textContent = `control table: ${c.control_error}`;
    box.appendChild(err);
  }
}

function selectPeriodPreset(value) {
  if (!ALL_MONTHS.length || value === "custom") return;
  const latest = ALL_MONTHS.length - 1;
  if (value === "last3") {
    state.from = ALL_MONTHS[Math.max(0, latest - 2)];
    state.to = ALL_MONTHS[latest];
  } else if (value === "last12") {
    state.from = ALL_MONTHS[Math.max(0, latest - 11)];
    state.to = ALL_MONTHS[latest];
  } else if (value === "ytd") {
    const year = ALL_MONTHS[latest].slice(0, 4);
    state.from = ALL_MONTHS.find((ym) => ym.slice(0, 4) === year) || ALL_MONTHS[0];
    state.to = ALL_MONTHS[latest];
  }
  $("select-from").value = state.from;
  $("select-to").value = state.to;
  syncUrl();
  selectPage(state.page);
}

function detectPeriodPreset() {
  const latest = ALL_MONTHS.length - 1;
  if (latest < 0) return "custom";
  const year = ALL_MONTHS[latest].slice(0, 4);
  const ytdFrom = ALL_MONTHS.find((ym) => ym.slice(0, 4) === year) || ALL_MONTHS[0];
  if (state.to === ALL_MONTHS[latest] && state.from === ALL_MONTHS[Math.max(0, latest - 2)]) {
    return "last3";
  }
  if (state.to === ALL_MONTHS[latest] && state.from === ALL_MONTHS[Math.max(0, latest - 11)]) {
    return "last12";
  }
  if (state.from === ytdFrom && state.to === ALL_MONTHS[latest]) return "ytd";
  return "custom";
}

/* ------------------------------------------------------------------ init */

async function initMeta() {
  const meta = await fetchJSON("/api/meta");
  ALL_MONTHS = meta.months || [];
  ALL_SNAPSHOTS = meta.snapshot_dates || [];
  if (!state.asOf || !ALL_SNAPSHOTS.includes(state.asOf)) {
    state.asOf = meta.latest_snapshot || null;
  }
  const asOfSel = $("select-as-of");
  asOfSel.innerHTML = "";
  for (const value of ALL_SNAPSHOTS) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    asOfSel.appendChild(option);
  }
  asOfSel.value = state.asOf;
  asOfSel.onchange = () => {
    state.asOf = asOfSel.value;
    const month = state.asOf.slice(0, 7);
    if (ALL_MONTHS.includes(month)) state.to = month;
    syncUrl();
    selectPage(state.page);
  };
  $("select-attribution").value = state.attribution;
  $("select-attribution").onchange = () => {
    state.attribution = $("select-attribution").value;
    syncUrl();
    selectPage(state.page);
  };
  const fromSel = $("select-from");
  const toSel = $("select-to");
  fromSel.innerHTML = "";
  toSel.innerHTML = "";
  for (const ym of ALL_MONTHS) {
    const o1 = document.createElement("option");
    o1.value = ym; o1.textContent = monthLabel(ym);
    const o2 = document.createElement("option");
    o2.value = ym; o2.textContent = monthLabel(ym);
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
    if (state.from > state.to) {
      [state.from, state.to] = [state.to, state.from];
    }
  }
  fromSel.value = state.from;
  toSel.value = state.to;
  fromSel.onchange = () => {
    state.from = fromSel.value;
    $("select-preset").value = "custom";
    syncUrl();
    selectPage(state.page);
  };
  toSel.onchange = () => {
    state.to = toSel.value;
    $("select-preset").value = "custom";
    syncUrl();
    selectPage(state.page);
  };
  $("select-preset").value = detectPeriodPreset();
  $("select-preset").onchange = () => selectPeriodPreset($("select-preset").value);
  renderCacheInfo(meta);
}

(async function main() {
  wireDemoAccounts();
  $("login-form").addEventListener("submit", submitLogin);
  $("logout-button").addEventListener("click", signOut);
  $("cache-refresh").addEventListener("click", refreshCache);
  $("drill-close").addEventListener("click", closeDrill);
  $("btn-charts-pdf").addEventListener("click", downloadAllChartsPDF);
  restoreUrlState();
  try {
    await initSession();
    await initMeta();
    selectPage(state.page || "overview");
  } catch (err) {
    if (err.status === 401) {
      showLogin("");
    } else {
      showLogin(`Cannot reach the backend (${err.message}). Is the server running with seeded tables?`);
    }
  }
})();
