/**
 * Register immediately with id + lazy main so the host finds the module even if
 * slot imports fail. Then load slots async (heavy deps) and re-register with viewerSlots.
 */
import React from 'react';
import pkg from '../package.json';

const MODULE_ID = 'vegetation-prime';

if (typeof console !== 'undefined' && console.debug) {
  console.debug(`[${MODULE_ID}] init v${pkg.version}`);
}

if (!window.__NKZ__) {
  console.error(`[${MODULE_ID}] window.__NKZ__ not found! Module registration failed.`);
} else {
  const LazyApp = React.lazy(() => import('./App'));

  const MainWrapper = () =>
    React.createElement(
      React.Suspense,
      {
        fallback: React.createElement('div', {
          className: 'p-8 text-center',
          children: 'Loading Vegetation Prime…',
        }),
      },
      React.createElement(LazyApp)
    );

  window.__NKZ__.register({
    id: MODULE_ID,
    version: pkg.version,
    main: MainWrapper,
  });

  const loadSlots = () => {
    import('./slots')
      .then((m) => {
        window.__NKZ__?.register({
          id: MODULE_ID,
          viewerSlots: m.viewerSlots,
          version: pkg.version,
          main: MainWrapper,
        });
        if (typeof console !== 'undefined' && console.debug) {
          console.debug(`[${MODULE_ID}] viewerSlots registered`);
        }
      })
      .catch((err) => {
        console.error(`[${MODULE_ID}] Failed to load viewerSlots:`, err);
      });
  };

  if (typeof requestAnimationFrame !== 'undefined') {
    requestAnimationFrame(() => loadSlots());
  } else {
    setTimeout(loadSlots, 0);
  }
}
