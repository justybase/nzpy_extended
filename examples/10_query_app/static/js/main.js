/* Application bootstrap: wires tabs, editor, schema browser, results. */
import { ensureTabModel, getEditor, initEditor, insertTextAtCursor, showTabModel } from "./editor.js";
import { initResults, renderResultTabBar, showResult } from "./results.js";
import { initSchemaBrowser, loadSchemas } from "./schemaBrowser.js";
import { ensureInitialTabs, initSqlTabs, render as renderSqlTabs } from "./sqlTabs.js";
import { getActiveTab, store } from "./store.js";
import { closeResultTab } from "./store.js";
import { executeActive, initImportDialog, initToolbar, syncTimeoutInput } from "./toolbar.js";

function refreshResultsUI() {
  const tab = getActiveTab();
  const bar = document.getElementById("result-tab-bar");
  const count = document.getElementById("results-count");
  const errEl = document.getElementById("results-error");
  renderResultTabBar(bar, count, tab, {
    onSelectResult: (id) => {
      if (tab) tab.activeResultId = id;
      refreshResultsUI();
    },
    onCloseResult: (id) => {
      if (tab) {
        closeResultTab(tab, id);
        refreshResultsUI();
      }
    },
  });
  const active = tab ? tab.results.find((r) => r.id === tab.activeResultId) : null;
  showResult(active || null, errEl);
}

function switchSqlTab(id) {
  showTabModel(id);
  syncTimeoutInput();
  refreshResultsUI();
}

ensureInitialTabs();
initSqlTabs(document.getElementById("sql-tab-bar"), {
  onSwitch: switchSqlTab,
  onCreate: (tab) => ensureTabModel(tab),
});
renderSqlTabs();

initResults(document.getElementById("table-scroll-container"));
initToolbar({ onResultsChanged: refreshResultsUI });
initImportDialog();
initSchemaBrowser(
  {
    tree: document.getElementById("schema-tree"),
    searchInput: document.getElementById("schema-search"),
    refreshBtn: document.getElementById("btn-schema-refresh"),
    detail: document.getElementById("schema-detail"),
  },
  { onInsertText: insertTextAtCursor }
);

initEditor({
  onRun: executeActive,
  onReady: () => {
    const active = getActiveTab();
    if (active) {
      const ed = getEditor();
      if (ed && active.model) ed.setModel(active.model);
    }
    void store;
    syncTimeoutInput();
    refreshResultsUI();
    loadSchemas();
  },
});
