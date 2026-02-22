import { defineConfig } from 'vite';
import { nkzModulePreset } from '@nekazari/module-builder';
import path from 'path';

export default defineConfig(nkzModulePreset({
  moduleId: 'vegetation-prime',
  entry: 'src/moduleEntry.ts',

  viteConfig: {
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      port: 5003,
      proxy: {
        '/api': {
          target: process.env.VITE_DEV_API_TARGET || 'http://localhost:8000',
          changeOrigin: true,
          secure: true,
        },
      },
    }
  }
}));
