import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const frontendRoot = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  base: '/static/',
  resolve: { alias: { '@justybase/spreadsheet-tasks/browser/browser-spreadsheet.js': path.resolve(frontendRoot, 'node_modules/@justybase/spreadsheet-tasks/browser/browser-spreadsheet.js') } },
  build: { outDir: '../static/dist', emptyOutDir: true },
  server: { proxy: { '/api': 'http://127.0.0.1:8480', '/api/v1/workspace': { target: 'ws://127.0.0.1:8480', ws: true } } },
});
