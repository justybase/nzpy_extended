import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactElement } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { api } from '../lib/api';
import type { PageResponse, ResultSet } from '../lib/types';
import { ContextMenu } from './ContextMenu';

function columnName(column: Record<string, unknown>, index: number): string {
  return String(column.ColumnName || column.column_name || column.name || `column_${index + 1}`);
}

function columnType(column: Record<string, unknown>): string {
  return String(column.DataType || column.data_type || column.type || '');
}

function cell(value: unknown): string {
  return value === null || value === undefined ? 'NULL' : typeof value === 'object' ? JSON.stringify(value) : String(value);
}

export function ResultGrid({ result }: { result: ResultSet }): ReactElement {
  const [page, setPage] = useState<PageResponse | null>(null);
  const [globalFilter, setGlobalFilter] = useState('');
  const [columnFilters, setColumnFilters] = useState<Record<number, string>>({});
  const [sorting, setSorting] = useState<{ columnIndex: number; desc: boolean }[]>([]);
  const [columnWidths, setColumnWidths] = useState<Record<number, number>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [context, setContext] = useState<{ x: number; y: number; row?: unknown[] } | null>(null);
  const [offset, setOffset] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const requestRef = useRef(0);
  const rows = page?.rows || [];
  const columns = page?.columns?.length ? page.columns : result.columns;
  const names = columns.map(columnName);
  const filters = useMemo(
    () => Object.entries(columnFilters).filter(([, value]) => value.trim()).map(([columnIndex, value]) => ({ columnIndex: Number(columnIndex), value })),
    [columnFilters],
  );
  const tableWidth = names.reduce((sum, _, index) => sum + (columnWidths[index] || 180), 0);
  const virtualizer = useVirtualizer({ count: rows.length, getScrollElement: () => scrollRef.current, estimateSize: () => 32, overscan: 12 });

  async function load(nextOffset = offset): Promise<void> {
    const request = ++requestRef.current;
    setLoading(true);
    setError('');
    try {
      const nextPage = await api.page(result.sessionId, { offset: nextOffset, limit: 200, globalFilter, columnFilters: filters, sorting });
      if (request === requestRef.current) setPage(nextPage);
    } catch (reason) {
      if (request === requestRef.current) setError(reason instanceof Error ? reason.message : 'Could not load result page.');
    } finally {
      if (request === requestRef.current) setLoading(false);
    }
  }

  useEffect(() => {
    setOffset(0);
    void load(0);
  }, [result.sessionId, globalFilter, JSON.stringify(filters), JSON.stringify(sorting)]);

  useEffect(() => {
    setPage(null);
    setGlobalFilter('');
    setColumnFilters({});
    setSorting([]);
    setColumnWidths({});
    setOffset(0);
  }, [result.sessionId]);

  function toggleSort(index: number): void {
    const current = sorting.find(item => item.columnIndex === index);
    setSorting(current
      ? (current.desc
        ? sorting.filter(item => item.columnIndex !== index)
        : sorting.map(item => item.columnIndex === index ? { ...item, desc: true } : item))
      : [...sorting, { columnIndex: index, desc: false }]);
  }

  function resizeColumn(index: number, event: React.PointerEvent<HTMLSpanElement>): void {
    event.preventDefault();
    event.stopPropagation();
    const startX = event.clientX;
    const startWidth = columnWidths[index] || 180;
    const move = (moveEvent: PointerEvent): void => {
      setColumnWidths(current => ({ ...current, [index]: Math.max(90, startWidth + moveEvent.clientX - startX) }));
    };
    const stop = (): void => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', stop);
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', stop, { once: true });
  }

  function copy(text: string): void { void navigator.clipboard?.writeText(text); }

  async function exportRows(): Promise<void> {
    try {
      const response = await fetch(`/api/v1/results/${result.sessionId}/export`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ format: 'csv', globalFilter, columnFilters: filters, sorting }) });
      if (!response.ok) throw new Error(`Export failed (HTTP ${response.status})`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'result.csv';
      link.click();
      URL.revokeObjectURL(url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not export result.');
    }
  }

  const totalRows = page?.total_rows ?? result.totalRows;
  const firstRow = totalRows ? offset + 1 : 0;
  const lastRow = totalRows ? Math.min(offset + rows.length, totalRows) : 0;
  const rowTemplate = names.map((_, index) => `${columnWidths[index] || 180}px`).join(' ') || 'minmax(180px, 1fr)';

  return <div className="result-grid-shell">
    <div className="grid-toolbar"><input value={globalFilter} onChange={event => setGlobalFilter(event.target.value)} placeholder="Filter loaded session…" /><span>{loading ? 'Loading…' : `${totalRows.toLocaleString()} rows`}</span><button onClick={() => void load(offset)}>Refresh</button><button onClick={() => void exportRows()}>Export CSV</button></div>
    {error && <div className="error-banner">{error}</div>}
    <div className="grid-scroll" ref={scrollRef} onContextMenu={event => { event.preventDefault(); setContext(null); }}>
      <table className="data-grid" style={{ minWidth: `${Math.max(180, tableWidth)}px` }}><thead><tr>{names.map((name, index) => <th key={`${name}-${index}`} style={{ width: columnWidths[index] || 180, minWidth: columnWidths[index] || 180 }}><button className="header-sort" onClick={() => toggleSort(index)}>{name} <small>{columnType(columns[index])}</small>{sorting.find(item => item.columnIndex === index) ? (sorting.find(item => item.columnIndex === index)!.desc ? ' ↓' : ' ↑') : ''}</button><input value={columnFilters[index] || ''} onChange={event => setColumnFilters(current => ({ ...current, [index]: event.target.value }))} placeholder="filter" /><span className="column-resizer" onPointerDown={event => resizeColumn(index, event)} /></th>)}</tr></thead><tbody><tr style={{ height: `${virtualizer.getTotalSize()}px` }}><td colSpan={Math.max(1, names.length)} className="virtual-cell"><div style={{ position: 'relative', height: `${virtualizer.getTotalSize()}px`, minWidth: `${Math.max(180, tableWidth)}px` }}>{virtualizer.getVirtualItems().map(item => { const row = rows[item.index]; return <div key={item.key} className="virtual-row" style={{ transform: `translateY(${item.start}px)`, gridTemplateColumns: rowTemplate }} onContextMenu={event => { event.preventDefault(); setContext({ x: event.clientX, y: event.clientY, row }); }}>{row.map((value, index) => <span key={index} className={value == null ? 'null-cell' : ''} title={cell(value)}>{cell(value)}</span>)}</div>; })}</div></td></tr></tbody></table>
    </div>
    <div className="pager"><button disabled={offset === 0} onClick={() => { const next = Math.max(0, offset - 200); setOffset(next); void load(next); }}>Previous</button><span>{firstRow.toLocaleString()}–{lastRow.toLocaleString()}</span><button disabled={!page?.has_more} onClick={() => { const next = offset + 200; setOffset(next); void load(next); }}>Next</button></div>
    {context && <ContextMenu x={context.x} y={context.y} onClose={() => setContext(null)} items={[{ label: 'Copy row as TSV', action: () => copy((context.row || []).map(cell).join('\t')) }, { label: 'Copy row as JSON', action: () => copy(JSON.stringify(Object.fromEntries(names.map((name, index) => [name, context.row?.[index] ?? null])), null, 2)) }, { label: 'Filter first cell', action: () => setGlobalFilter(cell(context.row?.[0])) }]} />}
  </div>;
}
