/**
 * i18n Configuration for Vegetation Prime Module
 * 
 * Uses react-i18next for internationalization.
 * Supports: es (Spanish), en (English), eu (Euskera)
 * 
 * The module detects language from:
 * 1. Host context (__nekazariLocale)
 * 2. Browser navigator.language
 * 3. Defaults to 'es'
 */

import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

// Import translation files
import es from './locales/es.json';
import en from './locales/en.json';
import eu from './locales/eu.json';

// Detect language from host or browser
const detectLanguage = (): string => {
    // Try host-provided locale first
    if (typeof window !== 'undefined') {
        const hostLocale = (window as any).__nekazariLocale;
        if (hostLocale && ['es', 'en', 'eu'].includes(hostLocale)) {
            return hostLocale;
        }
    }

    // Fallback to browser language
    if (typeof navigator !== 'undefined') {
        const browserLang = navigator.language.split('-')[0];
        if (['es', 'en', 'eu'].includes(browserLang)) {
            return browserLang;
        }
    }

    // Default to Spanish
    return 'es';
};

i18n
    .use(initReactI18next)
    .init({
        resources: {
            es: { translation: es },
            en: { translation: en },
            eu: { translation: eu },
        },
        lng: detectLanguage(),
        fallbackLng: 'es',
        interpolation: {
            escapeValue: false, // React already escapes
        },
        react: {
            useSuspense: false, // Avoid suspense issues with Module Federation
        },
    });

export default i18n;

// Re-export useTranslation for convenience
export { useTranslation } from 'react-i18next';
