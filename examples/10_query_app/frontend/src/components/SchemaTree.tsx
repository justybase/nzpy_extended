import { useEffect, useState } from 'react';
import type { ReactElement } from 'react';
import { api } from '../lib/api';
import type { SchemaNode } from '../lib/types';
import type { MenuItem } from './ContextMenu';

function kindIcon(kind: string, objectType?: string): string {
  if (kind === 'database') return '◉';
  if (kind === 'schema') return '▤';
  if (kind === 'column') return '⌁';
  if (objectType === 'VIEW') return '👁';
  if (objectType === 'PROCEDURE') return 'ƒ';
  if (objectType === 'SYNONYM') return '⎋';
  if (objectType === 'EXTERNAL TABLE') return '⧉';
  return '▦';
}

function TreeNode({ node, onMenu, onSelect }: { node: SchemaNode; onMenu(node: SchemaNode, event: React.MouseEvent): void; onSelect(node: SchemaNode): void }): ReactElement {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [children, setChildren] = useState<SchemaNode[]>([]);
  const [error, setError] = useState('');
  async function toggle(): Promise<void> {
    if (!node.has_children) { onSelect(node); return; }
    setOpen(value => !value);
    if (!children.length && !loading) {
      setLoading(true);
      setError('');
      try { setChildren((await api.tree(node.id, node.database)).nodes); } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not load schema node.'); } finally { setLoading(false); }
    }
  }
  return <div className="tree-node">
    <div className={`tree-row ${open ? 'is-open' : ''}`} onClick={() => void toggle()} onContextMenu={event => onMenu(node, event)} title={node.description || node.label}>
      <span className="twisty">{node.has_children ? (open ? '▾' : '▸') : '·'}</span>
      <span className={`node-icon node-${node.kind}`} aria-hidden="true">{kindIcon(node.kind, node.object_type)}</span>
      <span className="tree-label">{node.label}</span>
      {node.object_type && <span className="badge">{node.object_type}</span>}
      {node.column_type && <span className="badge is-type">{node.column_type}</span>}
      {loading && <span className="muted">…</span>}
    </div>
    {open && <div className="tree-children">{children.length ? children.map(child => <TreeNode key={child.id} node={child} onMenu={onMenu} onSelect={onSelect} />) : error ? <div className="error-banner">{error}</div> : !loading && <div className="empty-row">No objects</div>}</div>}
  </div>;
}

export function SchemaTree({ onMenu, onSelect, onRefresh, refreshKey = 0 }: { onMenu(node: SchemaNode, event: React.MouseEvent): void; onSelect(node: SchemaNode): void; onRefresh(): void; refreshKey?: number }): ReactElement {
  const [nodes, setNodes] = useState<SchemaNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [searchRows, setSearchRows] = useState<SchemaNode[]>([]);
  async function load(): Promise<void> { setLoading(true); setError(''); try { setNodes((await api.tree()).nodes); } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not load schema.'); } finally { setLoading(false); } }
  useEffect(() => { void load(); }, [refreshKey]);
  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      if (!search.trim()) { setSearchRows([]); return; }
      try {
        const response = await fetch(`/api/v1/schema/search?q=${encodeURIComponent(search)}`).then(result => result.json());
        if (!cancelled) setSearchRows((response.results || []).map((item: Record<string, string | boolean>, index: number) => ({ id: String(item.id || `search-${index}`), kind: 'object', label: `${item.schema}.${item.object_name}`, schema: String(item.schema || ''), object_name: String(item.object_name || ''), object_type: String(item.object_type || 'TABLE'), has_children: Boolean(item.has_children) })));
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : 'Could not search schema.');
      }
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [search]);
  const visible = search ? searchRows : nodes;
  return <section className="schema-panel">
    <div className="panel-title"><span className="panel-title-text">Schema</span><span className="panel-count">{loading ? '…' : `${visible.length}`}</span><button className="icon-button" onClick={onRefresh} title="Refresh schema">↻</button></div>
    <div className="schema-search">
      <span className="schema-search-box">
        <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.6" /><line x1="10.6" y1="10.6" x2="14" y2="14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
        <input value={search} onChange={event => setSearch(event.target.value)} placeholder="Search objects…" aria-label="Search schema objects" />
        {search && <button type="button" className="schema-search-clear" onClick={() => setSearch('')} aria-label="Clear schema search">×</button>}
      </span>
    </div>
    <div className="schema-scroll">{error && <div className="error-banner">{error}</div>}{visible.map(node => <TreeNode key={node.id} node={node} onMenu={onMenu} onSelect={onSelect} />)}{!loading && !error && !visible.length && <div className="empty-row">{search ? 'No matching objects' : 'No schema objects'}</div>}</div>
  </section>;
}

export function schemaMenu(node: SchemaNode, insert: (value: string) => void, openQuery: (sql: string) => void, refresh: () => void, showDetail: () => void): MenuItem[] {
  const qualified = node.kind === 'column'
    ? [node.database, node.schema, node.object_name, node.column_name || node.label].filter(Boolean).join('.')
    : [node.database, node.schema, node.object_name || node.label].filter(Boolean).join('.');
  const items: MenuItem[] = [
    { label: 'Copy qualified name', action: () => void navigator.clipboard?.writeText(qualified) },
    { label: 'Insert name into editor', action: () => insert(qualified) },
    { label: 'Refresh node', action: refresh },
  ];
  if (node.kind === 'object' && ['TABLE', 'VIEW', 'EXTERNAL TABLE', 'PROCEDURE', 'SYNONYM'].includes(node.object_type || '')) {
    if (['TABLE', 'VIEW', 'EXTERNAL TABLE'].includes(node.object_type || '')) items.splice(2, 0, { label: 'New SELECT tab', action: () => openQuery(`SELECT *\nFROM ${qualified}\nLIMIT 100;`) });
    items.push({ label: ['VIEW', 'PROCEDURE'].includes(node.object_type || '') ? 'Show definition' : 'Show details', action: showDetail });
  }
  return items;
}
