/// <reference types="vitest" />
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const projectRoot = path.dirname(fileURLToPath(import.meta.url));

/** Strip crossorigin from module script/link tags for Electron file:// compatibility */
function removeCrossOrigin(): import('vite').Plugin {
  return {
    name: 'remove-crossorigin',
    transformIndexHtml(html) {
      return html.replace(/\s+crossorigin(=("[^"]*"|'[^']*'|[^"'\s>]+))?/gi, '');
    },
  };
}

export default defineConfig({
  root: 'src/renderer',
  publicDir: path.resolve(projectRoot, 'assets'),
  base: './',
  plugins: [react(), removeCrossOrigin()],
  resolve: {
    alias: {
      '@': path.resolve(projectRoot, 'src/renderer'),
    },
  },
  build: {
    outDir: path.resolve(projectRoot, 'dist/renderer'),
    emptyOutDir: true,
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      output: {
        manualChunks: {
          'react-vendor': ['react', 'react-dom'],
        },
      },
    },
  },
  server: {
    port: 5200,
    strictPort: true,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: [path.resolve(projectRoot, 'src/renderer/test/setup.ts')],
    include: ['test/**/*.{test,spec}.{ts,tsx}'],
    css: true,
  },
});
