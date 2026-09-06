/* Virtualized results grid + per-SQL-tab result tabs. */
import { getActiveResult } from "./store.js";

const ROW_H = 34;
const OVERSCAN = 15;

let container = null;
let spacerEl = null;
let columnWidths = [];
let currentData = { columns: [], rows: [] };

function formatCell(v) {
  if (v === null || v === undefined) return "\u00A0";
  if (typeof v === "boolean") return v ? "true" : "false";
  return String(v);
}

function formatCellNull(v) {
  if (v === null || v === undefined) return '<span class="cell-null">NULL</span>';
  const div = document.createElement("div");
  div.textContent = formatCell(v);
  return div.innerHTML;
}

function colNames(columns) {
  return (columns || []).map((c) => c.ColumnName || c.name || c.column_name || "?");
}

export function initResults(scrollContainer) {
  container = scrollContainer;
  container.addEventListener("scroll", () => renderVirtualRows(), { passive: true });
}

export function renderResultTabBar(barEl, countEl, sqlTab, callbacks) {
  barEl.querySelectorAll(".result-tab").forEach((n) => n.remove());
  const active = getActiveResult(sqlTab);
  for (const r of sqlTab ? sqlTab.results : []) {
    const el = document.createElement("div");
    el.className = "result-tab" + (active && active.id === r.id ? " active" : "");
    const label = document.createElement("span");
    label.textContent = r.error ? `✖ ${r.label}` : `${r.label} (${r.rows ? r.rows.length : 0})`;
    label.title = r.error ? r.error : `${r.rowCount} rows · ${Math.round(r.elapsedMs || 0)} ms`;
    el.appendChild(label);
    const x = document.createElement("button");
    x.className = "close";
    x.textContent = "×";
    x.addEventListener("click", (e) => { e.stopPropagation(); callbacks.onCloseResult(r.id); });
    el.appendChild(x);
    el.addEventListener("click", () => callbacks.onSelectResult(r.id));
    barEl.insertBefore(el, countEl);
  }
  if (active) {
    countEl.textContent = active.error
      ? "Error"
      : `${active.rowCount} row${active.rowCount !== 1 ? "s" : ""}` +
        (active.truncated ? " (truncated)" : "") +
        ` · ${Math.round(active.elapsedMs || 0)} ms`;
  } else {
    countEl.textContent = "No results";
  }
}

export function showResult(result, errorEl) {
  if (!result) {
    container.innerHTML = "";
    currentData = { columns: [], rows: [] };
    return;
  }
  if (result.error) {
    container.innerHTML = "";
    errorEl.textContent = result.error;
    errorEl.style.display = "block";
    return;
  }
  errorEl.style.display = "none";
  currentData = { columns: result.columns || [], rows: result.rows || [] };
  if (!currentData.rows.length) {
    container.innerHTML = "";
    return;
  }
  buildTable();
}

function buildTable() {
  container.innerHTML = "";
  const names = colNames(currentData.columns);
  columnWidths = names.map((n) => Math.max(130, Math.min(n.length * 10 + 40, 300)));
  const totalWidth = columnWidths.reduce((a, b) => a + b, 0);

  const header = document.createElement("div");
  header.style.cssText = `display:flex;position:sticky;top:0;z-index:2;min-width:${totalWidth}px;`;
  names.forEach((name, i) => {
    const cell = document.createElement("div");
    cell.textContent = name;
    const type = currentData.columns[i] && (currentData.columns[i].DataType || currentData.columns[i].data_type);
    if (type) cell.title = String(type);
    cell.style.cssText =
      "padding:6px 12px;font-weight:600;font-size:12px;color:#0078d4;" +
      "background:#323233;border-bottom:2px solid #3c3c3c;" +
      "border-right:1px solid #3c3c3c;white-space:nowrap;overflow:hidden;" +
      "text-overflow:ellipsis;flex-shrink:0;";
    cell.style.width = columnWidths[i] + "px";
    cell.style.minWidth = columnWidths[i] + "px";
    header.appendChild(cell);
  });
  container.appendChild(header);

  spacerEl = document.createElement("div");
  spacerEl.style.position = "relative";
  spacerEl.style.width = "100%";
  spacerEl.style.minWidth = totalWidth + "px";
  container.appendChild(spacerEl);
  renderVirtualRows();
}

function renderVirtualRows() {
  if (!spacerEl || !container) return;
  const scrollTop = container.scrollTop;
  const viewH = container.clientHeight;
  const total = currentData.rows.length;
  const start = Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN);
  const end = Math.min(total - 1, Math.ceil((scrollTop + viewH) / ROW_H) + OVERSCAN);
  while (spacerEl.lastChild) spacerEl.removeChild(spacerEl.lastChild);
  spacerEl.style.height = total * ROW_H + "px";
  const ncols = currentData.columns.length;
  for (let i = start; i <= end; i++) {
    const rowData = currentData.rows[i];
    const el = document.createElement("div");
    Object.assign(el.style, {
      position: "absolute", top: "0", left: "0", width: "100%",
      height: ROW_H + "px", transform: `translateY(${i * ROW_H}px)`, display: "flex",
    });
    for (let j = 0; j < ncols; j++) {
      const cell = document.createElement("div");
      cell.style.cssText =
        "padding:4px 12px;font-size:12px;border-bottom:1px solid #3c3c3c;" +
        "border-right:1px solid #3c3c3c;white-space:nowrap;overflow:hidden;" +
        "text-overflow:ellipsis;flex-shrink:0;";
      cell.style.width = columnWidths[j] + "px";
      cell.style.minWidth = columnWidths[j] + "px";
      cell.title = formatCell(rowData[j]);
      cell.innerHTML = formatCellNull(rowData[j]);
      el.appendChild(cell);
    }
    spacerEl.appendChild(el);
  }
}
