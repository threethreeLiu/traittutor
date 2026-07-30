import type { Metadata } from "next";
import { Geist, Lora } from "next/font/google";
import "./globals.css";
import ThemeScript from "@/components/ThemeScript";
import ToastViewport from "@/components/common/ToastViewport";
import { AppShellProvider } from "@/context/AppShellContext";
import { I18nClientBridge } from "@/i18n/I18nClientBridge";

// Geist matches the public site (traittutor.info) and stays crisp at the
// small UI sizes the composer/toolbars use, unlike the rounder Jakarta.
const fontSans = Geist({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-sans",
});

const fontSerif = Lora({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-serif",
});

const APP_BASE_PATH = (process.env.NEXT_PUBLIC_BASE_PATH || "").replace(
  /\/$/,
  "",
);
const FAVICON_VERSION = "traittutor-icon-v2";
const iconUrl = (path: string) => `${APP_BASE_PATH}${path}?v=${FAVICON_VERSION}`;

export const metadata: Metadata = {
  title: "TraitTutor",
  description: "Agent-native intelligent learning companion",
  icons: {
    icon: [
      { url: iconUrl("/favicon.svg"), type: "image/svg+xml" },
      { url: iconUrl("/favicon-32x32.png"), sizes: "32x32", type: "image/png" },
      { url: iconUrl("/favicon-16x16.png"), sizes: "16x16", type: "image/png" },
    ],
    apple: iconUrl("/apple-touch-icon.png"),
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      data-scroll-behavior="smooth"
      className={`${fontSans.variable} ${fontSerif.variable}`}
    >
      <head>
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
  );
}
