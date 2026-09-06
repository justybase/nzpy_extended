/* Toolbar + status + toast + import dialog wiring. */
import { cancelQuery, downloadBlob, ENDPOINTS, postExport, runQuery } from "./api.js";
import { currentSql } from "./editor.js";
import { addResultTab, getActiveTab, store } from "./store.js";

let activeQueryId = null;
let onResultsChanged = null;

export function initToolbar(callbacks) {
  onResultsChanged = callbacks.onResultsChanged;
  document.getElementById("btn-run").addEventListener("click", executeActive);
  document.getElementById("btn-cancel").addEventListener("click", cancelActive);
  document.getElementById("btn-export").addEventListener("click", exportActive);
  document.getElementById("btn-import").addEventListener("click", openImport);
  document.getElementById("timeout-input").addEventListener("change", (e) => {
    const tab = getActiveTab();
    if (tab) tab.timeout = parseFloat(e.target.value) || 0;
  });
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      executeActive();
    }
  });
}

export function syncTimeoutInput() {
  const tab = getActiveTab();
  if (tab) document.getElementById("timeout-input").value = tab.timeout || 30;
}

export async function executeActive() {
  const tab = getActiveTab();
  if (!tab) return;
  const sql = currentSql();
  if (!sql) return;
  const timeout = parseFloat(document.getElementById("timeout-input").value) || 0;
  tab.timeout = timeout;
  const queryId = crypto.randomUUID();
  activeQueryId = queryId;

  hideError();
  setButtons(true);
  setStatus("running", "Executing...");
  loading(true);
  const started = performance.now();

  try {
    const { ok, json, text, status } = await runQuery({ sql, timeout, queryId });
    if (!ok || !json) {
      const msg = (json && json.detail) || text || `HTTP ${status} — non-JSON response`;
      addResultTab(tab, { label: timeLabel(), sql, error: String(msg), elapsedMs: performance.now() - started });
      showError(String(msg));
      setStatus("error", "Query failed");
    } else {
      const elapsed = json.elapsed_ms || performance.now() - started;
      addResultTab(tab, {
        label: timeLabel(), sql,
        columns: json.columns || [], rows: json.rows || [],
        rowCount: json.row_count ?? (json.rows || []).length,
        truncated: !!json.truncated, message: json.message || null, elapsedMs: elapsed,
      });
      if (json.truncated && json.message) showToast(json.message, "success");
      setStatus("success", `Done — ${json.rows ? json.rows.length : 0} rows`);
    }
  } catch (err) {
    addResultTab(tab, { label: timeLabel(), sql, error: err.message || "Network error", elapsedMs: 0 });
    showError(err.message || "Network error");
    setStatus("error", "Connection failed");
  } finally {
    loading(false);
    setButtons(false);
    onResultsChanged && onResultsChanged();
  }
}

async function cancelActive() {
  if (!activeQueryId) return;
  try {
    await cancelQuery(activeQueryId);
    showToast("Cancel signal sent", "success");
    setStatus("idle", "Cancelling...");
  } catch (err) {
    showToast("Cancel failed: " + err.message, "error");
  }
}

async function exportActive() {
  const sql = currentSql();
  if (!sql) return;
  try {
    const resp = await postExport(sql, "csv");
    if (!resp.ok) {
      const j = await resp.json().catch(() => ({}));
      showToast("Export failed: " + (j.detail || resp.status), "error");
      return;
    }
    downloadBlob(await resp.blob(), "export.csv");
    showToast("Exported successfully", "success");
  } catch (err) {
    showToast("Export failed: " + err.message, "error");
  }
}

/* -- import dialog -- */
function openImport() {
  document.getElementById("import-dialog").classList.add("open");
}
export function initImportDialog() {
  document.getElementById("btn-import-cancel").addEventListener("click", closeImport);
  document.getElementById("btn-import-do").addEventListener("click", doImport);
}
function closeImport() {
  document.getElementById("import-dialog").classList.remove("open");
}
async function doImport() {
  const table = document.getElementById("import-table").value.trim();
  const file = document.getElementById("import-file").files[0];
  const delimiter = document.getElementById("import-delimiter").value || ",";
  if (!table || !file) {
    showToast("Table name and file required", "error");
    return;
  }
  const fd = new FormData();
  fd.append("table", table);
  fd.append("file", file);
  fd.append("delimiter", delimiter);
  try {
    const resp = await fetch(ENDPOINTS.import, { method: "POST", body: fd });
    const json = await resp.json();
    if (resp.ok) {
      showToast(`Imported ${json.imported} rows into "${json.table}"`, "success");
      closeImport();
    } else {
      showToast("Import failed: " + (json.detail || resp.status), "error");
    }
  } catch (err) {
    showToast("Import error: " + err.message, "error");
  }
}

/* -- ui helpers -- */
export function setStatus(state, text) {
  document.getElementById("status-dot").className = "status-dot " + state;
  document.getElementById("status-text").textContent = text;
}
function setButtons(running) {
  document.getElementById("btn-run").disabled = running;
  document.getElementById("btn-cancel").disabled = !running;
}
export function showToast(msg, type = "") {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = type;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 4000);
}
function showError(msg) {
  const el = document.getElementById("results-error");
  el.textContent = msg;
  el.style.display = "block";
}
function hideError() {
  document.getElementById("results-error").style.display = "none";
}
function loading(on) {
  document.getElementById("loading-overlay").classList.toggle("active", on);
}
function timeLabel() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function getActiveQueryId() {
  return activeQueryId;
}
// keep sidebar visible on wide screens; unused hook for future layout toggles
export function toggleSidebar() {
  const sb = document.getElementById("schema-sidebar");
  sb.style.display = sb.style.display === "none" ? "" : "none";
}
void store;
