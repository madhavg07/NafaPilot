// Shared UI primitives. Cards on a light canvas, with one visual marker per kind of number:
//   prediction  → amber   (forecast from the profit engine)
//   simulated   → violet, hatched edge (synthetic outcomes from the simulator)
//   estimate    → blue    (treatment-vs-holdout statistic)
//   observed    → slate   (recorded synthetic history)
//   projection  → dotted slate (latest rate carried forward; not observed)

"use client";

import { AlertTriangle, CircleHelp, Inbox, RotateCw } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import type { ApiError } from "@/lib/api";
import { explainError } from "@/lib/messages";

export type Kind = "prediction" | "simulated" | "estimate" | "observed" | "projection";

const KIND_STYLE: Record<Kind, { edge: string; text: string; bg: string; label: string }> = {
  prediction: { edge: "border-l-4 border-pred", text: "text-pred", bg: "bg-pred-wash", label: "Prediction" },
  simulated: { edge: "border-l-4 border-sim kind-hatch", text: "text-sim", bg: "bg-sim-wash", label: "Simulated" },
  estimate: { edge: "border-l-4 border-est", text: "text-est", bg: "bg-est-wash", label: "Estimate" },
  observed: { edge: "border-l-4 border-rule-strong", text: "text-ink-2", bg: "bg-surface-2", label: "Observed (synthetic)" },
  projection: { edge: "border-l-4 border-dotted border-ink-3", text: "text-ink-2", bg: "bg-surface-2", label: "Projection" },
};

export function KindTag({ kind, children }: { kind: Kind; children?: ReactNode }) {
  const s = KIND_STYLE[kind];
  return <span className={`chip ${s.bg} ${s.text}`}>{children ?? s.label}</span>;
}

/** A block of numbers of one kind, marked by its coloured left edge and a tag. */
export function KindBlock({
  kind,
  title,
  tag,
  children,
  testId,
}: {
  kind: Kind;
  title: string;
  tag?: string;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <section
      data-kind={kind}
      data-testid={testId}
      aria-label={title}
      className={`min-w-0 rounded-xl border border-rule bg-surface p-4 ${KIND_STYLE[kind].edge}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <h3 className="font-bold">{title}</h3>
        <KindTag kind={kind}>{tag}</KindTag>
      </div>
      <div className="mt-3">{children}</div>
    </section>
  );
}

/** A page section as a card, with an optional icon, a one-line explanation and an action. */
export function Panel({
  title,
  note,
  action,
  children,
  id,
  icon,
  accent = "brand",
}: {
  title: string;
  note?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  id?: string;
  icon?: ReactNode;
  accent?: "brand" | "pred" | "sim" | "est" | "loss";
}) {
  const ACCENT = {
    brand: "bg-brand-wash text-brand",
    pred: "bg-pred-wash text-pred",
    sim: "bg-sim-wash text-sim",
    est: "bg-est-wash text-est",
    loss: "bg-loss-wash text-loss",
  } as const;
  return (
    <section
      id={id}
      aria-labelledby={id ? `${id}-title` : undefined}
      className="card animate-rise min-w-0 scroll-mt-24 p-5 sm:p-6"
    >
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          {icon && (
            <span aria-hidden="true" className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl ${ACCENT[accent]}`}>
              {icon}
            </span>
          )}
          <div className="min-w-0">
            <h2 id={id ? `${id}-title` : undefined} className="text-lg font-bold tracking-tight">
              {title}
            </h2>
            {note && <p className="mt-0.5 max-w-[70ch] text-sm leading-snug text-ink-2">{note}</p>}
          </div>
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </div>
      <div className="min-w-0">{children}</div>
    </section>
  );
}

export function Loading({ what }: { what: string }) {
  return (
    <div role="status" aria-live="polite" className="flex flex-col gap-2 py-1">
      <span className="sr-only">Loading {what}…</span>
      <p aria-hidden="true" className="text-sm text-ink-3">
        Loading {what}…
      </p>
      <div aria-hidden="true" className="h-3 w-2/3 animate-pulse rounded-full bg-rule" />
      <div aria-hidden="true" className="h-3 w-1/2 animate-pulse rounded-full bg-rule" />
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-dashed border-rule-strong bg-surface-2 px-4 py-3 text-sm text-ink-2">
      <Inbox aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-ink-3" />
      <div>{children}</div>
    </div>
  );
}

/** A backend error: plain-language meaning, plus the raw code/message/details for debugging. */
export function ErrorBox({
  error,
  onRetry,
  retryLabel = "Try again",
}: {
  error: ApiError;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  const ex = explainError(error);
  return (
    <div
      role="alert"
      data-testid="api-error"
      data-error-code={error.code}
      className="flex gap-3 rounded-xl border border-loss/30 bg-loss-wash px-4 py-3 text-sm"
    >
      <AlertTriangle aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 text-loss" />
      <div className="min-w-0 flex-1">
        <p className="font-bold text-loss">{ex.title}</p>
        <p className="mt-1 text-ink">{ex.action}</p>
        <p className="mt-1 text-ink-2">{error.message}</p>
        <details className="mt-2 text-ink-2">
          <summary className="cursor-pointer text-[13px]">Technical details</summary>
          <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 font-mono text-xs">
            <dt>code</dt>
            <dd>{error.code}</dd>
            <dt>HTTP</dt>
            <dd>{error.status || "no response"}</dd>
            {error.details && (
              <>
                <dt>details</dt>
                <dd className="break-all">{JSON.stringify(error.details)}</dd>
              </>
            )}
          </dl>
        </details>
        {onRetry && (
          <button type="button" onClick={onRetry} className="btn-secondary mt-3">
            <RotateCw aria-hidden="true" className="h-4 w-4" />
            {retryLabel}
          </button>
        )}
      </div>
    </div>
  );
}

/** A labelled figure. `size` lg is for the few numbers a page is about. */
export function Stat({
  label,
  value,
  sub,
  size = "md",
  tone,
  help,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  size?: "md" | "lg";
  tone?: "gain" | "loss";
  help?: ReactNode;
}) {
  const color = tone === "gain" ? "text-gain" : tone === "loss" ? "text-loss" : "";
  return (
    <div className="min-w-0">
      <dt className="label flex items-center gap-1">
        {label}
        {help && <HelpTip label={label}>{help}</HelpTip>}
      </dt>
      <dd className={`figure ${size === "lg" ? "text-[26px] leading-tight" : "text-lg"} ${color}`}>{value}</dd>
      {sub && <dd className="text-[13px] leading-snug text-ink-3">{sub}</dd>}
    </div>
  );
}

const TONE = {
  green: "bg-gain-wash text-gain",
  red: "bg-loss-wash text-loss",
  amber: "bg-pred-wash text-pred",
  zinc: "bg-surface-2 text-ink-2 border border-rule",
  violet: "bg-sim-wash text-sim",
  blue: "bg-est-wash text-est",
} as const;
const DOT = {
  green: "bg-gain",
  red: "bg-loss",
  amber: "bg-pred",
  zinc: "bg-ink-3",
  violet: "bg-sim",
  blue: "bg-est",
} as const;

/** Status as a coloured pill with a dot (the text carries the meaning, not only colour). */
export function Badge({ tone, children }: { tone: keyof typeof TONE; children: ReactNode }) {
  return (
    <span className={`chip ${TONE[tone]}`}>
      <span aria-hidden="true" className={`inline-block h-1.5 w-1.5 rounded-full ${DOT[tone]}`} />
      {children}
    </span>
  );
}

export function SyntheticNotice({ children }: { children: ReactNode }) {
  return <p className="rounded-xl bg-surface-2 px-4 py-3 text-[13px] text-ink-3">{children}</p>;
}

/**
 * A small "?" button that shows a plain-language explanation. Opens on click or keyboard,
 * closes on Escape or a click elsewhere.
 */
export function HelpTip({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <span ref={ref} className="relative inline-flex align-middle">
      <button
        type="button"
        aria-label={`What is "${label}"?`}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((o) => !o)}
        className="grid h-5 w-5 place-items-center rounded-full text-ink-3 transition hover:bg-brand-wash hover:text-brand"
      >
        <CircleHelp aria-hidden="true" className="h-3.5 w-3.5" />
      </button>
      {open && (
        <span
          id={id}
          role="tooltip"
          className="animate-rise absolute left-1/2 top-6 z-30 w-64 -translate-x-1/2 rounded-xl border border-rule bg-surface p-3 text-left text-[13px] font-normal leading-snug text-ink-2 shadow-lift"
        >
          {children}
        </span>
      )}
    </span>
  );
}

/** A short-lived confirmation message, announced to screen readers. */
export function Toast({ message, onDone }: { message: string | null; onDone: () => void }) {
  useEffect(() => {
    if (!message) return;
    const t = setTimeout(onDone, 3500);
    return () => clearTimeout(t);
  }, [message, onDone]);
  return (
    <div aria-live="polite" className="pointer-events-none fixed inset-x-0 bottom-5 z-50 flex justify-center px-4">
      {message && (
        <div className="animate-rise pointer-events-auto rounded-xl bg-ink px-4 py-2.5 text-sm font-semibold text-paper shadow-lift">
          {message}
        </div>
      )}
    </div>
  );
}
