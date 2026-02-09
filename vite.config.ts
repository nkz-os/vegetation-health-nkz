import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import federation from '@originjs/vite-plugin-federation';
import path from 'path';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    react(),
    federation({
      name: 'vegetation_prime_module',
      filename: 'remoteEntry.js',
      exposes: {
        // Main App Entry
        './App': './src/App.tsx',
        // Slots Registration (used by Host to discover slots)
        './viewerSlots': './src/slots/index.tsx',
        // Individual Slot Components (must match manifest.json slot definitions)
        './VegetationLayer': './src/components/slots/VegetationLayer.tsx',
        './TimelineWidget': './src/exports/TimelineWidget.tsx',
        './VegetationLayerControl': './src/components/slots/VegetationLayerControl.tsx',
        './VegetationConfig': './src/exports/VegetationConfig.tsx',
        './VegetationAnalytics': './src/exports/VegetationAnalytics.tsx',
      },
      shared: {
        // CRITICAL: React MUST be shared as singleton to avoid hook errors
        'react': {
          singleton: true,
          requiredVersion: '^18.3.1',
          import: false, // Use Global window.React
        },
        'react-dom': {
          singleton: true,
          requiredVersion: '^18.3.1',
          import: false, // Use Global window.ReactDOM (now patched in Host)
        },
        'react-router-dom': {
          singleton: true,
          requiredVersion: '^6.26.0',
          import: false, // Use Global window.ReactRouterDOM
        },
        // Shared with host for consistency
        '@nekazari/ui-kit': {
          singleton: true,
          requiredVersion: '^1.0.0',
          import: false,
          shareScope: 'default',
        },
        '@nekazari/sdk': {
          singleton: false,
          requiredVersion: '^1.0.0',
        },
      },
    }),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5003,
    cors: true,
    // Proxy API calls to avoid CORS issues in development
    proxy: {
      '/api': {
        target: 'https://nkz.artotxiki.com',
        changeOrigin: true,
        secure: true,
      },
    },
  },
  build: {
    target: 'esnext',
    minify: false,
    cssCodeSplit: false,
    // React must be shared via Module Federation (singleton) to work correctly
    // when module renders inside host's React tree
    rollupOptions: {
      // Externalize React dependencies to use Globals provided by Host
      external: [
        'react',
        'react-dom',
        'react-router-dom',
      ],
      output: {
        globals: {
          'react': 'React',
          'react-dom': 'ReactDOM',
          'react-router-dom': 'ReactRouterDOM',
        },
        format: 'es',
      },
    },
    // Ensure @nekazari/sdk is resolved correctly
    commonjsOptions: {
      include: [/node_modules/],
      transformMixedEsModules: true,
    },
  },
});
