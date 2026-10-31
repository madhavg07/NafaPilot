import { ChevronRight, FlaskConical, Sparkles } from "lucide-react";
import Link from "next/link";

export function Logo() {
  return (
    <span className="flex items-center gap-2.5">
      <span
        aria-hidden="true"
        className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-teal-500 to-emerald-600 text-white shadow-sm"
      >
        <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
          <path d="M4 17l5-5 4 4 7-8" />
          <path d="M15 8h5v5" />
        </svg>
      </span>
      <span className="leading-tight">
        <span className="block text-[17px] font-extrabold tracking-tight">
          Nafa<span className="text-brand">Pilot</span>
        </span>
        <span className="block text-[11px] font-medium text-ink-3">Grow sales, keep profit</span>
      </span>
    </span>
  );
}

export function AppHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-rule bg-surface/85 backdrop-blur">
      <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-x-6 gap-y-2 px-4 py-3 sm:px-5">
        <Link href="/" aria-label="NafaPilot home">
          <Logo />
        </Link>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="chip bg-brand-wash text-brand">
            <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
            AI suggests, code checks
          </span>
          <span className="chip bg-sim-wash text-sim">
            <FlaskConical aria-hidden="true" className="h-3.5 w-3.5" />
            Demo data · simulation only
          </span>
        </div>
      </div>
    </header>
  );
}

export function AppFooter() {
  return (
    <footer className="mx-auto w-full max-w-6xl px-4 pb-8 text-[13px] text-ink-3 sm:px-5">
      <p className="border-t border-rule pt-5">
        A prototype by Team PayMatrix for the Paytm Build for India AI Hackathon 2026. It is not an
        official Paytm product. Every merchant, order and outcome shown here is synthetic, and
        nothing is sent to customers or to any payment system.
      </p>
    </footer>
  );
}

export function Breadcrumbs({ items }: { items: { href?: string; label: string }[] }) {
  return (
    <nav aria-label="Breadcrumb" className="text-[13px] text-ink-3">
      <ol className="flex flex-wrap items-center gap-1">
        {items.map((it, i) => (
          <li key={i} className="flex items-center gap-1">
            {i > 0 && <ChevronRight aria-hidden="true" className="h-3.5 w-3.5" />}
            {it.href ? (
              <Link href={it.href} className="font-medium hover:text-brand">
                {it.label}
              </Link>
            ) : (
              <span aria-current="page" className="font-medium text-ink-2">
                {it.label}
              </span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}
