/* Monaco editor setup (AMD loader from CDN) + per-tab models. */
import { registerCompletionProvider, refreshSchemaCache } from "./completion.js";
import { getActiveTab, persist, store } from "./store.js";

let editor = null;
let onRun = null;

export function getEditor() {
  return editor;
}

export function insertTextAtCursor(text) {
  if (!editor) return;
  const sel = editor.getSelection();
  editor.executeEdits("insert", [{ range: sel, text, forceMoveMarkers: true }]);
  editor.focus();
}

export function initEditor(callbacks) {
  onRun = callbacks.onRun;
  window.require.config({
    paths: { vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs" },
  });
  window.require(["vs/editor/editor.main"], function () {
    monaco.languages.setLanguageConfiguration("sql", {
      brackets: [["(", ")"]],
      autoClosingPairs: [
        { open: "(", close: ")" },
        { open: "'", close: "'" },
        { open: '"', close: '"' },
      ],
      surroundingPairs: [
        { open: "(", close: ")" },
        { open: "'", close: "'" },
        { open: '"', close: '"' },
      ],
    });
    registerCompletionProvider(monaco);

    editor = monaco.editor.create(document.getElementById("editor-container"), {
      language: "sql",
      theme: "vs-dark",
      fontSize: 14,
      fontFamily: "'Cascadia Code', 'Fira Code', 'Consolas', monospace",
      lineNumbers: "on",
      minimap: { enabled: false },
      scrollBeyondLastLine: false,
      automaticLayout: true,
      wordWrap: "on",
      tabSize: 2,
      renderWhitespace: "selection",
      bracketPairColorization: { enabled: true },
    });
    window._monacoEditor = editor;

    // one model per SQL tab
    for (const tab of store.sqlTabs) {
      tab.model = monaco.editor.createModel(tab.content || "", "sql");
    }
    const active = getActiveTab();
    if (active && active.model) editor.setModel(active.model);

    let saveTimer = null;
    editor.onDidChangeModelContent(() => {
      const tab = getActiveTab();
      if (tab && editor.getModel() === tab.model) {
        tab.content = editor.getValue();
        clearTimeout(saveTimer);
        saveTimer = setTimeout(persist, 500);
      }
    });

    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => onRun && onRun());
    window.addEventListener("resize", () => editor.layout());

    refreshSchemaCache().catch(() => {});
    if (callbacks.onReady) callbacks.onReady();
  });
}

export function showTabModel(tabId) {
  const tab = store.sqlTabs.find((t) => t.id === tabId);
  if (tab && editor && tab.model) editor.setModel(tab.model);
}

export function ensureTabModel(tab) {
  if (!tab.model && window.monaco) {
    tab.model = monaco.editor.createModel(tab.content || "", "sql");
  }
  return tab.model;
}

export function currentSql() {
  return editor ? editor.getValue().trim() : "";
}
