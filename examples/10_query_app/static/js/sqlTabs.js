/* SQL tab bar: one Monaco model per tab, persisted to localStorage. */
import { createSqlTab, closeSqlTab, getActiveTab, restore, setActiveTab, store } from "./store.js";

export const DEFAULT_SQL =
  "-- SELECT 1 AS col1, 'hello' AS col2, 3.14 AS col3\nSELECT * FROM _v_table WHERE objtype='TABLE' LIMIT 50";

let barEl = null;
let onSwitch = null;

export function initSqlTabs(bar, callbacks) {
  barEl = bar;
  onSwitch = callbacks.onSwitch;
  bar.querySelector("#btn-add-tab").addEventListener("click", () => {
    const tab = createSqlTab("", "-- new query\nSELECT 1");
    if (callbacks.onCreate) callbacks.onCreate(tab);
    render();
    switchTo(tab.id);
  });
}

export function ensureInitialTabs() {
  if (store.sqlTabs.length) return;
  const saved = restore();
  if (saved) {
    for (const s of saved) {
      const t = createSqlTab(s.title, s.content || "");
      t.timeout = s.timeout || 30;
    }
  } else {
    createSqlTab("Query 1", DEFAULT_SQL);
  }
  if (!store.activeSqlTabId && store.sqlTabs.length) {
    store.activeSqlTabId = store.sqlTabs[0].id;
  }
}

export function render() {
  barEl.querySelectorAll(".sql-tab").forEach((n) => n.remove());
  const addBtn = barEl.querySelector("#btn-add-tab");
  for (const tab of store.sqlTabs) {
    const el = document.createElement("div");
    el.className = "sql-tab" + (tab.id === store.activeSqlTabId ? " active" : "");
    const name = document.createElement("span");
    name.textContent = tab.title;
    name.title = "Double-click to rename";
    name.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      const next = prompt("Tab name:", tab.title);
      if (next && next.trim()) {
        tab.title = next.trim();
        render();
      }
    });
    el.appendChild(name);
    const x = document.createElement("button");
    x.className = "close";
    x.textContent = "×";
    x.title = "Close tab";
    x.addEventListener("click", (e) => {
      e.stopPropagation();
      closeSqlTab(tab.id);
      if (!store.sqlTabs.length) createSqlTab("Query 1", DEFAULT_SQL);
      render();
      const active = getActiveTab();
      if (active && onSwitch) onSwitch(active.id);
    });
    el.appendChild(x);
    el.addEventListener("click", () => switchTo(tab.id));
    barEl.insertBefore(el, addBtn);
  }
}

export function switchTo(id) {
  setActiveTab(id);
  render();
  if (onSwitch) onSwitch(id);
}
