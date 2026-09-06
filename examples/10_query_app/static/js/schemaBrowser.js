/* Schema browser: lazy tree schemas -> tables/views -> columns + search + detail. */
import { ENDPOINTS, getJson } from "./api.js";

let els = {};
let onInsertText = null; // callback(text) — inserts into active editor

export function initSchemaBrowser(elements, callbacks) {
  els = elements;
  onInsertText = callbacks.onInsertText;
  els.refreshBtn.addEventListener("click", loadSchemas);
  let debounce = null;
  els.searchInput.addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      const q = els.searchInput.value.trim();
      if (q) search(q);
      else loadSchemas();
    }, 300);
  });
}

export async function loadSchemas() {
  setDetail("<p>Loading schemas…</p>");
  const { ok, json } = await getJson(ENDPOINTS.schemas);
  if (!ok || !json) {
    els.tree.innerHTML = `<div class="tree-row">Failed to load schemas</div>`;
    return;
  }
  els.tree.innerHTML = "";
  for (const schema of json.schemas || []) {
    els.tree.appendChild(schemaNode(schema));
  }
  setDetail("<p>Select a table to preview columns, distribution key and size.</p>");
}

function schemaNode(schema) {
  const node = document.createElement("div");
  node.className = "tree-node";
  const row = document.createElement("div");
  row.className = "tree-row";
  row.innerHTML = `<span class="twisty">▶</span><span>📁 ${escapeHtml(schema)}</span>`;
  const children = document.createElement("div");
  children.className = "tree-children";
  children.style.display = "none";
  let loaded = false;
  row.addEventListener("click", async () => {
    const open = children.style.display !== "none";
    children.style.display = open ? "none" : "block";
    row.querySelector(".twisty").textContent = open ? "▶" : "▼";
    if (!open && !loaded) {
      loaded = true;
      children.innerHTML = `<div class="tree-row">Loading…</div>`;
      const [{ ok, json }] = [await getJson(`${ENDPOINTS.tables}?schema=${encodeURIComponent(schema)}`)];
      children.innerHTML = "";
      const tables = (ok && json && json.tables) || [];
      if (!tables.length) children.innerHTML = `<div class="tree-row">No tables</div>`;
      for (const t of tables) children.appendChild(tableNode(schema, t));
      const v = await getJson(`${ENDPOINTS.views}?schema=${encodeURIComponent(schema)}`);
      if (v.ok && v.json && (v.json.views || []).length) {
        for (const vw of v.json.views) children.appendChild(tableNode(schema, { table_name: vw.view_name, objtype: "VIEW" }, true));
      }
    }
  });
  node.appendChild(row);
  node.appendChild(children);
  return node;
}

function tableNode(schema, t, isView = false) {
  const name = t.table_name || t.view_name || "?";
  const fq = `${schema}.${name}`;
  const node = document.createElement("div");
  node.className = "tree-node";
  const row = document.createElement("div");
  row.className = "tree-row";
  const icon = isView ? "👁" : "▦";
  row.innerHTML = `<span class="twisty">▶</span><span>${icon} ${escapeHtml(name)}</span> <span class="badge">${isView ? "VIEW" : escapeHtml(t.objtype || "TABLE")}</span>`;
  const children = document.createElement("div");
  children.className = "tree-children";
  children.style.display = "none";
  let loaded = false;
  row.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (e.detail === 2 || e.target.closest(".tree-col")) return;
    const open = children.style.display !== "none";
    children.style.display = open ? "none" : "block";
    row.querySelector(".twisty").textContent = open ? "▶" : "▼";
    showDetail(schema, name);
    if (!open && !loaded) {
      loaded = true;
      const { ok, json } = await getJson(
        `${ENDPOINTS.columns}?table=${encodeURIComponent(name)}&schema=${encodeURIComponent(schema)}`
      );
      children.innerHTML = "";
      for (const c of (ok && json && json.columns) || []) {
        const cRow = document.createElement("div");
        cRow.className = "tree-row tree-col";
        cRow.innerHTML = `<span>• ${escapeHtml(c.column_name)}</span> <span class="tree-type">${escapeHtml(c.data_type || "")}</span>`;
        cRow.title = "Click to insert column name";
        cRow.addEventListener("click", (ev) => {
          ev.stopPropagation();
          onInsertText && onInsertText(c.column_name);
        });
        children.appendChild(cRow);
      }
      if (!children.children.length) children.innerHTML = `<div class="tree-row">No columns</div>`;
    }
  });
  row.addEventListener("dblclick", (e) => {
    e.stopPropagation();
    onInsertText && onInsertText(fq);
  });
  row.title = "Click to expand · double-click to insert " + fq;
  node.appendChild(row);
  node.appendChild(children);
  return node;
}

async function showDetail(schema, table) {
  setDetail("<p>Loading detail…</p>");
  const { ok, json } = await getJson(
    `${ENDPOINTS.tableDetail}?table=${encodeURIComponent(table)}&schema=${encodeURIComponent(schema)}`
  );
  if (!ok || !json) {
    setDetail("<p>Failed to load detail.</p>");
    return;
  }
  const cols = (json.columns || []).map(
    (c) => `<div>• <b>${escapeHtml(c.column_name)}</b> <span class="tree-type">${escapeHtml(c.data_type || "")} ${c.nullable === "N" ? "NOT NULL" : ""}</span></div>`
  ).join("");
  setDetail(
    `<h4>${escapeHtml(schema)}.${escapeHtml(table)}</h4>` +
    `<div>Distribution: <b>${escapeHtml((json.distribution_key || []).join(", ") || "RANDOM")}</b></div>` +
    (json.size_mb != null ? `<div>Size: <b>${escapeHtml(String(json.size_mb))} MB</b></div>` : "") +
    `<div style="margin-top:6px">${cols || "No columns"}</div>`
  );
}

async function search(q) {
  els.tree.innerHTML = `<div class="tree-row">Searching…</div>`;
  const { ok, json } = await getJson(`${ENDPOINTS.search}?q=${encodeURIComponent(q)}`);
  if (!ok || !json) {
    els.tree.innerHTML = `<div class="tree-row">Search failed</div>`;
    return;
  }
  els.tree.innerHTML = "";
  for (const r of json.results || []) {
    const row = document.createElement("div");
    row.className = "tree-row";
    row.innerHTML = `<span>${r.object_type === "VIEW" ? "👁" : r.object_type === "PROCEDURE" ? "⚙" : "▦"} ${escapeHtml(r.schema)}.${escapeHtml(r.object_name)}</span> <span class="badge">${escapeHtml(r.object_type)}</span>`;
    row.title = "Double-click to insert";
    const fq = `${r.schema}.${r.object_name}`;
    row.addEventListener("dblclick", () => onInsertText && onInsertText(fq));
    els.tree.appendChild(row);
  }
  if (!els.tree.children.length) els.tree.innerHTML = `<div class="tree-row">No matches</div>`;
}

function setDetail(html) {
  if (els.detail) els.detail.innerHTML = html;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
}
