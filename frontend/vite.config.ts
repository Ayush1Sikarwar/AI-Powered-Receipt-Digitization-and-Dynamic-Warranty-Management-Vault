import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The dev server proxies /api to the backend so browser code can use
// relative URLs (works behind the sandbox preview proxy too).
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    // The sandbox previews the app through a proxy host; allow all hosts in
    // dev (the production build is served by a reverse proxy).
    allowedHosts: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
  },
});
