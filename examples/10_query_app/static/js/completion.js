/* Local schema-aware SQL completion.
 *
 * Inspired by JustyBase's completionEngine/keywordProvider, reduced to what
 * a static example can do without a language server:
 *
 *   - Netezza keyword/function catalog (static, incl. DISTRIBUTE/GROOM).
 *   - Snippet catalog (subset of JustyBase's netezza.code-snippets).
 *   - Schema cache from GET /api/schema-cache + lazy per-table columns.
 *   - Context rules mirroring app/services/completion_context.py:
 *       FROM|JOIN|INTO|UPDATE|TABLE ... -> tables/views
 *       SELECT|WHERE|...              -> columns of FROM tables + keywords
 *       alias.                        -> columns of that table
 *
 * The Python twin lives in app/services/completion_context.py and is unit
 * tested (tests/test_completion_context.py). Keep regexes aligned.
 */
import { ENDPOINTS, getJson } from "./api.js";

export const NETEZZA_KEYWORDS = [
  "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "IN", "IS", "NULL",
  "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "FULL JOIN", "JOIN", "ON", "USING",
  "GROUP BY", "ORDER BY", "HAVING", "LIMIT", "OFFSET",
  "INSERT INTO", "VALUES", "UPDATE", "SET", "DELETE FROM", "MERGE INTO",
  "CREATE TABLE", "CREATE TEMP TABLE", "CREATE VIEW", "CREATE OR REPLACE VIEW",
  "ALTER TABLE", "DROP TABLE", "DROP VIEW", "TRUNCATE TABLE",
  "AS", "DISTINCT", "UNION", "UNION ALL", "ALL", "EXISTS", "BETWEEN",
  "LIKE", "ILIKE", "ASC", "DESC", "TRUE", "FALSE",
  "CASE", "WHEN", "THEN", "ELSE", "END", "WITH",
  "COUNT", "SUM", "AVG", "MIN", "MAX", "COALESCE", "NVL", "CAST",
  "ROW_NUMBER", "RANK", "CURRENT_DATE", "CURRENT_TIMESTAMP",
  "DISTRIBUTE ON", "DISTRIBUTE ON RANDOM", "ORGANIZE ON",
  "GROOM TABLE", "GENERATE STATISTICS",
  "INT", "INTEGER", "BIGINT", "SMALLINT", "VARCHAR", "NVARCHAR", "CHAR",
  "BOOLEAN", "FLOAT", "DOUBLE", "NUMERIC", "DECIMAL",
  "DATE", "TIME", "TIMESTAMP", "INTERVAL",
];

export const NETEZZA_SNIPPETS = [
  { prefix: "nzselect", label: "SELECT..FROM..WHERE", insert: "SELECT ${1:column1}, ${2:column2}\nFROM ${3:schema}.${4:table}\nWHERE ${5:condition}\nLIMIT 100;" },
  { prefix: "nzjoin", label: "INNER JOIN template", insert: "SELECT a.${1:col1}, b.${2:col2}\nFROM ${3:schema}.${4:table1} a\nINNER JOIN ${5:schema}.${6:table2} b ON a.${7:id} = b.${8:id};" },
  { prefix: "nzcte", label: "WITH cte AS (..)", insert: "WITH ${1:cte} AS (\n    SELECT ${2:*} FROM ${3:schema}.${4:table}\n)\nSELECT * FROM ${1:cte};" },
  { prefix: "nzgroupby", label: "GROUP BY + HAVING", insert: "SELECT ${1:col}, COUNT(*) AS cnt\nFROM ${2:schema}.${3:table}\nGROUP BY ${1:col}\nHAVING COUNT(*) > 1\nORDER BY cnt DESC;" },
  { prefix: "nzgroom", label: "GROOM TABLE", insert: "GROOM TABLE ${1:schema}.${2:table} VERSIONS;" },
  { prefix: "nzstats", label: "GENERATE STATISTICS", insert: "GENERATE STATISTICS ON ${1:schema}.${2:table};" },
];

/* -- context detection (mirror of completion_context.py) -- */
const TABLE_POS_RE = /\b(FROM|JOIN|INTO|UPDATE|TABLE)\s+[A-Za-z0-9_.$]*$/i;
const DOT_RE = /([A-Za-z_][A-Za-z0-9_$]*)\.\s*[A-Za-z0-9_$]*$/;
const COLUMN_HINT_RE = /\b(SELECT|WHERE|AND|OR|BY|HAVING|ON|=|,|\(|WHEN)\s+[A-Za-z0-9_.$, ]*$/i;
const FROM_JOIN_RE = /\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_$.]*)(?:\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_$]*))?/gi;

export function detectContext(beforeCursor) {
  if (DOT_RE.test(beforeCursor)) return "column-dot";
  if (TABLE_POS_RE.test(beforeCursor)) return "table";
  if (COLUMN_HINT_RE.test(beforeCursor)) return "column";
  return "default";
}

export function dotPrefix(beforeCursor) {
  const m = beforeCursor.match(DOT_RE);
  return m ? m[1] : "";
}

export function extractFromTables(sql) {
  const out = [];
  const skip = new Set(["WHERE", "GROUP", "ORDER", "LIMIT", "JOIN", "ON", "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "HAVING", "UNION"]);
  let m;
  FROM_JOIN_RE.lastIndex = 0;
  while ((m = FROM_JOIN_RE.exec(sql))) {
    let alias = (m[2] || "").toUpperCase();
    if (skip.has(alias)) alias = "";
    const name = m[1].toUpperCase();
    out.push({ name, alias: alias || name.split(".").pop() });
  }
  return out;
}

/* -- schema cache -- */
export const schemaCache = { schemas: [], tables: [], columnsByTable: new Map(), loaded: false };

export async function refreshSchemaCache() {
  const { ok, json } = await getJson(ENDPOINTS.schemaCache);
  if (ok && json) {
    schemaCache.schemas = json.schemas || [];
    schemaCache.tables = json.tables || [];
    schemaCache.loaded = true;
  }
  return schemaCache;
}

export async function getColumnsFor(schema, table) {
  const key = `${(schema || "").toUpperCase()}.${table.toUpperCase()}`;
  if (schemaCache.columnsByTable.has(key)) return schemaCache.columnsByTable.get(key);
  const { ok, json } = await getJson(
    `${ENDPOINTS.columns}?table=${encodeURIComponent(table)}${schema ? `&schema=${encodeURIComponent(schema)}` : ""}`
  );
  const cols = ok && json ? json.columns || [] : [];
  schemaCache.columnsByTable.set(key, cols);
  // prefetch in background for other FROM tables is done by caller
  return cols;
}

export function resolveTableForQualifier(qualifier, fromTables) {
  const q = qualifier.toUpperCase();
  const hit = fromTables.find((t) => t.alias === q || t.name === q || t.name.endsWith("." + q));
  return hit ? hit.name : null;
}

/* Prefetch columns for every table referenced in FROM/JOIN (fire-and-forget). */
export function prefetchFromColumns(sql) {
  for (const t of extractFromTables(sql)) {
    const parts = t.name.split(".");
    const tbl = parts.pop();
    const sch = parts.length ? parts.join(".") : null;
    getColumnsFor(sch, tbl).catch(() => {});
  }
}

/* -- Monaco provider -- */
export function registerCompletionProvider(monaco) {
  monaco.languages.registerCompletionItemProvider("sql", {
    triggerCharacters: [".", " ", "_"],
    provideCompletionItems: async (model, position) => {
      const line = model.getValueInRange({
        startLineNumber: position.lineNumber, startColumn: 1,
        endLineNumber: position.lineNumber, endColumn: position.column,
      });
      const word = model.getWordUntilPosition(position);
      const range = {
        startLineNumber: position.lineNumber, endLineNumber: position.lineNumber,
        startColumn: word.startColumn, endColumn: word.endColumn,
      };
      const fullSql = model.getValue();
      const ctx = detectContext(line);
      const suggestions = [];

      const kw = (label) => ({
        label, kind: monaco.languages.CompletionItemKind.Keyword,
        insertText: label, range, detail: "Netezza keyword",
      });
      const tbl = (label, detail) => ({
        label, kind: monaco.languages.CompletionItemKind.Class,
        insertText: label, range, detail: detail || "table",
      });
      const col = (label, detail) => ({
        label, kind: monaco.languages.CompletionItemKind.Field,
        insertText: label, range, detail: detail || "column",
      });

      if (ctx === "table") {
        for (const t of schemaCache.tables.slice(0, 400)) {
          const fq = t.schema ? `${t.schema}.${t.table_name}` : t.table_name;
          suggestions.push(tbl(fq, t.objtype || "TABLE"));
        }
        for (const s of schemaCache.schemas) suggestions.push(tbl(s, "schema"));
        if (!suggestions.length) NETEZZA_KEYWORDS.forEach((k) => suggestions.push(kw(k)));
        return { suggestions };
      }

      if (ctx === "column-dot") {
        const qual = dotPrefix(line);
        const fromTables = extractFromTables(fullSql);
        const resolved = resolveTableForQualifier(qual, fromTables);
        const namesToTry = resolved ? [resolved] : schemaCache.tables
          .filter((t) => t.table_name.toUpperCase() === qual.toUpperCase()
            || `${t.schema}.${t.table_name}`.toUpperCase() === qual.toUpperCase())
          .map((t) => (t.schema ? `${t.schema}.${t.table_name}` : t.table_name));
        for (const fq of namesToTry.slice(0, 3)) {
          const parts = fq.split(".");
          const cols = await getColumnsFor(parts.length > 1 ? parts.slice(0, -1).join(".") : null, parts[parts.length - 1]);
          for (const c of cols) suggestions.push(col(c.column_name, c.data_type));
        }
        return { suggestions };
      }

      if (ctx === "column") {
        const fromTables = extractFromTables(fullSql);
        prefetchFromColumns(fullSql);
        const seen = new Set();
        for (const t of fromTables.slice(0, 6)) {
          const parts = t.name.split(".");
          const cols = await getColumnsFor(parts.length > 1 ? parts.slice(0, -1).join(".") : null, parts[parts.length - 1]);
          for (const c of cols) {
            if (!seen.has(c.column_name)) {
              seen.add(c.column_name);
              suggestions.push(col(c.column_name, `${t.alias} · ${c.data_type}`));
            }
          }
        }
        NETEZZA_KEYWORDS.forEach((k) => suggestions.push(kw(k)));
        return { suggestions };
      }

      // default: keywords + snippets + schemas + top tables
      NETEZZA_KEYWORDS.forEach((k) => suggestions.push(kw(k)));
      for (const s of NETEZZA_SNIPPETS) {
        suggestions.push({
          label: s.prefix + " — " + s.label,
          kind: monaco.languages.CompletionItemKind.Snippet,
          insertText: s.insert, range, detail: "Netezza snippet",
          insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
        });
      }
      for (const s of schemaCache.schemas.slice(0, 100)) suggestions.push(tbl(s, "schema"));
      for (const t of schemaCache.tables.slice(0, 200)) {
        suggestions.push(tbl(t.schema ? `${t.schema}.${t.table_name}` : t.table_name, t.objtype || "TABLE"));
      }
      return { suggestions };
    },
  });
}
