export type Column = Record<string, unknown>;

export type ResultSet = {
  id: string;
  sessionId: string;
  queryId: string;
  statementIndex: number;
  label: string;
  status: 'running' | 'complete' | 'error' | 'cancelled';
  columns: Column[];
  totalRows: number;
  truncated?: boolean;
  message?: string;
  createdAt: string;
};

export type SqlTab = {
  id: string;
  title: string;
  sql: string;
  database?: string;
  schema?: string;
  dirty: boolean;
  results: ResultSet[];
  activeResultId?: string;
  runningQueryId?: string;
};

export type SchemaNode = {
  id: string;
  kind: string;
  label: string;
  database?: string;
  schema?: string;
  object_name?: string;
  column_name?: string;
  object_type?: string;
  column_type?: string;
  has_children: boolean;
  description?: string;
};

export type PageResponse = {
  session_id: string;
  columns: Column[];
  rows: unknown[][];
  offset: number;
  limit: number;
  total_rows: number;
  has_more: boolean;
  truncated?: boolean;
  message?: string;
};
