import { defineModule, withModuleProvider } from '@nekazari/module-kit';
import React, { lazy, Suspense } from 'react';
import './i18n';
import { vegetationPrimeSlots } from './slots';
import { VegetationProvider } from './services/vegetationContext';
import pkg from '../package.json';

const LazyApp = lazy(() => import('./App'));

const MainWrapper: React.FC = () => (
  <VegetationProvider>
    <Suspense fallback={<div className="p-8 text-center">Loading Vegetation Prime…</div>}>
      <LazyApp />
    </Suspense>
  </VegetationProvider>
);

export default defineModule({
  id: 'vegetation-prime',
  displayName: 'Vegetation Prime',
  version: pkg.version,
  hostApiVersion: '^2.0.0',
  description: 'High-performance vegetation intelligence suite — Nekazari Platform Module',
  accent: { base: '#65A30D', soft: '#ECFCCB', strong: '#4D7C0F' },
  icon: 'leaf',
  main: MainWrapper,
  api: { basePath: '/api/vegetation' },
  slots: withModuleProvider(vegetationPrimeSlots) as never,
});
