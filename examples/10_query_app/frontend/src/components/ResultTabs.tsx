import type { ReactElement } from 'react';
import type { ResultSet } from '../lib/types';
import { ResultGrid } from './ResultGrid';

function statusClass(status: ResultSet['status']): string {
  if (status === 'complete') return 'is-ok';
  if (status === 'running') return 'is-running';
  if (status === 'error') return 'is-error';
  return 'is-muted';
}

function statusGlyph(status: ResultSet['status']): string {
  if (status === 'complete') return '●';
  if (status === 'running') return '◐';
  if (status === 'error') return '●';
  return '○';
}

export function ResultTabs({ results, activeId, onSelect, onClose }: { results: ResultSet[]; activeId?: string; onSelect(id: string): void; onClose(id: string): void }): ReactElement {
  const active = results.find(result => result.id === activeId) || results.at(-1);
  return <section className="results-panel">
    <div className="result-tabs" role="tablist" aria-label="Result sets">
      {results.map(result => (
        <div
          key={result.id}
          role="tab"
          aria-selected={result.id === active?.id}
          className={`result-tab ${result.id === active?.id ? 'active' : ''}`}
          onClick={() => onSelect(result.id)}
          title={`${result.label} — ${result.totalRows.toLocaleString('en-US')} rows · ${result.status}`}
        >
          <span className={`result-status ${statusClass(result.status)}`} aria-hidden="true">{statusGlyph(result.status)}</span>
          <span className="result-tab-label">{result.label}</span>
          <span className="result-tab-count">{result.totalRows.toLocaleString('en-US')}</span>
          <button onClick={event => { event.stopPropagation(); onClose(result.id); }} title="Close result" aria-label={`Close ${result.label}`}>×</button>
        </div>
      ))}
      {!results.length && <span className="muted">No results yet — run a query to see data here</span>}
    </div>
    {active ? <ResultGrid key={active.sessionId} result={active} /> : <div className="results-empty"><div className="results-empty-icon">▦</div><p>No results yet</p><span>Run the script with Ctrl+Enter to populate this panel.</span></div>}
  </section>;
}
