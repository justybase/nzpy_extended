/* Sales Decision Cockpit — the UI follows a decision path, not a report catalogue. */

const $ = (id) => document.getElementById(id);
const charts = [];
const state = {
  page: new URLSearchParams(window.location.search).get("page") || "cockpit",
  week: null,
  lookback: "12",
  scope: "network",
  region: "",
  unit: "",
  metric: "sales_amount",
  meta: null,
};

const esc = (value) => String(value ?? "")
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;").replaceAll("'", "&#039;");

function fmt(value, kind = "str") {
  if (value === null || value === undefined) return "—";
  if (kind === "pct") return `${(Number(value) * 100).toFixed(1)}%`;
  if (kind === "eur") return `€${Number(value).toLocaleString("en-GB", { maximumFractionDigits: 0 })}`;
  if (kind === "int") return Number(value).toLocaleString("en-GB", { maximumFractionDigits: 0 });
  return esc(value);
}

function queryParams(extra = {}) {
  const params = new URLSearchParams({
    week: state.week || "",
    lookback: state.lookback,
    scope: state.scope,
    region: state.region,
    unit: state.unit,
    metric: state.metric,
    ...extra,
  });
  for (const [key, value] of [...params.entries()]) if (!value) params.delete(key);
  return params;
}

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
  return body;
}

function setNotice(message, stale = false) {
  const notice = $("notice");
  notice.hidden = !message;
  notice.className = `notice${stale ? " stale" : ""}`;
  notice.textContent = message || "";
}

function setPage(page) {
  state.page = page;
  for (const button of document.querySelectorAll(".menu-item")) {
    button.classList.toggle("active", button.dataset.page === page);
  }
  const url = new URL(window.location.href);
  url.searchParams.set("page", page);
  window.history.replaceState({}, "", url);
  $("sidebar").classList.remove("menu-open");
  $("menu-toggle").setAttribute("aria-expanded", "false");
}

function syncControls() {
  $("select-week").value = state.week || "";
  $("select-lookback").value = state.lookback;
  $("select-scope").value = state.scope;
  $("select-region").value = state.region;
  $("select-unit").value = state.unit;
  $("select-metric").value = state.metric;
  $("region-control").hidden = state.scope === "unit";
  $("unit-control").hidden = state.scope !== "unit";
}

function readControls() {
  state.week = $("select-week").value;
  state.lookback = $("select-lookback").value;
  state.scope = $("select-scope").value;
  state.region = $("select-region").value;
  state.unit = $("select-unit").value;
  state.metric = $("select-metric").value;
  if (state.scope === "unit") state.region = "";
  if (state.scope !== "unit") state.unit = "";
  if (state.scope === "unit" && !state.unit && state.meta?.units?.length) state.unit = state.meta.units[0].code;
  syncControls();
}

function cacheText(cache) {
  if (!cache) return "No cache metadata";
  const rows = Object.values(cache.tables || {}).reduce((sum, item) => sum + Number(item.rows || 0), 0);
  const status = cache.status || "UNKNOWN";
  return `${status} · ${Object.keys(cache.tables || {}).length} tables · ${rows.toLocaleString("en-GB")} rows`;
}

function renderCache(cache) {
  $("cache-info").innerHTML = `${esc(cacheText(cache))}<br><span>${esc(cache.load_id || "No load id")}</span>`;
}

function selectOptions(select, items, emptyLabel = "All") {
  select.innerHTML = `<option value="">${esc(emptyLabel)}</option>` + items.map(item =>
    `<option value="${esc(item.code)}">${esc(item.label)}</option>`).join("");
}

function initMeta(meta) {
  state.meta = meta;
  state.week = state.week && meta.weeks.includes(state.week) ? state.week : meta.latest_week;
  selectOptions($("select-week"), meta.weeks.map(week => ({ code: week, label: `Week of ${week}` })), "Latest available");
  selectOptions($("select-region"), meta.regions, "All regions");
  selectOptions($("select-unit"), meta.units, "Choose a unit");
  $("select-metric").innerHTML = meta.metrics.map(item => `<option value="${esc(item.code)}">${esc(item.label)}</option>`).join("");
  syncControls();
  renderCache(meta.cache);
}

function renderHead(title, subtitle) {
  $("page-title").textContent = title;
  $("page-subtitle").textContent = subtitle || "";
}

function destroyCharts() {
  for (const chart of charts) chart.destroy();
  charts.length = 0;
}

function chartColor(name) {
  return name === "Actual" ? "#007f82" : name === "Target" ? "#a8b9be" : "#4579a8";
}

function renderCharts(specs) {
  destroyCharts();
  for (const spec of specs || []) {
    const canvas = document.querySelector(`canvas[data-chart-id="${CSS.escape(spec.id)}"]`);
    if (!canvas) continue;
    if (typeof Chart === "undefined") {
      renderFallbackChart(canvas, spec);
      continue;
    }
    const isDoughnut = spec.type === "doughnut";
    const chart = new Chart(canvas, {
      type: isDoughnut ? "doughnut" : spec.type === "bar" ? "bar" : "line",
      data: {
        labels: spec.labels,
        datasets: spec.series.map(series => ({
          label: series.name,
          data: series.data,
          borderColor: chartColor(series.name),
          backgroundColor: isDoughnut ? ["#c7464d", "#b8781c", "#2c8666"] : chartColor(series.name),
          borderWidth: 2,
          fill: false,
          tension: .28,
          borderRadius: spec.type === "bar" ? 4 : 0,
        })),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: isDoughnut || spec.series.length > 1, position: "bottom", labels: { boxWidth: 10, font: { size: 10 } } } },
        scales: isDoughnut ? {} : { x: { ticks: { color: "#6d7d87", font: { size: 9 }, maxRotation: 45 }, grid: { display: false } }, y: { ticks: { color: "#6d7d87", font: { size: 9 } }, grid: { color: "#e8eef0" } } },
      },
    });
    charts.push(chart);
  }
}

function renderFallbackChart(canvas, spec) {
  const replacement = document.createElement("div");
  replacement.className = "chart-fallback";
  if (spec.type === "doughnut") {
    const values = spec.series[0]?.data || [];
    const total = values.reduce((sum, value) => sum + Number(value || 0), 0) || 1;
    let cursor = 0;
    const stops = values.map((value, index) => {
      const start = cursor / total * 360;
      cursor += Number(value || 0);
      const end = cursor / total * 360;
      return `${["#c7464d", "#b8781c", "#2c8666"][index % 3]} ${start}deg ${end}deg`;
    }).join(", ");
    replacement.innerHTML = `<div class="fallback-donut-wrap"><div class="fallback-donut" style="background:conic-gradient(${stops})"></div><div class="fallback-legend">${(spec.labels || []).map((label, index) => `<span style="--fallback-color:${["#c7464d", "#b8781c", "#2c8666"][index % 3]}">${esc(label)}</span>`).join("")}</div></div>`;
    canvas.replaceWith(replacement);
    return;
  }
  const width = 640;
  const height = 250;
  const padding = { left: 42, right: 18, top: 18, bottom: 35 };
  const allValues = (spec.series || []).flatMap(series => series.data.map(value => Number(value || 0)));
  const min = Math.min(0, ...allValues);
  const max = Math.max(1, ...allValues);
  const x = index => padding.left + (spec.labels.length <= 1 ? 0 : index * (width - padding.left - padding.right) / (spec.labels.length - 1));
  const y = value => height - padding.bottom - (Number(value || 0) - min) / (max - min || 1) * (height - padding.top - padding.bottom);
  const grid = [0, .5, 1].map(step => {
    const value = min + (max - min) * step;
    const yy = y(value);
    return `<line x1="${padding.left}" x2="${width - padding.right}" y1="${yy}" y2="${yy}" stroke="#e8eef0"/><text x="4" y="${yy + 3}">${esc(Math.round(value).toLocaleString("en-GB"))}</text>`;
  }).join("");
  const labels = (spec.labels || []).map((label, index) => `<text x="${x(index)}" y="${height - 8}" text-anchor="middle" transform="rotate(-35 ${x(index)} ${height - 8})">${esc(String(label).slice(0, 12))}</text>`).join("");
  let marks = "";
  if (spec.type === "bar") {
    const series = spec.series[0]?.data || [];
    const barWidth = Math.max(8, Math.min(34, (width - padding.left - padding.right) / Math.max(series.length, 1) * .55));
    marks = series.map((value, index) => `<rect x="${x(index) - barWidth / 2}" y="${Math.min(y(value), y(0))}" width="${barWidth}" height="${Math.abs(y(value) - y(0))}" rx="3" fill="#4579a8"/>`).join("");
  } else {
    marks = (spec.series || []).map(series => {
      const points = series.data.map((value, index) => `${x(index)},${y(value)}`).join(" ");
      return `<polyline points="${points}" fill="none" stroke="${chartColor(series.name)}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>`;
    }).join("");
  }
  replacement.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(spec.title)}">${grid}${marks}${labels}</svg>`;
  canvas.replaceWith(replacement);
}

function chartCards(specs) {
  return `<div class="two-column chart-row">${(specs || []).map(spec => `<section class="card"><div class="card-head"><div><h2>${esc(spec.title)}</h2><div class="card-sub">Rendered from the selected closed-week context.</div></div></div><div class="card-body"><div class="chart-box${spec.id === "status" ? " short" : ""}"><canvas data-chart-id="${esc(spec.id)}" aria-label="${esc(spec.title)}"></canvas></div></div></section>`).join("")}</div>`;
}

function exportLinks(path, extra = {}) {
  const params = queryParams(extra);
  return `<a class="export-link" href="/api/export/${path}/xlsx?${params}">XLSX</a><a class="export-link" href="/api/export/${path}/xlsb?${params}">XLSB</a>`;
}

function renderHeadlines(items) {
  return `<div class="headline-grid">${items.map(item => `<div class="headline ${esc(item.status)}"><div class="headline-label">${esc(item.label)}</div><div class="headline-value">${fmt(item.value, item.fmt)}</div><div class="headline-detail">${esc(item.detail || "")}</div></div>`).join("")}</div>`;
}

function renderNarrative(items) {
  return `<div class="narrative-grid">${items.map(item => `<article class="narrative"><div class="narrative-kind">${esc(item.kind)}</div><div class="narrative-title">${esc(item.title)}</div><div class="narrative-detail">${esc(item.detail)}</div></article>`).join("")}</div>`;
}

function renderStatus(counts) {
  return `<div class="status-strip"><div class="status-card critical"><div class="status-label">Critical</div><div class="status-value">${counts.critical || 0}</div><div class="status-detail">Intervention threshold crossed</div></div><div class="status-card warning"><div class="status-label">Watch</div><div class="status-value">${counts.warning || 0}</div><div class="status-detail">Leading indicator at risk</div></div><div class="status-card good"><div class="status-label">On track</div><div class="status-value">${counts.good || 0}</div><div class="status-detail">Pace and pipeline are healthy</div></div><div class="status-card"><div class="status-label">Total in view</div><div class="status-value">${Object.values(counts).reduce((a, b) => a + Number(b || 0), 0)}</div><div class="status-detail">Rows are sorted by urgency</div></div></div>`;
}

function renderExceptionTable(rows, metric, drillable = false) {
  const fmtKind = metric === "sales_count" ? "int" : metric === "margin_amount" ? "eur" : "eur";
  if (!rows?.length) return `<div class="empty">No rows match the selected context.</div>`;
  const rowClass = drillable ? "exception-row" : "";
  return `<div class="table-wrap"><table><thead><tr><th>Status</th><th>Entity</th><th>Actual</th><th>Target</th><th>Gap</th><th>Attainment</th><th>4-week pace</th><th>Pipeline</th><th>Why it matters</th></tr></thead><tbody>${rows.map(row => `<tr class="${rowClass}" data-unit-code="${esc(row.code)}"><td><span class="pill ${esc(row.severity)}">${esc(row.severity)}</span></td><td><div class="entity">${esc(row.entity)}<small>${esc(row.parent)} · ${esc(row.code)}</small></div></td><td class="num">${fmt(row.actual, fmtKind)}</td><td class="num">${fmt(row.target, fmtKind)}</td><td class="num ${row.gap < 0 ? "negative" : "positive"}">${fmt(row.gap, fmtKind)}<small> ${fmt(row.gap_pct, "pct")}</small></td><td class="num">${fmt(row.attainment, "pct")}</td><td class="num">${fmt(row.forecast_attainment, "pct")}</td><td class="num">${fmt(row.pipeline_coverage, "pct")}</td><td>${esc(row.issue)}</td></tr>`).join("")}</tbody></table></div>`;
}

function renderCockpit(payload) {
  renderHead("Control tower", payload.subtitle);
  const fresh = payload.freshness || {};
  setNotice(`Data ${fresh.status || "UNKNOWN"} · source through ${fresh.source_watermark || "—"} · load ${fresh.load_id || "—"}`, !fresh.reconciled);
  const drillable = payload.scope !== "unit";
  const exceptionHint = drillable ? "Click a row to move from the signal to its contributing drivers." : "Seller-level exceptions are shown for review; use the unit context for driver analysis.";
  $("page-content").innerHTML = renderStatus(payload.status_counts) + renderHeadlines(payload.headlines) + renderNarrative(payload.narrative) + chartCards(payload.charts) + `<section class="card"><div class="card-head"><div><h2>Exceptions requiring attention</h2><div class="card-sub">${exceptionHint}</div></div><div class="card-actions">${exportLinks("cockpit")}</div></div>${renderExceptionTable(payload.exceptions, payload.metric, drillable)}</section>`;
  renderCharts(payload.charts);
  for (const row of document.querySelectorAll(".exception-row")) row.addEventListener("click", () => {
    const code = row.dataset.unitCode;
    state.page = "drivers";
    state.scope = "unit";
    state.region = "";
    state.unit = code;
    setPage("drivers");
    syncControls();
    loadPage();
  });
}

function renderBridge(bridge, metric) {
  if (!bridge?.length) return `<div class="empty">No bridge data available.</div>`;
  const deltas = bridge.filter(item => item.kind === "delta");
  const max = Math.max(...bridge.map(item => Math.abs(Number(item.value || 0))), 1);
  return `<div class="bridge">${bridge.map(item => {
    const value = Number(item.value || 0);
    const height = Math.max(5, Math.round(Math.abs(value) / max * 145));
    const cls = item.kind === "delta" ? (value < 0 ? "negative" : "positive") : item.kind;
    const valueKind = metric === "sales_count" ? "int" : "eur";
    return `<div class="bridge-item"><div class="bridge-value">${fmt(value, valueKind)}</div><div class="bridge-bar ${cls}" style="height:${height}px" title="${esc(item.label)}"></div><div class="bridge-label">${esc(item.label)}</div></div>`;
  }).join("")}</div><div class="footnote">The bridge starts at target, applies every dimension contribution, and ends at actual. Negative bars explain the gap; positive bars offset it.</div>`;
}

function renderDriverTable(rows, metric) {
  if (!rows?.length) return `<div class="empty">No driver rows available.</div>`;
  const valueKind = metric === "sales_count" ? "int" : "eur";
  return `<div class="table-wrap"><table><thead><tr><th>Driver</th><th>Actual</th><th>Target</th><th>Contribution</th><th>Contribution %</th><th>Conversion</th><th>Pipeline</th><th>Cancellation</th></tr></thead><tbody>${rows.map(row => `<tr><td><div class="entity">${esc(row.label)}<small>${esc(row.code)}</small></div></td><td class="num">${fmt(row.actual, valueKind)}</td><td class="num">${fmt(row.target, valueKind)}</td><td class="num ${row.contribution < 0 ? "negative" : "positive"}">${fmt(row.contribution, valueKind)}</td><td class="num">${fmt(row.contribution_pct, "pct")}</td><td class="num">${fmt(row.conversion_rate, "pct")}</td><td class="num">${fmt(row.weighted_pipeline, "eur")}</td><td class="num">${fmt(row.cancellation_rate, "pct")}</td></tr>`).join("")}</tbody></table></div>`;
}

function renderDrivers(payload) {
  renderHead("Driver analysis", `${payload.title} · ${payload.week} · ${state.scope}`);
  const fresh = payload.freshness || {};
  setNotice(`Driver analysis uses the same reconciled dataset generation as the control tower · ${fresh.load_id || "—"}`, !fresh.reconciled);
  const valueKind = payload.metric === "sales_count" ? "int" : "eur";
  $("page-content").innerHTML = `<div class="driver-summary"><div class="summary-block"><div class="summary-label">Actual</div><div class="summary-value">${fmt(payload.total_actual, valueKind)}</div></div><div class="summary-block"><div class="summary-label">Target</div><div class="summary-value">${fmt(payload.total_target, valueKind)}</div></div><div class="summary-block"><div class="summary-label">Gap</div><div class="summary-value ${payload.total_gap < 0 ? "negative" : "positive"}">${fmt(payload.total_gap, valueKind)}</div></div></div><div class="two-column"><section class="card"><div class="card-head"><div><h2>Gap bridge</h2><div class="card-sub">Target → contribution by ${esc(DIMENSION_LABEL(payload.dimension))} → actual</div></div><div class="card-actions">${exportLinks("drivers", { dimension: payload.dimension })}</div></div>${renderBridge(payload.bridge, payload.metric)}</section><section class="card"><div class="card-head"><div><h2>Trend context</h2><div class="card-sub">Trailing view for the same selected scope.</div></div></div><div class="card-body"><div class="chart-box short"><canvas data-chart-id="trend"></canvas></div></div></section></div><section class="card"><div class="card-head"><div><h2>Driver evidence</h2><div class="card-sub">Rows are ordered from the largest negative contribution to the largest positive offset.</div></div><div class="card-actions"><label class="card-sub">Breakdown <select id="driver-dimension">${(state.meta?.dimensions || []).map(item => `<option value="${esc(item.code)}" ${item.code === payload.dimension ? "selected" : ""}>${esc(item.label)}</option>`).join("")}</select></label></div></div>${renderDriverTable(payload.drivers, payload.metric)}</section>`;
  renderCharts(payload.charts.filter(chart => chart.id === "trend"));
  $("driver-dimension").addEventListener("change", (event) => loadPage({ dimension: event.target.value }));
}

function DIMENSION_LABEL(code) {
  return state.meta?.dimensions?.find(item => item.code === code)?.label || code;
}

function renderReview(payload) {
  renderHead("Review pack", payload.subtitle);
  setNotice(`Print-ready review pack · data through ${payload.freshness?.source_watermark || "—"}`, !payload.freshness?.reconciled);
  const decision = payload.decision || {};
  $("page-content").innerHTML = `<div class="review-page"><div class="review-head"><div class="review-kicker">Sales network / Weekly operating review</div><div class="review-title">${esc(payload.title)}</div><div class="review-meta">${esc(payload.subtitle)} · Generated from a reconciled reporting snapshot</div></div><section class="decision ${esc(decision.status)}"><div class="decision-status">Decision status · ${esc(decision.status)}</div><h2>${esc(decision.title)}</h2><p>${esc(decision.detail)}</p></section><div class="evidence-grid">${(payload.evidence || []).map(item => `<div class="evidence"><div class="evidence-label">${esc(item.label)}</div><div class="evidence-value">${esc(item.value)}</div></div>`).join("")}</div><section class="card"><div class="card-head"><div><h2>Recommended actions</h2><div class="card-sub">These are deterministic suggestions for the next operating review; this demo does not write them back to a workflow system.</div></div><div class="card-actions no-print"><button class="btn primary" id="print-review" type="button">Print / PDF</button></div></div><ul class="action-list">${(payload.recommended_actions || []).map(item => `<li><span class="action-priority ${esc(item.priority)}">${esc(item.priority)}</span><span class="action-owner">${esc(item.owner)}</span><span>${esc(item.action)}</span><span class="action-due">Due ${esc(item.due)}</span></li>`).join("")}</ul></section><section class="card" style="margin-top:15px"><div class="card-head"><div><h2>Exceptions in scope</h2><div class="card-sub">The review pack keeps the top five signals next to the decision.</div></div></div>${renderExceptionTable(payload.exceptions, state.metric)}</section><div class="footnote">Reporting method: exception-first weekly review. Status thresholds, metric definitions and the difference from the classic KPI catalogue are documented in README.md.</div></div>`;
  $("print-review").addEventListener("click", () => window.print());
}

async function loadPage(extra = {}) {
  const content = $("page-content");
  content.innerHTML = `<div class="loading">Loading ${esc(state.page)}…</div>`;
  try {
    let payload;
    if (state.page === "drivers") {
      payload = await fetchJSON(`/api/drivers?${queryParams(extra)}`);
      renderDrivers(payload);
    } else if (state.page === "review") {
      payload = await fetchJSON(`/api/review-pack?${queryParams()}`);
      renderReview(payload);
    } else {
      payload = await fetchJSON(`/api/cockpit?${queryParams()}`);
      renderCockpit(payload);
    }
  } catch (error) {
    setNotice(error.message, true);
    content.innerHTML = `<div class="empty">Could not load this view. ${esc(error.message)}</div>`;
  }
}

async function reloadData() {
  $("refresh-button").disabled = true;
  try {
    await fetchJSON("/api/refresh", { method: "POST" });
    state.meta = await fetchJSON("/api/meta");
    initMeta(state.meta);
    await loadPage();
    toast("Metadata reloaded");
  } catch (error) {
    setNotice(error.message, true);
  } finally {
    $("refresh-button").disabled = false;
  }
}

function toast(message) {
  const item = $("toast");
  item.hidden = false;
  item.textContent = message;
  window.setTimeout(() => { item.hidden = true; }, 2200);
}

for (const button of document.querySelectorAll(".menu-item")) button.addEventListener("click", () => { setPage(button.dataset.page); loadPage(); });
$("menu-toggle").addEventListener("click", () => {
  const open = $("sidebar").classList.toggle("menu-open");
  $("menu-toggle").setAttribute("aria-expanded", String(open));
});
$("apply-button").addEventListener("click", () => { readControls(); loadPage(); });
$("select-scope").addEventListener("change", () => { readControls(); });
$("select-region").addEventListener("change", () => { if (state.scope === "region") { readControls(); } });
$("select-unit").addEventListener("change", () => { if (state.scope === "unit") { readControls(); } });
$("refresh-button").addEventListener("click", reloadData);

(async function init() {
  try {
    const meta = await fetchJSON("/api/meta");
    initMeta(meta);
    setPage(state.page);
    await loadPage();
  } catch (error) {
    setNotice(error.message, true);
    $("page-content").innerHTML = `<div class="empty">Could not initialise the dashboard. ${esc(error.message)}</div>`;
  }
})();
