"use client";

import { Languages, Layers, Loader2, Package, ShoppingCart, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState, type ReactNode } from "react";

import {
  ApiError,
  generateOffers,
  OFFER_COST_TYPE,
  PROMOTION_KINDS,
  type Language,
  type PromotionKind,
  type SettingsOut,
} from "@/lib/api";
import { toApiError } from "@/lib/useApi";

import { ErrorBox, HelpTip } from "../ui";

export const KIND_TEXT: Record<PromotionKind, { title: string; hint: string; example: string; icon: ReactNode }> = {
  product: {
    title: "Product offer",
    hint: "₹ off one product",
    example: "e.g. ₹20 off a cappuccino, 2–5 pm",
    icon: <Package className="h-5 w-5" />,
  },
  bundle: {
    title: "Bundle offer",
    hint: "Combo price for items bought together",
    example: "e.g. cappuccino + sandwich for ₹25 less",
    icon: <Layers className="h-5 w-5" />,
  },
  cart: {
    title: "Cart offer",
    hint: "₹ off when the order is big enough",
    example: "e.g. ₹30 off on ₹300 or more",
    icon: <ShoppingCart className="h-5 w-5" />,
  },
};

export function OfferPlanner({
  merchantId,
  settings,
  selectedCount,
}: {
  merchantId: string;
  settings: SettingsOut | null;
  selectedCount: number;
}) {
  const router = useRouter();
  const [kinds, setKinds] = useState<Set<PromotionKind>>(new Set(PROMOTION_KINDS));
  const [costs, setCosts] = useState<Record<PromotionKind, string>>(() => {
    const v = (k: PromotionKind) => {
      const x = settings?.campaign_costs_inr[OFFER_COST_TYPE[k]];
      return x === null || x === undefined ? "" : String(x);
    };
    return { product: v("product"), bundle: v("bundle"), cart: v("cart") };
  });
  const [lang, setLang] = useState<Language>("en");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const inFlight = useRef(false);
  const invalid = PROMOTION_KINDS.filter((k) => costs[k] !== "" && !/^\d+(\.\d{1,2})?$/.test(costs[k]));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (inFlight.current || invalid.length || kinds.size === 0 || selectedCount === 0) return;
    inFlight.current = true;
    setPending(true);
    setError(null);
    try {
      const chosen = PROMOTION_KINDS.filter((k) => kinds.has(k));
      const run = await generateOffers(merchantId, {
        kinds: chosen,
        lang,
        campaign_costs_inr: Object.fromEntries(chosen.map((k) => [k, costs[k] === "" ? null : costs[k]])),
      });
      router.push(`/recommendations/${run.run_id}`);
    } catch (err) {
      setError(toApiError(err));
      inFlight.current = false;
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-5" aria-label="Plan product offers">
      <fieldset className="grid gap-3 md:grid-cols-3">
        <legend className="mb-2 flex items-center gap-1 text-sm font-semibold text-ink-2">
          Which kinds of offer should the AI suggest?
          <HelpTip label="Kinds of offer">
            Every idea is checked the same way: code finds your past orders it would apply to, works out the profit on
            the whole order, and checks your limits. A product already discounted never counts towards a cart offer.
          </HelpTip>
        </legend>
        {PROMOTION_KINDS.map((k) => {
          const on = kinds.has(k);
          const t = KIND_TEXT[k];
          return (
            <div
              key={k}
              className={`rounded-xl border p-3 transition ${on ? "border-brand/60 bg-brand-wash/60" : "border-rule bg-surface-2"}`}
            >
              <label className="flex cursor-pointer items-start gap-3">
                <input
                  type="checkbox"
                  className="mt-1 h-4 w-4 accent-[var(--brand)]"
                  checked={on}
                  onChange={() =>
                    setKinds((s) => {
                      const n = new Set(s);
                      if (n.has(k)) n.delete(k);
                      else n.add(k);
                      return n;
                    })
                  }
                />
                <span>
                  <span className="flex items-center gap-1.5 font-bold text-ink">
                    <span aria-hidden="true" className="text-brand">
                      {t.icon}
                    </span>
                    {t.title}
                  </span>
                  <span className="block text-[13px] text-ink-2">{t.hint}</span>
                  <span className="block text-[12px] text-ink-3">{t.example}</span>
                </span>
              </label>
              <label className="mt-3 block text-[12px] text-ink-3">
                Cost to run it (posters, messages)
                <span className="relative mt-1 block">
                  <span className="pointer-events-none absolute left-3 top-2 text-ink-3">₹</span>
                  <input
                    inputMode="decimal"
                    className="field pl-7"
                    value={costs[k]}
                    disabled={!on}
                    aria-invalid={invalid.includes(k)}
                    aria-label={`${t.title} running cost in rupees`}
                    placeholder="0"
                    onChange={(e) => setCosts((c) => ({ ...c, [k]: e.target.value.trim() }))}
                  />
                </span>
              </label>
            </div>
          );
        })}
      </fieldset>
      {invalid.length > 0 && <p className="text-sm text-loss">Enter costs in rupees, like 500 or 500.50.</p>}
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          className="btn-primary px-5 py-3"
          disabled={pending || invalid.length > 0 || kinds.size === 0 || selectedCount === 0}
          aria-busy={pending}
        >
          {pending ? <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" /> : <Sparkles aria-hidden="true" className="h-4 w-4" />}
          {pending ? "Getting offer ideas…" : "Get offer ideas"}
        </button>
        <label className="flex items-center gap-2 text-sm text-ink-2">
          <Languages aria-hidden="true" className="h-4 w-4" />
          Explain in
          <select className="field w-auto" value={lang} onChange={(e) => setLang(e.target.value as Language)}>
            <option value="en">English</option>
            <option value="hinglish">Hinglish</option>
          </select>
        </label>
        <span className="text-[13px] text-ink-3">Uses your {selectedCount} selected products.</span>
      </div>
      {selectedCount === 0 && <p className="text-sm text-pred">Select at least one product above first.</p>}
      {pending && (
        <p role="status" className="text-[13px] text-ink-3">
          The AI is proposing offers and code is checking each one against your past orders. This can take up to a minute.
        </p>
      )}
      {error && <ErrorBox error={error} />}
    </form>
  );
}
