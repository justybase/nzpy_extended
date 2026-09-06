import type { ReactElement } from 'react';
import type { ResultSet } from '../lib/types';
import { ResultGrid } from './ResultGrid';

export function ResultTabs({ results, activeId, onSelect, onClose }: { results: ResultSet[]; activeId?: string; onSelect(id: string): void; onClose(id: string): void }): ReactElement {
  const active = results.find(result => result.id === activeId) || results.at(-1);
  return <section className="results-panel">
    <div className="result-tabs">{results.map(result => <div key={result.id} className={`result-tab ${result.id === active?.id ? 'active' : ''}`} onClick={() => onSelect(result.id)}><span>{result.status === 'error' ? '✕ ' : ''}{result.label} · {result.totalRows}</span><button onClick={event => { event.stopPropagation(); onClose(result.id); }}>×</button></div>)}{!results.length && <span className="muted">No results yet</span>}</div>
    {active && <ResultGrid result={active} />}
  </section>;
}
