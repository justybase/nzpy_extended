import type { PageResponse, SchemaNode } from './types';

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) }, ...init });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body as T;
}

export const api = {
  preview: (sql: string, database?: string) => json<{ containsWrite: boolean; previewToken: string; statements: unknown[] }>('/api/v1/query/preview', { method: 'POST', body: JSON.stringify({ sql, database }) }),
  execute: (body: unknown, signal?: AbortSignal) => json<{ queryId: string; status: string; error?: string; results: Array<{ resultSetId: string; sessionId: string; statementIndex: number; columns: Record<string, unknown>[]; status: string; totalRows: number; truncated?: boolean; message?: string }> }>('/api/v1/query/execute', { method: 'POST', body: JSON.stringify(body), signal }),
  tree: (parentId?: string, database?: string) => json<{ nodes: SchemaNode[] }>(`/api/v1/schema/tree?${new URLSearchParams({ ...(parentId ? { parent_id: parentId } : {}), ...(database ? { database } : {}) })}`),
  detail: (node: SchemaNode) => json<Record<string, unknown>>(`/api/v1/schema/detail?${new URLSearchParams({ table: node.object_name || node.label, ...(node.schema ? { schema: node.schema } : {}), ...(node.database ? { database: node.database } : {}) })}`),
  completion: (sql: string, offset: number, database?: string, schema?: string) => json<{ items: Array<Record<string, unknown>> }>('/api/v1/language/completion', { method: 'POST', body: JSON.stringify({ sql, offset, database, schema }) }),
  diagnostics: (sql: string, database?: string, schema?: string) => json<{ diagnostics: Array<Record<string, unknown>> }>('/api/v1/language/diagnostics', { method: 'POST', body: JSON.stringify({ sql, database, schema }) }),
  page: (sessionId: string, body: unknown) => json<PageResponse>(`/api/v1/results/${sessionId}/page`, { method: 'POST', body: JSON.stringify(body) }),
  manifest: (sessionId: string) => json<Record<string, unknown>>(`/api/v1/results/${sessionId}`),
  deleteResult: (sessionId: string) => json(`/api/v1/results/${sessionId}`, { method: 'DELETE' }),
  refreshSchema: (database?: string, schema?: string) => json('/api/v1/schema/refresh', { method: 'POST', body: JSON.stringify({ database, schema }) }),
};

export function download(url: string, name: string): void {
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
}
