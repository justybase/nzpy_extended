/* Thin fetch wrapper around the FastAPI backend. */
export const ENDPOINTS = {
  query: "/api/query",
  cancel: "/api/cancel",
  export: "/api/export",
  import: "/api/import",
  schemas: "/api/schemas",
  tables: "/api/tables",
  views: "/api/views",
  columns: "/api/columns",
  procedures: "/api/procedures",
  search: "/api/search",
  tableDetail: "/api/table-detail",
  schemaCache: "/api/schema-cache",
  schemaRefresh: "/api/schema/refresh",
  version: "/api/version",
  status: "/api/status",
};

async function asJson(resp) {
  const text = await resp.text();
  try {
    return { ok: resp.ok, status: resp.status, json: JSON.parse(text), text };
  } catch {
    return { ok: false, status: resp.status, json: null, text };
  }
}

export async function runQuery({ sql, timeout, queryId }) {
  const fd = new FormData();
  fd.append("sql", sql);
  fd.append("timeout", timeout > 0 ? String(timeout) : "");
  fd.append("query_id", queryId);
  const resp = await fetch(ENDPOINTS.query, { method: "POST", body: fd });
  return asJson(resp);
}

export async function cancelQuery(queryId) {
  const fd = new FormData();
  fd.append("query_id", queryId);
  const resp = await fetch(ENDPOINTS.cancel, { method: "POST", body: fd });
  return asJson(resp);
}

export async function getJson(url) {
  const resp = await fetch(url);
  return asJson(resp);
}

export async function postExport(sql, format = "csv") {
  const fd = new FormData();
  fd.append("sql", sql);
  fd.append("format", format);
  return fetch(ENDPOINTS.export, { method: "POST", body: fd });
}

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
