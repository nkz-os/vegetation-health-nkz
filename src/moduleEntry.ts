/**
 * Vegetation Prime — Nekazari Platform Module
 * Uses @nekazari/module-kit for typed module definition.
 */
import { defineModule, toNKZRegistration } from '@nekazari/module-kit';
import React from 'react';
import { i18n } from '@nekazari/sdk';
import pkg from '../package.json';

import esTranslations from './i18n/locales/es.json';
import enTranslations from './i18n/locales/en.json';
import euTranslations from './i18n/locales/eu.json';
import caTranslations from './i18n/locales/ca.json';
import frTranslations from './i18n/locales/fr.json';
import ptTranslations from './i18n/locales/pt.json';

const MODULE_ID = 'vegetation-prime';

const moduleConfig = defineModule({
  id: MODULE_ID,
  displayName: 'Vegetation Prime',
  accent: { base: '#65A30D', soft: '#ECFCCB', strong: '#4D7C0F' },
  hostApiVersion: '^2.0.0',
  api: { basePath: '/api/vegetation' },
});

// Inject module translations into host i18n instance (deep merge)
if (i18n?.addResourceBundle) {
  i18n.addResourceBundle('es', 'common', esTranslations, true, true);
  i18n.addResourceBundle('en', 'common', enTranslations, true, true);
  i18n.addResourceBundle('eu', 'common', euTranslations, true, true);
  i18n.addResourceBundle('ca', 'common', caTranslations, true, true);
  i18n.addResourceBundle('fr', 'common', frTranslations, true, true);
  i18n.addResourceBundle('pt', 'common', ptTranslations, true, true);
}

if (!window.__NKZ__) {
  console.error(`[${MODULE_ID}] window.__NKZ__ not found! Module registration failed.`);
} else {
  const LazyApp = React.lazy(() => import('./App'));

  let useMobileAuth: (() => void) | undefined;
  import('./hooks/useMobileAuth').then(m => { useMobileAuth = m.useMobileAuth; }).catch(() => {});

  const MainWrapper = () => {
    if (useMobileAuth) {
      try { useMobileAuth(); } catch (_) { /* optional */ }
    }
    return React.createElement(
      React.Suspense,
      { fallback: React.createElement('div', { className: 'p-8 text-center', children: 'Loading Vegetation Prime…' }) },
      React.createElement(LazyApp)
    );
  };

  window.__NKZ__.register({ id: MODULE_ID, version: pkg.version, main: MainWrapper });

  const loadSlots = () => {
    import('./slots').then(m => {
      window.__NKZ__?.register({
        id: MODULE_ID,
        viewerSlots: m.viewerSlots,
        version: pkg.version,
        main: MainWrapper,
      });
    }).catch(err => {
      console.error(`[${MODULE_ID}] Failed to load viewerSlots:`, err);
    });
  };

  if (typeof requestAnimationFrame !== 'undefined') {
    requestAnimationFrame(() => loadSlots());
  } else {
    setTimeout(loadSlots, 0);
  }
}

// Export for dev mode
export default moduleConfig;
