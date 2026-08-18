import type { Metadata } from 'next'
import './globals.css'
import ThemeScript from '@/components/ThemeScript'
import ToastViewport from '@/components/common/ToastViewport'
import { AppShellProvider } from '@/context/AppShellContext'
import { I18nClientBridge } from '@/i18n/I18nClientBridge'

// Fonts (Inter / Lora / JetBrains Mono) are loaded at runtime from a Google
// Fonts mirror reachable in mainland China. Each CSS variable falls back to
// the system stacks defined in globals.css, so the UI still renders when the
// CDN is unreachable — though a fully air-gapped deployment would lose the
// custom typefaces.
const FONT_CDN = 'https://fonts.productmml.top'
const FONT_HREF =
  `${FONT_CDN}/css2?family=Inter:wght@400;500;600&family=Lora:ital,wght@0,400;0,500;0,600;1,400;1,500&family=JetBrains+Mono:wght@400;500&display=swap`

const APP_BASE_PATH = (process.env.NEXT_PUBLIC_BASE_PATH || '').replace(/\/$/, '')
const FAVICON_VERSION = 'traittutor-icon-v3-transparent'
const iconUrl = (path: string) => `${APP_BASE_PATH}${path}?v=${FAVICON_VERSION}`

export const metadata: Metadata = {
  title: 'TraitTutor',
  description: 'Agent-native intelligent learning companion',
  icons: {
    icon: [
      { url: iconUrl('/favicon.svg'), type: 'image/svg+xml' },
      { url: iconUrl('/favicon-32x32.png'), sizes: '32x32', type: 'image/png' },
      { url: iconUrl('/favicon-16x16.png'), sizes: '16x16', type: 'image/png' },
    ],
    apple: iconUrl('/apple-touch-icon.png'),
  },
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html
      lang="zh"
      suppressHydrationWarning
      data-scroll-behavior="smooth"
    >
      <head>
        <link rel="preconnect" href={FONT_CDN} crossOrigin="anonymous" />
        <link rel="stylesheet" href={FONT_HREF} />
        <ThemeScript />
      </head>
      <body
        className="font-sans bg-[var(--background)] text-[var(--foreground)]"
        suppressHydrationWarning
      >
        <AppShellProvider>
          <I18nClientBridge>{children}</I18nClientBridge>
          <ToastViewport />
        </AppShellProvider>
      </body>
    </html>
  )
}
