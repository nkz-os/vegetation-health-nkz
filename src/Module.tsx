import { defineModule } from '@nekazari/module-kit';
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

// Wrap every slot widget with VegetationProvider so each federated widget mount
// gets its own provider tree (loadRemote() returns isolated React trees).
// Strip the moduleProvider key — schema only allows slot-type keys whose values
// are arrays.
const { moduleProvider: _moduleProvider, ...rawSlots } = vegetationPrimeSlots as Record<string, unknown>;
const wrappedSlots = Object.fromEntries(
  Object.entries(rawSlots).map(([slot, entries]) => [
    slot,
    (entries as Array<Record<string, any>>).map((entry) => {
      const Inner = entry.localComponent as React.ComponentType<any> | undefined;
      if (!Inner) return entry;
      const Wrapped: React.FC<any> = (props) => (
        <VegetationProvider>
          <Inner {...props} />
        </VegetationProvider>
      );
      return { ...entry, localComponent: Wrapped };
    }),
  ]),
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
  slots: wrappedSlots as never,
});
