import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// Dev: Vite serves the UI on :5173 and proxies /api to the Python server.
// Prod: `npm run build` writes dist/, which the Python server hosts at /.
export default defineConfig({
  plugins: [svelte()],
  server: { proxy: { '/api': 'http://127.0.0.1:8765' } },
  build: { outDir: 'dist', emptyOutDir: true },
});
