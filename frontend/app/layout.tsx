import type { Metadata } from "next";

import { AppFooter, AppHeader } from "@/components/AppHeader";

import "./globals.css";

// Plus Jakarta Sans is self-hosted through @fontsource (see globals.css), so builds and demos
// work offline.

export const metadata: Metadata = {
  title: "NafaPilot",
  description: "Profit-aware merchant growth decision engine (synthetic-data prototype)",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="flex min-h-full flex-col">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-3 focus:py-2"
        >
          Skip to content
        </a>
        <AppHeader />
        <main id="main" className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 pb-12 pt-6 sm:px-5">
          {children}
        </main>
        <AppFooter />
      </body>
    </html>
  );
}
