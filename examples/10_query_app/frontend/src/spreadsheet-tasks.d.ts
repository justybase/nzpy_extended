declare module '@justybase/spreadsheet-tasks/browser/browser-spreadsheet.js' {
  export function downloadXlsx(fileName: string, rows: unknown[][], headers: string[], sheetName?: string): void;
  export function downloadXlsb(fileName: string, rows: unknown[][], headers: string[], sheetName?: string): void;
}
