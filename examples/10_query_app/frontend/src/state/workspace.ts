import type { ResultSet, SqlTab } from '../lib/types';

export type WorkspaceState = { tabs: SqlTab[]; activeTabId: string; connected: boolean; status: string };
export type Action =
  | { type: 'tab.add'; sql?: string; title?: string }
  | { type: 'tab.close'; id: string }
  | { type: 'tab.activate'; id: string }
  | { type: 'tab.rename'; id: string; title: string }
  | { type: 'tab.sql'; id: string; sql: string }
  | { type: 'query.start'; id: string; queryId: string }
  | { type: 'query.result'; tabId: string; result: ResultSet }
  | { type: 'query.progress'; tabId: string; resultId: string; totalRows: number }
  | { type: 'query.complete'; tabId: string; resultId: string; totalRows: number; truncated?: boolean; message?: string }
  | { type: 'query.resultStatus'; tabId: string; resultId: string; status: ResultSet['status']; message?: string }
  | { type: 'query.batchComplete'; tabId: string; status: string }
  | { type: 'query.error'; tabId: string; message: string }
  | { type: 'result.activate'; tabId: string; resultId: string }
  | { type: 'result.close'; tabId: string; resultId: string };

let sequence = 1;
export const newTab = (): SqlTab => {
  const number = sequence++;
  return { id: `query-${Date.now()}-${number}`, title: `Query ${number}`, sql: '-- Netezza SQL\nSELECT 1 AS value;', dirty: false, results: [] };
};

export const initialState: WorkspaceState = { tabs: [newTab()], activeTabId: '', connected: false, status: 'Ready' };
initialState.activeTabId = initialState.tabs[0].id;

export function createInitialState(): WorkspaceState {
  try {
    const raw = localStorage.getItem('nz.query-workspace.v2');
    if (raw) {
      const saved = JSON.parse(raw) as Partial<WorkspaceState>;
      if (Array.isArray(saved.tabs) && saved.tabs.length) {
        const tabs = saved.tabs.map(tab => ({
          ...newTab(),
          ...tab,
          runningQueryId: undefined,
          results: (Array.isArray(tab.results) ? tab.results : []).map(result => ({
            ...result,
            status: result.status === 'running' ? 'cancelled' : result.status,
            message: result.status === 'running' ? 'Query was interrupted by reload.' : result.message,
          })),
        }));
        return { tabs, activeTabId: tabs.some(tab => tab.id === saved.activeTabId) ? String(saved.activeTabId) : tabs[0].id, connected: false, status: 'Ready' };
      }
    }
  } catch { /* ignore corrupt browser state */ }
  const tab = newTab();
  return { tabs: [tab], activeTabId: tab.id, connected: false, status: 'Ready' };
}

export function reducer(state: WorkspaceState, action: Action): WorkspaceState {
  switch (action.type) {
    case 'tab.add': { const tab = { ...newTab(), ...(action.sql !== undefined ? { sql: action.sql } : {}), ...(action.title ? { title: action.title } : {}) }; return { ...state, tabs: [...state.tabs, tab], activeTabId: tab.id }; }
    case 'tab.close': {
      const tabs = state.tabs.filter(tab => tab.id !== action.id);
      const next = tabs.length ? tabs : [newTab()];
      const oldIndex = state.tabs.findIndex(tab => tab.id === action.id);
      const nextActive = state.activeTabId === action.id ? (tabs[Math.min(Math.max(0, oldIndex - 1), tabs.length - 1)] || next[0]).id : state.activeTabId;
      return { ...state, tabs: next, activeTabId: nextActive };
    }
    case 'tab.activate': return { ...state, activeTabId: action.id };
    case 'tab.rename': return { ...state, tabs: state.tabs.map(tab => tab.id === action.id ? { ...tab, title: action.title, dirty: false } : tab) };
    case 'tab.sql': return { ...state, tabs: state.tabs.map(tab => tab.id === action.id ? { ...tab, sql: action.sql, dirty: true } : tab) };
    case 'query.start': return { ...state, status: 'Running…', tabs: state.tabs.map(tab => tab.id === action.id ? { ...tab, runningQueryId: action.queryId } : tab) };
    case 'query.result': return { ...state, tabs: state.tabs.map(tab => tab.id === action.tabId ? { ...tab, results: [...tab.results, action.result], activeResultId: action.result.id } : tab) };
    case 'query.progress': return { ...state, tabs: state.tabs.map(tab => tab.id === action.tabId ? { ...tab, results: tab.results.map(result => result.id === action.resultId ? { ...result, totalRows: action.totalRows } : result) } : tab) };
    case 'query.complete': return { ...state, tabs: state.tabs.map(tab => tab.id === action.tabId ? { ...tab, results: tab.results.map(result => result.id === action.resultId ? { ...result, status: 'complete', totalRows: action.totalRows, truncated: action.truncated, message: action.message } : result) } : tab) };
    case 'query.resultStatus': return { ...state, tabs: state.tabs.map(tab => tab.id === action.tabId ? { ...tab, results: tab.results.map(result => result.id === action.resultId ? { ...result, status: action.status, message: action.message } : result) } : tab) };
    case 'query.batchComplete': return { ...state, status: action.status === 'complete' ? 'Ready' : action.status, tabs: state.tabs.map(tab => tab.id === action.tabId ? { ...tab, runningQueryId: undefined } : tab) };
    case 'query.error': return { ...state, status: action.message, tabs: state.tabs.map(tab => tab.id === action.tabId ? { ...tab, runningQueryId: undefined } : tab) };
    case 'result.activate': return { ...state, tabs: state.tabs.map(tab => tab.id === action.tabId ? { ...tab, activeResultId: action.resultId } : tab) };
    case 'result.close': return { ...state, tabs: state.tabs.map(tab => { if (tab.id !== action.tabId) return tab; const results = tab.results.filter(result => result.id !== action.resultId); return { ...tab, results, activeResultId: tab.activeResultId === action.resultId ? results.at(-1)?.id : tab.activeResultId }; }) };
  }
}
