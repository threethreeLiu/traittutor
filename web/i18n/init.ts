import i18n, { type Resource } from 'i18next'
import { initReactI18next } from 'react-i18next'

import enApp from '@/locales/en/app.json'
import zhApp from '@/locales/zh/app.json'

export type AppLanguage = 'en' | 'zh'

export function normalizeLanguage(lang: unknown): AppLanguage {
  // No explicit preference → product default (zh), matching
  // app-shell-storage's readStoredLanguage and the AppShell initial state.
  if (!lang) return 'zh'
  const s = String(lang).toLowerCase()
  if (s === 'en' || s === 'english') return 'en'
  return 'zh'
}

let _initialized = false

export function initI18n(language?: unknown) {
  if (_initialized) return i18n

  const resources: Resource = {
    en: { app: enApp },
    zh: { app: zhApp },
  }

  i18n.use(initReactI18next).init({
    resources,
    lng: normalizeLanguage(language),
    fallbackLng: 'zh',
    // Initialize synchronously. The default `initImmediate: true` defers init
    // to a setTimeout, so SSR and the first hydration render race the init:
    // whichever side renders while uninitialized falls back to raw keys, and
    // the post-mount language switch can land between the two renders —
    // producing "server rendered text didn't match the client" errors.
    initImmediate: false,
    // Use a single default namespace to keep lookups simple.
    // We intentionally keep keySeparator disabled so keys like "Generating..." remain valid.
    defaultNS: 'app',
    ns: ['app'],
    keySeparator: false,
    interpolation: {
      escapeValue: false,
    },
    returnEmptyString: false,
    returnNull: false,
  })

  _initialized = true
  return i18n
}

export async function ensureLanguage(language: AppLanguage) {
  if (i18n.hasResourceBundle(language, 'app')) return
  const bundle = language === 'zh' ? zhApp : enApp
  i18n.addResourceBundle(language, 'app', bundle, true, true)
}
