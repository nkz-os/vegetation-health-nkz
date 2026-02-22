import { viewerSlots } from './slots';
import pkg from '../package.json';

const MODULE_ID = 'vegetation-prime';

if (typeof console !== 'undefined' && console.debug) {
  console.debug(`[${MODULE_ID}] init v${pkg.version}`);
}

if (window.__NKZ__) {
    window.__NKZ__.register({
        id: MODULE_ID,
        viewerSlots: viewerSlots,
        version: pkg.version,
    });
} else {
    console.error(`[${MODULE_ID}] window.__NKZ__ not found! Module registration failed.`);
}
