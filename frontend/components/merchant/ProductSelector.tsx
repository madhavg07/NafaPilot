"use client";

import { Check, Link2, RotateCcw, Save } from "lucide-react";
import { useMemo, useState } from "react";

import { ApiError, saveProductSelection, type CatalogueOut } from "@/lib/api";
import { inr, num } from "@/lib/format";
import { toApiError } from "@/lib/useApi";

import { ErrorBox, HelpTip } from "../ui";

function Trend({ pct }: { pct: number | null }) {
  if (pct === null) return <span className="text-ink-3">new</span>;
  const up = pct > 0;
  const flat = Math.abs(pct) < 3;
  return (
    <span className={`chip ${flat ? "bg-surface-2 text-ink-2" : up ? "bg-gain-wash text-gain" : "bg-loss-wash text-loss"}`}>
      {flat ? "≈" : up ? "▲" : "▼"} {num(Math.abs(pct), 0)}%
    </span>
  );
}

export function ProductSelector({
  merchantId,
  catalogue,
  onSaved,
}: {
  merchantId: string;
  catalogue: CatalogueOut;
  onSaved: (message: string) => void;
}) {
  const initial = useMemo(() => new Set(catalogue.selected_skus), [catalogue.selected_skus]);
  const [chosen, setChosen] = useState<Set<string>>(initial);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const changed = chosen.size !== initial.size || [...chosen].some((s) => !initial.has(s));
  const names = Object.fromEntries(catalogue.products.map((p) => [p.sku, p.name]));

  function toggle(sku: string) {
    setChosen((c) => {
      const n = new Set(c);
      if (n.has(sku)) n.delete(sku);
      else n.add(sku);
      return n;
    });
  }

  async function save() {
    setPending(true);
    setError(null);
    try {
      await saveProductSelection(merchantId, [...chosen]);
      onSaved(`${chosen.size} product${chosen.size === 1 ? "" : "s"} selected for offers.`);
    } catch (err) {
      setError(toApiError(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-5" data-testid="product-selector">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-ink-2">
          <strong className="text-ink" data-testid="selected-count">
            {chosen.size} of {catalogue.products.length}
          </strong>{" "}
          products can be used in offers. The AI only sees these, and code rejects any offer on other products.
        </p>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn-secondary" onClick={() => setChosen(new Set(catalogue.products.map((p) => p.sku)))}>
            <Check aria-hidden="true" className="h-4 w-4" /> Select all
          </button>
          <button type="button" className="btn-secondary" onClick={() => setChosen(new Set(initial))} disabled={!changed}>
            <RotateCcw aria-hidden="true" className="h-4 w-4" /> Undo changes
          </button>
          <button type="button" className="btn-primary" onClick={save} disabled={!changed || pending} aria-busy={pending}>
            <Save aria-hidden="true" className="h-4 w-4" />
            {pending ? "Saving…" : "Save selection"}
          </button>
        </div>
      </div>

      <div className="overflow-x-auto rounded-xl border border-rule">
        <table className="w-full min-w-[44rem] text-sm">
          <caption className="sr-only">Product catalogue with prices, margins and sales in the last 28 days</caption>
          <thead className="bg-surface-2 text-left text-[13px] text-ink-3">
            <tr>
              <th scope="col" className="w-10 py-2 pl-3 font-semibold">
                <span className="sr-only">Use in offers</span>
              </th>
              <th scope="col" className="py-2 pr-3 font-semibold">Product</th>
              <th scope="col" className="py-2 pr-3 text-right font-semibold">Price</th>
              <th scope="col" className="py-2 pr-3 text-right font-semibold">
                <span className="inline-flex items-center gap-1">
                  Profit per item
                  <HelpTip label="Profit per item">Price minus what one item costs you to make or buy.</HelpTip>
                </span>
              </th>
              <th scope="col" className="py-2 pr-3 text-right font-semibold">Sold, 28 days</th>
              <th scope="col" className="py-2 pr-3 font-semibold">vs 28 days before</th>
            </tr>
          </thead>
          <tbody>
            {catalogue.products.map((p) => {
              const on = chosen.has(p.sku);
              return (
                <tr
                  key={p.sku}
                  className={`border-t border-rule transition ${on ? "bg-surface" : "bg-surface-2/60 text-ink-3"}`}
                >
                  <td className="py-2 pl-3">
                    <input
                      type="checkbox"
                      className="h-4 w-4 accent-[var(--brand)]"
                      checked={on}
                      onChange={() => toggle(p.sku)}
                      aria-label={`Use ${p.name} in offers`}
                    />
                  </td>
                  <td className="py-2 pr-3">
                    <span className={`font-semibold ${on ? "text-ink" : ""}`}>{p.name}</span>
                    <span className="ml-2 text-[12px] text-ink-3">
                      {p.sku} · {p.category}
                    </span>
                  </td>
                  <td className="figure py-2 pr-3 text-right font-semibold">{inr(p.price_inr)}</td>
                  <td className="py-2 pr-3 text-right">
                    {p.margin_inr === null ? (
                      <span className="text-ink-3">cost unknown</span>
                    ) : (
                      <>
                        <span className="figure font-semibold">{inr(p.margin_inr)}</span>{" "}
                        <span className="text-[12px] text-ink-3">({num(p.margin_pct, 0)}%)</span>
                      </>
                    )}
                  </td>
                  <td className="figure py-2 pr-3 text-right">{num(p.stats.units_28d)}</td>
                  <td className="py-2 pr-3">
                    <Trend pct={p.stats.trend_pct} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {catalogue.pairs.length > 0 && (
        <div>
          <h3 className="flex items-center gap-1 text-sm font-bold">
            <Link2 aria-hidden="true" className="h-4 w-4 text-brand" />
            Often bought together
            <HelpTip label="Often bought together">
              Found in your last 28 days of orders. “2.3× more often” means the two appear in the same order 2.3 times
              more than if customers picked them at random. It shows a pattern, not a cause.
            </HelpTip>
          </h3>
          <ul className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3" data-testid="pairs">
            {catalogue.pairs.slice(0, 6).map((x) => (
              <li key={`${x.sku_a}-${x.sku_b}`} className="rounded-xl border border-rule bg-surface-2 px-3 py-2 text-sm">
                <span className="font-semibold">
                  {names[x.sku_a]} + {names[x.sku_b]}
                </span>
                <span className="block text-[12px] text-ink-3">
                  {num(x.orders_together_28d)} orders together · {num(x.lift, 1)}× more often than chance ·{" "}
                  {num(x.confidence_a_to_b * 100, 0)}% of {names[x.sku_a]} orders
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="text-[13px] text-ink-3">
        Typical order in the last 28 days: {inr(catalogue.basket.average_value_inr)} with{" "}
        {num(catalogue.basket.average_items, 1)} items; a quarter of orders are above {inr(catalogue.basket.p75_value_inr)}.
      </p>
      {error && <ErrorBox error={error} />}
    </div>
  );
}
