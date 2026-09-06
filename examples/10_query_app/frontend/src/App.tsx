import Editor from '@monaco-editor/react';
import type { OnMount } from '@monaco-editor/react';
import type * as MonacoTypes from 'monaco-editor';
import { useEffect, useMemo, useRef, useReducer, useState } from 'react';
import type { ReactElement } from 'react';
import { api } from './lib/api';
import type { ResultSet } from './lib/types';
import { createInitialState, reducer } from './state/workspace';
import { ContextMenu } from './components/ContextMenu';
import { ResultTabs } from './components/ResultTabs';
import { SchemaTree, schemaMenu } from './components/SchemaTree';

type MonacoEditor = Parameters<OnMount>[0];
type Monaco = Parameters<OnMount>[1];

export function App(): ReactElement {
  const [state, dispatch] = useReducer(reducer, undefined, createInitialState);
  const [context, setContext] = useState<{ x: number; y: number; items: ReturnType<typeof schemaMenu> } | null>(null);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [schemaRefreshKey, setSchemaRefreshKey] = useState(0);
  const editorRef = useRef<MonacoEditor | null>(null);
  const monacoRef = useRef<Monaco | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const tab = state.tabs.find(item => item.id === state.activeTabId) || state.tabs[0];
  const stateRef = useRef(state);
  stateRef.current = state;
  const activeTabRef = useRef(tab);
  activeTabRef.current = tab;
  const queryOwnersRef = useRef(new Map<string, string>());
  const columnsRef = useRef(new Map<string, Record<string, unknown>[]>());
  const resultRefs = useRef(new Map<string, string>());
  const httpAbortersRef = useRef(new Map<string, AbortController>());
  const runChordPendingRef = useRef(false);
  const runChordTimerRef = useRef<number | undefined>(undefined);

  useEffect(() => { localStorage.setItem('nz.query-workspace.v2', JSON.stringify({ tabs: state.tabs.map(item => ({ ...item, results: item.results.map(result => ({ ...result, columns: [] })) })), activeTabId: state.activeTabId })); }, [state.tabs, state.activeTabId]);

  useEffect(() => {
    const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/v1/workspace/ws`);
    socketRef.current = socket;
    socket.onopen = () => undefined;
    socket.onclose = () => { if (socketRef.current === socket) socketRef.current = null; };
    socket.onmessage = event => handleEvent(JSON.parse(event.data) as Record<string, unknown>);
    return () => socket.close();
  }, []);

  function handleEvent(event: Record<string, unknown>): void {
    const queryId = String(event.queryId || '');
    const ownerId = queryOwnersRef.current.get(queryId);
    const isBatchComplete = event.type === 'batch-complete';
    const cleanupQuery = (): void => {
      queryOwnersRef.current.delete(queryId);
      resultRefs.current.forEach((_value, key) => { if (key.startsWith(`${queryId}:`)) resultRefs.current.delete(key); });
    };
    const owner = stateRef.current.tabs.find(item => item.id === ownerId);
    if (!owner) {
      if (isBatchComplete) cleanupQuery();
      return;
    }
    const statementIndex = Number(event.statementIndex || 0);
    if (event.type === 'columns') columnsRef.current.set(`${queryId}:${statementIndex}`, (event.columns as Record<string, unknown>[]) || []);
    if (event.type === 'session-created') {
      const resultId = String(event.resultSetId);
      resultRefs.current.set(`${queryId}:${statementIndex}`, resultId);
      const result: ResultSet = { id: resultId, sessionId: String(event.sessionId), queryId, statementIndex, label: `Statement ${statementIndex + 1}`, status: 'running', columns: columnsRef.current.get(`${queryId}:${statementIndex}`) || [], totalRows: 0, createdAt: new Date().toISOString() };
      dispatch({ type: 'query.result', tabId: owner.id, result });
    } else if (event.type === 'progress') {
      const resultId = resultRefs.current.get(`${queryId}:${statementIndex}`); if (resultId) dispatch({ type: 'query.progress', tabId: owner.id, resultId, totalRows: Number(event.totalRows || 0) });
    } else if (event.type === 'complete') {
      const resultId = resultRefs.current.get(`${queryId}:${statementIndex}`); if (resultId) dispatch({ type: 'query.complete', tabId: owner.id, resultId, totalRows: Number(event.totalRows || 0), truncated: Boolean(event.limitReached), message: typeof event.message === 'string' ? event.message : undefined });
    } else if (event.type === 'error') {
      const resultId = resultRefs.current.get(`${queryId}:${statementIndex}`);
      if (resultId) dispatch({ type: 'query.resultStatus', tabId: owner.id, resultId, status: 'error', message: String(event.message || 'Query failed') });
      dispatch({ type: 'query.error', tabId: owner.id, message: String(event.message || 'Query failed') });
    } else if (event.type === 'cancelled') {
      const resultId = resultRefs.current.get(`${queryId}:${statementIndex}`);
      if (resultId) dispatch({ type: 'query.resultStatus', tabId: owner.id, resultId, status: 'cancelled', message: 'Query cancelled.' });
    } else if (event.type === 'batch-complete') {
      dispatch({ type: 'query.batchComplete', tabId: owner.id, status: String(event.status || 'error') });
      cleanupQuery();
    }
  }

  const run = async (mode: 'cursor' | 'selection' | 'script' = 'cursor'): Promise<void> => {
    const currentTab = activeTabRef.current;
    if (!currentTab) return;
    const editor = editorRef.current; const model = editor?.getModel();
    const sql = currentTab.sql; if (!sql.trim()) return;
    const preview = await api.preview(sql, currentTab.database);
    let writeConfirmed = false;
    if (preview.containsWrite) { writeConfirmed = window.confirm('This script contains statements that change database state. Execute it?'); if (!writeConfirmed) return; }
    const queryId = crypto.randomUUID();
    dispatch({ type: 'query.start', id: currentTab.id, queryId });
    const position = editor?.getPosition();
    const selection = editor?.getSelection();
    const payload = { type: 'query.start', queryId, tabId: currentTab.id, sql, mode, database: currentTab.database, schema: currentTab.schema, cursorOffset: position && model ? model.getOffsetAt(position) : undefined, selection: selection && model && !selection.isEmpty() ? { start: model.getOffsetAt({ lineNumber: selection.startLineNumber, column: selection.startColumn }), end: model.getOffsetAt({ lineNumber: selection.endLineNumber, column: selection.endColumn }) } : undefined, timeoutSeconds: 30, previewToken: preview.previewToken, writeConfirmed };
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      queryOwnersRef.current.set(queryId, currentTab.id);
      socket.send(JSON.stringify(payload));
      return;
    }
    const aborter = new AbortController();
    httpAbortersRef.current.set(queryId, aborter);
    try {
      const response = await api.execute(payload, aborter.signal);
      for (const item of response.results) {
        const result: ResultSet = { id: item.resultSetId, sessionId: item.sessionId, queryId, statementIndex: item.statementIndex, label: `Statement ${item.statementIndex + 1}`, status: 'running', columns: item.columns || [], totalRows: 0, createdAt: new Date().toISOString() };
        dispatch({ type: 'query.result', tabId: currentTab.id, result });
        if (item.status === 'complete') dispatch({ type: 'query.complete', tabId: currentTab.id, resultId: result.id, totalRows: item.totalRows, truncated: item.truncated, message: item.message });
        else dispatch({ type: 'query.resultStatus', tabId: currentTab.id, resultId: result.id, status: item.status === 'cancelled' ? 'cancelled' : 'error', message: item.message });
      }
      if (response.status === 'complete' || response.status === 'cancelled') dispatch({ type: 'query.batchComplete', tabId: currentTab.id, status: response.status });
      else dispatch({ type: 'query.error', tabId: currentTab.id, message: response.error || 'Query failed' });
    } catch (reason) {
      if (aborter.signal.aborted) dispatch({ type: 'query.batchComplete', tabId: currentTab.id, status: 'cancelled' });
      else dispatch({ type: 'query.error', tabId: currentTab.id, message: reason instanceof Error ? reason.message : 'Could not execute query.' });
    } finally {
      httpAbortersRef.current.delete(queryId);
    }
  };
  const cancel = (): void => { const id = tab?.runningQueryId; if (!id) return; if (socketRef.current?.readyState === WebSocket.OPEN) socketRef.current.send(JSON.stringify({ type: 'query.cancel', queryId: id })); else httpAbortersRef.current.get(id)?.abort(); };
  const insert = (value: string): void => { const editor = editorRef.current; const selection = editor?.getSelection(); if (editor && selection) { editor.executeEdits('schema-action', [{ range: selection, text: value, forceMoveMarkers: true }]); editor.focus(); } };
  const openQuery = (sql: string): void => { dispatch({ type: 'tab.add', sql, title: 'Preview' }); };
  const refreshSchema = async (database?: string, schema?: string): Promise<void> => { try { await api.refreshSchema(database, schema); } catch { /* the reload below renders the server error in the tree */ } finally { setSchemaRefreshKey(value => value + 1); } };
  const showDetail = async (node: Parameters<typeof schemaMenu>[0]): Promise<void> => {
    if (!node.object_name || !['TABLE', 'VIEW', 'EXTERNAL TABLE', 'PROCEDURE', 'SYNONYM'].includes(node.object_type || '')) return;
    try { setDetail(await api.detail(node)); } catch (reason) { setDetail({ error: reason instanceof Error ? reason.message : 'Could not load object details.' }); }
  };
  const closeTab = (id: string): void => {
    const closing = stateRef.current.tabs.find(item => item.id === id);
    if (closing?.runningQueryId && socketRef.current?.readyState === WebSocket.OPEN) socketRef.current.send(JSON.stringify({ type: 'query.cancel', queryId: closing.runningQueryId }));
    if (closing?.runningQueryId) httpAbortersRef.current.get(closing.runningQueryId)?.abort();
    dispatch({ type: 'tab.close', id });
  };
  const onMount: OnMount = (editor, monaco) => {
    editorRef.current = editor; monacoRef.current = monaco;
    const quickSql = new Map([['SX', 'SELECT '], ['FX', 'FROM '], ['WX', 'WHERE '], ['HX', 'HAVING '], ['GX', 'GROUP BY ']]);
    editor.onKeyDown(event => {
      const isCtrlW = event.keyCode === monaco.KeyCode.KeyW && (event.ctrlKey || event.metaKey);
      if (isCtrlW) {
        event.preventDefault();
        event.stopPropagation();
        runChordPendingRef.current = true;
        window.clearTimeout(runChordTimerRef.current);
        runChordTimerRef.current = window.setTimeout(() => { runChordPendingRef.current = false; }, 1500);
        return;
      }
      if (runChordPendingRef.current && event.keyCode === monaco.KeyCode.Enter) {
        event.preventDefault();
        event.stopPropagation();
        runChordPendingRef.current = false;
        window.clearTimeout(runChordTimerRef.current);
        void run('cursor');
        return;
      }
      if (runChordPendingRef.current) runChordPendingRef.current = false;
    });
    editor.onKeyUp(event => {
      if (event.keyCode !== monaco.KeyCode.Space) return;
      const model = editor.getModel();
      const position = editor.getPosition();
      if (!model || !position) return;
      const beforeCursor = model.getLineContent(position.lineNumber).slice(0, position.column - 1);
      const match = /(^|[\s(])(SX|FX|WX|HX|GX)\s$/i.exec(beforeCursor);
      if (!match) return;
      const abbreviationStart = match.index + match[1].length;
      editor.executeEdits('quick-sql', [{ range: { startLineNumber: position.lineNumber, startColumn: abbreviationStart + 1, endLineNumber: position.lineNumber, endColumn: position.column }, text: quickSql.get(match[2].toUpperCase()) || '' }]);
    });
    monaco.languages.registerCompletionItemProvider('sql', { triggerCharacters: ['.', ' ', '\n'], provideCompletionItems: async (model: MonacoTypes.editor.ITextModel, position: MonacoTypes.Position) => {
      const currentTab = activeTabRef.current;
      const response = await api.completion(model.getValue(), model.getOffsetAt(position), currentTab.database, currentTab.schema);
      const word = model.getWordUntilPosition(position);
      return { suggestions: response.items.map(item => ({ label: String(item.label), kind: monaco.languages.CompletionItemKind[item.kind === 'column' ? 'Field' : item.kind === 'table' || item.kind === 'view' ? 'Class' : item.kind === 'snippet' ? 'Snippet' : 'Keyword'], detail: String(item.detail || ''), documentation: String(item.documentation || ''), insertText: String(item.insertText || item.label), insertTextRules: item.kind === 'snippet' ? monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet : undefined, range: { startLineNumber: position.lineNumber, startColumn: word.startColumn, endLineNumber: position.lineNumber, endColumn: word.endColumn }, sortText: String(item.sortText || '') })) };
    } });
    let diagnosticTimer: number | undefined;
    const updateDiagnostics = (): void => {
      window.clearTimeout(diagnosticTimer);
      diagnosticTimer = window.setTimeout(() => {
        const model = editor.getModel();
        if (!model) return;
        const currentTab = activeTabRef.current;
        void api.diagnostics(model.getValue(), currentTab.database, currentTab.schema).then(response => {
          monaco.editor.setModelMarkers(model, 'nzpy-language', response.diagnostics.map(item => {
            const start = item.start as { line?: number; character?: number } | undefined;
            const end = item.end as { line?: number; character?: number } | undefined;
            return { message: String(item.message || ''), severity: item.severity === 'error' ? monaco.MarkerSeverity.Error : monaco.MarkerSeverity.Warning, code: String(item.code || ''), startLineNumber: Number(start?.line || 0) + 1, startColumn: Number(start?.character || 0) + 1, endLineNumber: Number(end?.line ?? start?.line ?? 0) + 1, endColumn: Math.max(Number(end?.character ?? start?.character ?? 0) + 1, 2) };
          }));
        }).catch(() => undefined);
      }, 300);
    };
    editor.onDidChangeModelContent(updateDiagnostics);
    updateDiagnostics();
  };
  const activeResult = useMemo(() => tab?.results.find(result => result.id === tab.activeResultId), [tab]);
  const definition = typeof detail?.definition === 'string' ? detail.definition : typeof detail?.source === 'string' ? detail.source : '';
  const detailMetadata = detail ? Object.fromEntries(Object.entries(detail).filter(([key]) => !['definition', 'source'].includes(key))) : null;
  return <div className="app-shell" onClick={() => setContext(null)}>
    <header className="topbar"><div className="brand">Netezza SQL Workspace</div><button className="primary" onClick={() => void run('cursor')} disabled={Boolean(tab?.runningQueryId)}>▶ Run</button><button onClick={() => void run('selection')} disabled={Boolean(tab?.runningQueryId)}>Run selection</button><button onClick={() => void run('script')} disabled={Boolean(tab?.runningQueryId)}>Run script</button><button className="danger" onClick={cancel} disabled={!tab?.runningQueryId}>■ Cancel</button><span className="toolbar-spacer" /><span className="status-dot" /><span>{state.status}</span></header>
    <div className="sql-tabs">{state.tabs.map(item => <div key={item.id} className={`sql-tab ${item.id === tab.id ? 'active' : ''}`} onClick={() => dispatch({ type: 'tab.activate', id: item.id })}><span onDoubleClick={() => { const title = window.prompt('Tab name', item.title); if (title) dispatch({ type: 'tab.rename', id: item.id, title }); }}>{item.title}{item.dirty ? ' •' : ''}</span><button onClick={event => { event.stopPropagation(); closeTab(item.id); }}>×</button></div>)}<button className="new-tab" onClick={() => dispatch({ type: 'tab.add' })}>＋</button></div>
    <main className="workspace"><aside><SchemaTree refreshKey={schemaRefreshKey} onMenu={(node, event) => { event.preventDefault(); setContext({ x: event.clientX, y: event.clientY, items: schemaMenu(node, insert, openQuery, () => void refreshSchema(node.database, node.schema), () => void showDetail(node)) }); }} onSelect={node => { if (node.kind === 'object' || node.kind === 'column') { const name = node.kind === 'column' ? [node.database, node.schema, node.object_name, node.column_name || node.label] : [node.database, node.schema, node.object_name || node.label]; insert(name.filter(Boolean).join('.')); } }} onRefresh={() => void refreshSchema()} /></aside><section className="editor-results"><div className="editor-head"><span>{tab.database || 'Configured database'}</span><span>{tab.schema || 'All schemas'}</span><button onClick={() => insert('SELECT ')}>Insert SELECT</button></div><div className="editor"><Editor height="100%" language="sql" theme="vs-dark" path={tab.id} value={tab.sql} onChange={value => dispatch({ type: 'tab.sql', id: tab.id, sql: value || '' })} onMount={onMount} options={{ minimap: { enabled: false }, automaticLayout: true, fontSize: 14, wordWrap: 'on', bracketPairColorization: { enabled: true }, scrollBeyondLastLine: false, acceptSuggestionOnEnter: 'off' }} /></div><ResultTabs results={tab.results} activeId={tab.activeResultId} onSelect={id => dispatch({ type: 'result.activate', tabId: tab.id, resultId: id })} onClose={id => dispatch({ type: 'result.close', tabId: tab.id, resultId: id })} /></section></main>
    {context && <ContextMenu x={context.x} y={context.y} items={context.items} onClose={() => setContext(null)} />}
    {detail && <div className="detail-overlay" onClick={() => setDetail(null)}><section className="detail-dialog" onClick={event => event.stopPropagation()}><div className="detail-head"><strong>{definition ? `${String(detail.object_type || 'Object')} definition` : 'Object details'}</strong><button onClick={() => setDetail(null)}>×</button></div>{definition && <div className="definition-block"><div className="definition-toolbar"><span>Source definition</span><button onClick={() => void navigator.clipboard?.writeText(definition)}>Copy definition</button></div><pre className="source-code">{definition}</pre></div>}<pre>{JSON.stringify(detailMetadata, null, 2)}</pre></section></div>}
    {activeResult?.message && <div className="toast">{activeResult.message}</div>}
  </div>;
}
