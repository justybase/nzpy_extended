import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactElement } from 'react';
import { TabulatorFull as Tabulator } from 'tabulator-tables';
import 'tabulator-tables/dist/css/tabulator_midnight.min.css';
import { downloadXlsb, downloadXlsx } from '@justybase/spreadsheet-tasks/browser/browser-spreadsheet.js';
import { api } from '../lib/api';
import type { ResultSet } from '../lib/types';

type TabulatorTable = {
  destroy(): void;
  setData(): Promise<unknown>;
  setGroupBy(field: string | string[] | false): void;
  getFilters(includeHeaderFilters?: boolean): Array<{ field?: string; value?: string }>;
  getSorters(): Array<{ field?: string; dir?: 'asc' | 'desc' }>;
};

type TabulatorFilter = { field?: string; value?: string };
type TabulatorSorter = { field?: string; dir?: 'asc' | 'desc' };

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
  const elementRef = useRef<HTMLDivElement>(null);
  const tableRef = useRef<TabulatorTable | null>(null);
  const [globalFilter, setGlobalFilter] = useState('');
  const [groupFields, setGroupFields] = useState<string[]>([]);
  const [groupDragOver, setGroupDragOver] = useState(false);
  const [error, setError] = useState('');
  const [exporting, setExporting] = useState('');
  const columns = result.columns || [];
  const names = useMemo(() => columns.map(columnName), [columns]);
  const columnKey = `${result.sessionId}:${names.join('\0')}`;
  const globalFilterRef = useRef(globalFilter);
  globalFilterRef.current = globalFilter;

  function copy(text: string): void { void navigator.clipboard?.writeText(text); }

  useEffect(() => {
    setGlobalFilter('');
    setGroupFields([]);
    setError('');
    setExporting('');
  }, [result.sessionId]);

  useEffect(() => {
    if (!elementRef.current || !columns.length) return undefined;
    const fields = names.map((_name, index) => `c${index}`);
    const table = new Tabulator(elementRef.current, {
      height: '100%',
      layout: 'fitDataStretch',
      placeholder: 'No rows',
      responsiveLayout: false,
      // Native header drag is reserved for the Group rows drop target.
      // Allowing Tabulator's column-move module here makes the browser drag
      // event disappear before React can receive it.
      movableColumns: false,
      resizableColumnFit: false,
      selectableRange: true,
      selectableRangeColumns: true,
      selectableRangeRows: true,
      clipboard: true,
      clipboardCopyConfig: { columnHeaders: true, rowHeaders: false },
      pagination: true,
      paginationMode: 'remote',
      paginationSize: 5000,
      paginationSizeSelector: [100, 500, 1000, 5000],
      filterMode: 'remote',
      sortMode: 'remote',
      rowContextMenu: (row: { getData(): Record<string, unknown> }) => {
        const data = row.getData();
        const values = names.map((_name, index) => data[`c${index}`]);
        return [
          { label: 'Copy row as TSV', action: () => copy(values.map(cell).join('\t')) },
          { label: 'Copy row as JSON', action: () => copy(JSON.stringify(Object.fromEntries(names.map((name, index) => [name, values[index] ?? null])), null, 2)) },
          { label: 'Filter first cell', action: () => setGlobalFilter(cell(values[0])) },
        ];
      },
      columns: columns.map((column, index) => ({
        field: fields[index],
        title: names[index],
        headerTooltip: `${columnType(column)} · Drag header to Group rows`,
        headerFilter: 'input',
        headerFilterLiveFilter: false,
        sorter: /INT|DECIMAL|NUMERIC|NUMBER|REAL|FLOAT|DOUBLE/i.test(columnType(column)) ? 'number' : 'string',
        formatter: (cellValue: { getValue(): unknown }) => cell(cellValue.getValue()),
      })),
      ajaxURL: `/api/v1/results/${result.sessionId}/page`,
      ajaxRequestFunc: async (_url: string, _config: unknown, params: { page?: number; size?: number; filter?: TabulatorFilter[]; sorters?: TabulatorSorter[] }) => {
        const size = Number(params.size || 200);
        const filters = (params.filter || [])
          .map(item => ({ columnIndex: fields.indexOf(String(item.field)), value: String(item.value || '') }))
          .filter(item => item.columnIndex >= 0 && item.value);
        const sorting = (params.sorters || [])
          .map(item => ({ columnIndex: fields.indexOf(String(item.field)), desc: item.dir === 'desc' }))
          .filter(item => item.columnIndex >= 0);
        try {
          const page = await api.page(result.sessionId, {
            offset: (Number(params.page || 1) - 1) * size,
            limit: size,
            globalFilter: globalFilterRef.current,
            columnFilters: filters,
            sorting,
          });
          return {
            last_page: Math.max(1, Math.ceil(page.total_rows / size)),
            last_row: page.total_rows,
            data: page.rows.map(row => Object.fromEntries(fields.map((field, index) => [field, row[index]]))),
          };
        } catch (reason) {
          setError(reason instanceof Error ? reason.message : 'Could not load result page.');
          throw reason;
        }
      },
    }) as unknown as TabulatorTable;
    tableRef.current = table;
    const headerCleanups: Array<() => void> = [];
    const attachHeaderDrag = (): void => {
      const draggableHeaders = Array.from(elementRef.current?.querySelectorAll<HTMLElement>('.tabulator-col[tabulator-field]') || []);
      draggableHeaders.forEach(header => {
        if (header.dataset.groupDragBound === 'true') return;
        const field = header.getAttribute('tabulator-field') || '';
        const onDragStart = (event: DragEvent): void => {
          event.dataTransfer?.setData('text/x-grid-field', field);
          event.dataTransfer?.setData('text/plain', field);
          if (event.dataTransfer) event.dataTransfer.effectAllowed = 'copy';
          header.classList.add('group-dragging');
        };
        const onDragEnd = (): void => header.classList.remove('group-dragging');
        header.dataset.groupDragBound = 'true';
        header.draggable = true;
        header.title = 'Drag this column header to Group rows';
        header.addEventListener('dragstart', onDragStart);
        header.addEventListener('dragend', onDragEnd);
        const titleElement = header.querySelector<HTMLElement>('.tabulator-col-title');
        if (titleElement) {
          titleElement.draggable = true;
          titleElement.addEventListener('dragstart', onDragStart);
          titleElement.addEventListener('dragend', onDragEnd);
        }
        headerCleanups.push(() => {
          delete header.dataset.groupDragBound;
          header.draggable = false;
          header.removeEventListener('dragstart', onDragStart);
          header.removeEventListener('dragend', onDragEnd);
          if (titleElement) {
            titleElement.draggable = false;
            titleElement.removeEventListener('dragstart', onDragStart);
            titleElement.removeEventListener('dragend', onDragEnd);
          }
        });
      });
    };
    attachHeaderDrag();
    const headerAttachTimer = window.setTimeout(attachHeaderDrag, 0);
    return () => {
      window.clearTimeout(headerAttachTimer);
      headerCleanups.forEach(cleanup => cleanup());
      table.destroy();
      if (tableRef.current === table) tableRef.current = null;
    };
  // columnKey deliberately recreates the grid when a running query publishes columns.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [columnKey]);

  useEffect(() => {
    const table = tableRef.current;
    if (!table) return;
    const timer = window.setTimeout(() => { void table.setData(); }, 250);
    return () => window.clearTimeout(timer);
  }, [globalFilter]);

  useEffect(() => {
    if (tableRef.current) tableRef.current.setGroupBy(groupFields.length ? groupFields : false);
  }, [groupFields]);

  function addGroupField(field: string): void {
    setGroupFields(current => current.includes(field) ? current : [...current, field]);
  }

  function removeGroupField(field: string): void {
    setGroupFields(current => current.filter(item => item !== field));
  }

  function onGroupDrop(event: React.DragEvent<HTMLDivElement>): void {
    event.preventDefault();
    setGroupDragOver(false);
    const field = event.dataTransfer.getData('text/x-grid-field') || event.dataTransfer.getData('text/plain');
    if (field) addGroupField(field);
  }

  async function exportRows(format: 'csv' | 'xlsx' | 'xlsb'): Promise<void> {
    if (exporting) return;
    const table = tableRef.current;
    if (!table) return;
    setExporting(`Preparing ${format.toUpperCase()}…`);
    setError('');
    try {
      const fields = names.map((_name, index) => `c${index}`);
      const filters = table.getFilters(true)
        .map(item => ({ columnIndex: fields.indexOf(String(item.field)), value: String(item.value || '') }))
        .filter(item => item.columnIndex >= 0 && item.value);
      const sorting = table.getSorters()
        .map(item => ({ columnIndex: fields.indexOf(String(item.field)), desc: item.dir === 'desc' }))
        .filter(item => item.columnIndex >= 0);
      const rows: unknown[][] = [];
      let offset = 0;
      while (true) {
        const page = await api.page(result.sessionId, { offset, limit: 1000, globalFilter, columnFilters: filters, sorting });
        rows.push(...page.rows);
        setExporting(`Preparing ${format.toUpperCase()}… ${rows.length.toLocaleString()} rows`);
        if (!page.has_more || !page.rows.length) break;
        offset += page.rows.length;
      }
      if (format === 'xlsx') downloadXlsx('result.xlsx', rows, names, 'SQL result');
      else if (format === 'xlsb') downloadXlsb('result.xlsb', rows, names, 'SQL result');
      else {
        const escape = (value: unknown): string => `"${cell(value).replaceAll('"', '""')}"`;
        const csv = [names, ...rows].map(row => row.map(escape).join(',')).join('\r\n');
        const link = document.createElement('a');
        link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
        link.download = 'result.csv';
        link.click();
        URL.revokeObjectURL(link.href);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `Could not export ${format.toUpperCase()}.`);
    } finally {
      setExporting('');
    }
  }

  if (!columns.length) {
    return <div className="result-grid-shell"><div className="grid-toolbar"><span>{result.status === 'running' ? 'Loading columns…' : 'No columns'}</span></div></div>;
  }

  return <div className="result-grid-shell">
    <div className="grid-toolbar grid-toolbar-main">
      <label className="grid-search"><span>Search results</span><input aria-label="Search result grid" value={globalFilter} onChange={event => setGlobalFilter(event.target.value)} placeholder="Search all columns…" /></label>
      <span className="grid-help">Sort by header · filter below header · select a range · Ctrl+C copies</span>
      <button onClick={() => void tableRef.current?.setData()} disabled={Boolean(exporting)}>Refresh</button>
      <div className="export-menu" aria-label="Export result"><span>Export</span><button onClick={() => void exportRows('csv')} disabled={Boolean(exporting)}>CSV</button><button onClick={() => void exportRows('xlsx')} disabled={Boolean(exporting)}>XLSX</button><button onClick={() => void exportRows('xlsb')} disabled={Boolean(exporting)}>XLSB</button></div>
    </div>
    <div className="grid-groupbar">
      <strong>Group rows</strong>
      <div className={`group-drop-zone ${groupFields.length ? 'has-groups' : ''} ${groupDragOver ? 'drag-over' : ''}`} onDragEnter={() => setGroupDragOver(true)} onDragOver={event => { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; }} onDragLeave={() => setGroupDragOver(false)} onDrop={onGroupDrop}>
        {!groupFields.length && <span className="group-drop-placeholder">Drag column headers here to group rows</span>}
        {groupFields.map(field => <button className="group-chip" key={field} onClick={() => removeGroupField(field)} title="Remove grouping">{names[Number(field.slice(1))]} ×</button>)}
      </div>
      {groupFields.length > 0 && <button className="clear-groups" onClick={() => setGroupFields([])}>Clear groups</button>}
    </div>
    {exporting && <div className="export-progress" role="status">{exporting}</div>}
    {error && <div className="error-banner">{error}</div>}
    <div className="tabulator-host" ref={elementRef} />
  </div>;
}
