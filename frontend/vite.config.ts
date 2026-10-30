import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// The dev server proxies /api to the backend so browser code can use
// relative URLs (works behind the sandbox preview proxy too).
//
// Native development: the backend runs on 127.0.0.1:8000 (the default).
// Docker development: set VITE_API_PROXY_TARGET=http://backend:8000
//   (or pass it via the docker-compose environment).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  const apiProxyTarget = env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000';

  return {
    plugins: [react()],
    server: {
      host: true,
      port: 5173,
      // The sandbox previews the app through a proxy host; allow all hosts in
      // dev (the production build is served by a reverse proxy).
      allowedHosts: true,
      proxy: {
        '/api': {
          target: apiProxyTarget,
          changeOrigin: false,
        },
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./tests/setup.ts'],
    },
  };
});