import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: '/static/',
  build: { outDir: '../static/dist', emptyOutDir: true },
  server: { proxy: { '/api': 'http://127.0.0.1:8480', '/api/v1/workspace': { target: 'ws://127.0.0.1:8480', ws: true } } },
});
