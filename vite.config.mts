import {defineConfig} from 'vite';
import {resolve} from 'node:path';

export default defineConfig({
  root: resolve(import.meta.dirname, 'frontend'),
  publicDir: 'public',
  build: {
    target: 'es2022', cssCodeSplit: false, assetsInlineLimit: 0, sourcemap: false,
    rollupOptions: {
      input: {reader: resolve(import.meta.dirname,'frontend/index.html'), transport: resolve(import.meta.dirname,'frontend/src/api/transport.ts')},
      preserveEntrySignatures: 'strict',
      output: {entryFileNames: 'assets/[name]-[hash].js'},
    },
  },
});
