import { i18n } from '@nekazari/sdk';
import es from './i18n/locales/es.json';
import en from './i18n/locales/en.json';
import eu from './i18n/locales/eu.json';
import ca from './i18n/locales/ca.json';
import fr from './i18n/locales/fr.json';
import pt from './i18n/locales/pt.json';

const NS = 'common';

function register(): void {
  const add = i18n && 'addResourceBundle' in i18n ? i18n.addResourceBundle : undefined;
  if (typeof add !== 'function') return;
  for (const [lang, res] of Object.entries({ es, en, eu, ca, fr, pt })) {
    add.call(i18n, lang, NS, res, true, true);
  }
}

register();
