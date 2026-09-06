/* Central state: SQL tabs (each with its own Monaco model + result tabs).
 *
 *   SqlTab    { id, title, content, timeout, results: ResultTab[], activeResultId }
 *   ResultTab { id, label, sql, columns, rows, rowCount, truncated, message,
 *               elapsedMs, error, createdAt }
 *
 * Persisted to localStorage (SQL text + titles only, not result data).
 */
export const store = {
  sqlTabs: [],
  activeSqlTabId: null,
  seq: 1,
};

const LS_KEY = "nz.sqltabs.v1";
const MAX_SQL_TABS = 10;
const MAX_RESULTS_PER_TAB = 8;

export function newSqlTabId() {
  return "sql-" + Date.now().toString(36) + "-" + store.seq++;
}

export function newResultId() {
  return "res-" + Date.now().toString(36) + "-" + Math.floor(Math.random() * 1e4);
}

export function createSqlTab(title, content = "") {
  const tab = {
    id: newSqlTabId(),
    title: title || `Query ${store.sqlTabs.length + 1}`,
    content,
    timeout: 30,
    results: [],
    activeResultId: null,
    model: null, // monaco model, attached by editor.js
  };
  store.sqlTabs.push(tab);
  store.activeSqlTabId = tab.id;
  while (store.sqlTabs.length > MAX_SQL_TABS) store.sqlTabs.shift();
  persist();
  return tab;
}

export function getActiveTab() {
  return store.sqlTabs.find((t) => t.id === store.activeSqlTabId) || null;
}

export function setActiveTab(id) {
  store.activeSqlTabId = id;
  persist();
}

export function closeSqlTab(id) {
  const i = store.sqlTabs.findIndex((t) => t.id === id);
  if (i >= 0) {
    const [tab] = store.sqlTabs.splice(i, 1);
    if (tab.model && window.monaco) tab.model.dispose();
  }
  if (store.activeSqlTabId === id) {
    store.activeSqlTabId = store.sqlTabs.length
      ? store.sqlTabs[store.sqlTabs.length - 1].id
      : null;
  }
  persist();
}

export function addResultTab(sqlTab, result) {
  const tab = { id: newResultId(), createdAt: new Date(), ...result };
  sqlTab.results.push(tab);
  sqlTab.activeResultId = tab.id;
  while (sqlTab.results.length > MAX_RESULTS_PER_TAB) sqlTab.results.shift();
  return tab;
}

export function closeResultTab(sqlTab, resultId) {
  sqlTab.results = sqlTab.results.filter((r) => r.id !== resultId);
  if (sqlTab.activeResultId === resultId) {
    sqlTab.activeResultId = sqlTab.results.length
      ? sqlTab.results[sqlTab.results.length - 1].id
      : null;
  }
}

export function getActiveResult(sqlTab) {
  if (!sqlTab) return null;
  return sqlTab.results.find((r) => r.id === sqlTab.activeResultId) || null;
}

export function persist() {
  try {
    localStorage.setItem(
      LS_KEY,
      JSON.stringify(
        store.sqlTabs.map((t) => ({ title: t.title, content: t.model ? t.model.getValue() : t.content, timeout: t.timeout }))
      )
    );
  } catch { /* private mode etc. — ignore */ }
}

export function restore() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return null;
    const arr = JSON.parse(raw);
    if (!Array.isArray(arr) || !arr.length) return null;
    return arr.slice(0, MAX_SQL_TABS);
  } catch {
    return null;
  }
}
