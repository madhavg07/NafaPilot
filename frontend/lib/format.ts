// Display formatting only. No arithmetic that changes a backend result.

const inrFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 0 });
const inrFmt2 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
const numFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });

/** ₹ amount. `null`/`undefined` renders as an em dash, never as ₹0. */
export function inr(v: number | string | null | undefined, exact = false): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "string" ? Number(v) : v;
  if (!Number.isFinite(n)) return String(v);
  // Whole rupees print without decimals; anything else prints paise (never a lone ".7").
  const s = (exact || !Number.isInteger(n) ? inrFmt2 : inrFmt).format(Math.abs(n));
  return n < 0 ? `−₹${s}` : `₹${s}`;
}

/** Signed ₹ (+₹100 / −₹3,600). */
export function inrSigned(v: number | string | null | undefined, exact = false): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "string" ? Number(v) : v;
  return n > 0 ? `+${inr(n, exact)}` : inr(n, exact);
}

export function num(v: number | string | null | undefined, digits?: number): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "string" ? Number(v) : v;
  if (!Number.isFinite(n)) return String(v);
  if (digits !== undefined) {
    const s = new Intl.NumberFormat("en-IN", {
      maximumFractionDigits: digits,
      minimumFractionDigits: digits,
    }).format(Math.abs(n));
    return n < 0 ? `−${s}` : s;
  }
  const s = numFmt.format(Math.abs(n));
  return n < 0 ? `−${s}` : s;
}

export function signed(v: number | null | undefined, digits?: number): string {
  if (v === null || v === undefined) return "—";
  return v > 0 ? `+${num(v, digits)}` : num(v, digits);
}

export function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "—";
  return `${num(v * 100, digits)}%`;
}

export function signedPct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined) return "—";
  const s = `${num(Math.abs(v), digits)}%`;
  return v > 0 ? `+${s}` : v < 0 ? `−${s}` : s;
}

const dateFmt = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric" });
const shortDateFmt = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short" });
const dtFmt = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

function parseDate(v: string): Date {
  // Plain dates (YYYY-MM-DD) are calendar dates; parse them as local noon to avoid TZ shifts.
  return /^\d{4}-\d{2}-\d{2}$/.test(v) ? new Date(`${v}T12:00:00`) : new Date(v);
}

export const date = (v: string | null | undefined) => (v ? dateFmt.format(parseDate(v)) : "—");
export const shortDate = (v: string | null | undefined) =>
  v ? shortDateFmt.format(parseDate(v)) : "—";
export const dateTime = (v: string | null | undefined) => (v ? dtFmt.format(parseDate(v)) : "—");

export const humanize = (s: string) => s.replaceAll("_", " ");

export const CAMPAIGN_TYPE_LABEL: Record<string, string> = {
  targeted_offer: "Targeted offer",
  off_peak: "Off-peak offer",
  new_customer: "New-customer offer",
  no_action: "No action",
  product_offer: "Product offer",
  bundle_offer: "Bundle offer",
  cart_offer: "Cart offer",
};

export const typeLabel = (t: string) => CAMPAIGN_TYPE_LABEL[t] ?? humanize(t);

export const TARGET_LABEL: Record<string, string> = {
  eligible_existing_customers: "Existing customers",
  new_customers: "New customers",
  all_walk_ins: "All walk-ins",
  none: "Nobody",
  all_customers: "All customers",
};

/** Split a backend reason string such as "small_sample: fewer than 30 …" into parts. */
export function splitReason(r: string): { code: string; text: string } {
  const i = r.indexOf(":");
  if (i > 0 && /^[a-z_]+$/.test(r.slice(0, i))) {
    return { code: r.slice(0, i), text: r.slice(i + 1).trim() };
  }
  return { code: "", text: r };
}

const SOURCE_LABEL: Record<string, string> = {
  llm_suggestion: "an AI suggestion (not verified)",
  merchant_edit: "the merchant's edit",
  template: "a template",
  illustrative: "illustrative values",
  measured: "measured data",
};
export const sourceLabel = (s: string) => SOURCE_LABEL[s] ?? humanize(s);
export const capitalize = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);
