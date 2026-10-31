"use client";

import { Layers, Minus, Package, Plus, ShoppingCart, ShoppingBasket } from "lucide-react";
import { useState } from "react";

import {
  ApiError,
  checkBasket,
  getProducts,
  type BasketCheckOut,
  type CandidateOut,
  type PromotionKind,
  type PromotionSpec,
} from "@/lib/api";
import { inr, inrSigned, num } from "@/lib/format";
import { toApiError, useApi } from "@/lib/useApi";

import { Badge, ErrorBox, HelpTip, Loading } from "../ui";

const KIND_BADGE: Record<PromotionKind, { label: string; tone: "blue" | "violet" | "amber" }> = {
  product: { label: "Product offer", tone: "blue" },
  bundle: { label: "Bundle offer", tone: "violet" },
  cart: { label: "Cart offer", tone: "amber" },
};

export function KindIcon({ kind, className = "h-4 w-4" }: { kind: PromotionKind; className?: string }) {
  if (kind === "bundle") return <Layers aria-hidden="true" className={className} />;
  if (kind === "cart") return <ShoppingCart aria-hidden="true" className={className} />;
  return <Package aria-hidden="true" className={className} />;
}

export function KindBadge({ kind }: { kind: PromotionKind }) {
  const b = KIND_BADGE[kind];
  return <Badge tone={b.tone}>{b.label}</Badge>;
}

function slotText(p: PromotionSpec): string {
  if (!p.hours) return p.weekdays_only ? "Weekdays, all day" : "All day, every day";
  const from = Math.min(...p.hours);
  const to = Math.max(...p.hours) + 1;
  const fmt = (h: number) => `${((h + 11) % 12) + 1} ${h < 12 || h === 24 ? "am" : "pm"}`;
  return `${fmt(from)} to ${fmt(to)}${p.weekdays_only ? ", weekdays only" : ""}`;
}

/** The facts code used for one offer: price, discount share, qualifying orders, notes. */
export function OfferFacts({ p, notes }: { p: PromotionSpec; notes?: string[] }) {
  const pct = p.list_price_inr ? (p.discount_inr / p.list_price_inr) * 100 : null;
  return (
    <div className="mt-3 rounded-xl border border-rule bg-surface-2 p-4 text-sm" data-testid="offer-facts">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
        <div>
          <dt className="label">{p.kind === "cart" ? "Minimum eligible cart" : p.kind === "bundle" ? "Combo list price" : "Product price"}</dt>
          <dd className="figure text-base">{inr(p.list_price_inr)}</dd>
        </div>
        <div>
          <dt className="label">{p.kind === "bundle" ? "Customer pays for the combo" : "Discount"}</dt>
          <dd className="figure text-base">
            {p.kind === "bundle" && p.list_price_inr !== null ? inr(p.list_price_inr - p.discount_inr) : `${inr(p.discount_inr)} off`}
            {pct !== null && <span className="ml-1 text-[12px] font-medium text-ink-3">({num(pct, 1)}%)</span>}
          </dd>
        </div>
        <div>
          <dt className="label flex items-center gap-1">
            Would have applied
            <HelpTip label="Would have applied">
              Orders in the last 28 days that already met this offer. If it runs, these customers get the discount even
              though they would have bought anyway. Code counts them from your real order lines.
            </HelpTip>
          </dt>
          <dd className="figure text-base">{num(p.qualifying_orders_28d)} orders</dd>
        </div>
        <div>
          <dt className="label">When</dt>
          <dd className="text-[13px] font-semibold">{slotText(p)}</dd>
        </div>
      </dl>
      {p.kind === "cart" && (
        <p className="mt-3 text-[12px] text-ink-3">
          Only items not already discounted by a product or bundle offer, and only products you selected (
          {p.eligible_skus.length}), count towards the minimum.
        </p>
      )}
      {p.basket_basis && <p className="mt-2 text-[12px] text-ink-3">{p.basket_basis}</p>}
      {notes && notes.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-[12px] text-pred">
          {notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Side-by-side comparison of all offers in a run. */
export function OfferComparison({ candidates, selected }: { candidates: CandidateOut[]; selected: string | null }) {
  const rows = candidates.filter((c) => c.promotion);
  if (rows.length === 0) return null;
  const sc = (c: CandidateOut, n: "conservative" | "expected") =>
    c.economics?.scenarios.find((s) => s.scenario === n)?.incremental_contribution_profit_inr ?? null;
  return (
    <section className="card animate-rise p-5 sm:p-6" aria-labelledby="compare-title">
      <h2 id="compare-title" className="text-lg font-bold tracking-tight">
        Compare the offers
      </h2>
      <p className="mt-0.5 text-sm text-ink-2">
        Profit is for 28 days on whole orders, after the discount and running cost. “Bad case” is what your loss limit is
        checked against.
      </p>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[38rem] text-sm" data-testid="offer-comparison">
          <thead className="text-left text-[13px] text-ink-3">
            <tr className="border-b border-rule">
              <th scope="col" className="py-2 pr-3 font-semibold">Offer</th>
              <th scope="col" className="py-2 pr-3 text-right font-semibold">Likely profit</th>
              <th scope="col" className="py-2 pr-3 text-right font-semibold">Bad case</th>
              <th scope="col" className="py-2 font-semibold">Your limits</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => {
              const p = c.promotion as PromotionSpec;
              const exp = sc(c, "expected");
              const con = sc(c, "conservative");
              return (
                <tr key={c.record_id} className={`border-b border-rule last:border-0 ${selected === c.record_id ? "bg-brand-wash/60" : ""}`}>
                  <td className="py-2.5 pr-3">
                    <span className="flex items-center gap-2 font-semibold">
                      <span className="text-ink-3">{c.record_id}.</span>
                      <KindIcon kind={p.kind} />
                      {p.label}
                    </span>
                  </td>
                  <td className={`figure py-2.5 pr-3 text-right ${exp !== null && exp < 0 ? "text-loss" : "text-gain"}`}>
                    {exp === null ? "—" : inrSigned(Math.round(exp))}
                  </td>
                  <td className={`figure py-2.5 pr-3 text-right ${con !== null && con < 0 ? "text-loss" : ""}`}>
                    {con === null ? "—" : inrSigned(Math.round(con))}
                  </td>
                  <td className="py-2.5">
                    {c.status === "filtered_by_preference" ? (
                      <Badge tone="zinc">Set aside</Badge>
                    ) : c.policy?.permitted ? (
                      <Badge tone="green">{selected === c.record_id ? "Pass · recommended" : "Pass"}</Badge>
                    ) : (
                      <Badge tone="red">Blocked</Badge>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** Try a basket against the run's offers to see who gets which discount. */
export function BasketChecker({ merchantId, candidates }: { merchantId: string; candidates: CandidateOut[] }) {
  const cat = useApi(`products:${merchantId}`, (s) => getProducts(merchantId, s));
  const offers = candidates.filter((c) => c.campaign_id && c.promotion);
  const [ids, setIds] = useState<Set<string>>(() => new Set(offers.map((c) => c.campaign_id as string)));
  const [qty, setQty] = useState<Record<string, number>>(() => {
    const first: Record<string, number> = {};
    for (const c of offers) for (const s of c.promotion?.skus ?? []) first[s] = 1;
    return first;
  });
  const [result, setResult] = useState<BasketCheckOut | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [pending, setPending] = useState(false);

  if (offers.length === 0) return null;
  const lines = Object.entries(qty).filter(([, q]) => q > 0);

  async function run() {
    setPending(true);
    setError(null);
    try {
      setResult(
        await checkBasket(merchantId, {
          lines: lines.map(([sku, quantity]) => ({ sku, quantity })),
          campaign_ids: [...ids],
        }),
      );
    } catch (err) {
      setError(toApiError(err));
    } finally {
      setPending(false);
    }
  }

  const bump = (sku: string, d: number) => {
    setResult(null);
    setQty((q) => ({ ...q, [sku]: Math.max(0, Math.min(20, (q[sku] ?? 0) + d)) }));
  };

  return (
    <section className="card animate-rise p-5 sm:p-6" aria-labelledby="basket-title" data-testid="basket-checker">
      <div className="flex items-start gap-3">
        <span aria-hidden="true" className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-sim-wash text-sim">
          <ShoppingBasket className="h-5 w-5" />
        </span>
        <div>
          <h2 id="basket-title" className="text-lg font-bold tracking-tight">
            Try a basket
          </h2>
          <p className="mt-0.5 text-sm text-ink-2">
            Build an order and see exactly which discount each item gets if these offers run together. An item never gets
            two discounts, and discounted items don’t count towards a cart offer.
          </p>
        </div>
      </div>

      {cat.kind === "loading" && <Loading what="products" />}
      {cat.kind === "error" && <ErrorBox error={cat.error} onRetry={cat.reload} />}
      {cat.kind === "ok" && (
        <div className="mt-4 grid gap-5 lg:grid-cols-2">
          <div>
            <fieldset>
              <legend className="text-sm font-semibold text-ink-2">Offers running</legend>
              <div className="mt-2 flex flex-col gap-1.5">
                {offers.map((c) => {
                  const id = c.campaign_id as string;
                  return (
                    <label key={id} className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        className="h-4 w-4 accent-[var(--brand)]"
                        checked={ids.has(id)}
                        onChange={() => {
                          setResult(null);
                          setIds((s) => {
                            const n = new Set(s);
                            if (n.has(id)) n.delete(id);
                            else n.add(id);
                            return n;
                          });
                        }}
                      />
                      <span className="text-ink-3">{c.record_id}.</span> {c.promotion?.label}
                    </label>
                  );
                })}
              </div>
            </fieldset>
            <h3 className="mt-4 text-sm font-semibold text-ink-2">Items in the basket</h3>
            <ul className="mt-2 max-h-72 overflow-y-auto rounded-xl border border-rule">
              {cat.data.products.map((p) => (
                <li key={p.sku} className="flex items-center justify-between gap-2 border-b border-rule px-3 py-1.5 text-sm last:border-0">
                  <span className="min-w-0 truncate">
                    {p.name} <span className="text-[12px] text-ink-3">{inr(p.price_inr)}</span>
                  </span>
                  <span className="flex items-center gap-1">
                    <button type="button" className="btn-secondary px-2 py-1" aria-label={`One less ${p.name}`} onClick={() => bump(p.sku, -1)}>
                      <Minus aria-hidden="true" className="h-3.5 w-3.5" />
                    </button>
                    <span className="w-6 text-center font-semibold tabular-nums" aria-label={`${p.name} quantity`}>
                      {qty[p.sku] ?? 0}
                    </span>
                    <button type="button" className="btn-secondary px-2 py-1" aria-label={`One more ${p.name}`} onClick={() => bump(p.sku, 1)}>
                      <Plus aria-hidden="true" className="h-3.5 w-3.5" />
                    </button>
                  </span>
                </li>
              ))}
            </ul>
            <button type="button" className="btn-sim mt-3" onClick={run} disabled={pending || lines.length === 0}>
              {pending ? "Checking…" : "Check this basket"}
            </button>
          </div>

          <div aria-live="polite">
            {error && <ErrorBox error={error} />}
            {!result && !error && (
              <p className="rounded-xl border border-dashed border-rule-strong p-4 text-sm text-ink-3">
                Pick items and press “Check this basket”.
              </p>
            )}
            {result && (
              <div className="flex flex-col gap-3" data-testid="basket-result">
                <ul className="rounded-xl border border-rule text-sm">
                  {result.units.map((u) => (
                    <li key={`${u.sku}-${u.unit}`} className="flex items-start justify-between gap-3 border-b border-rule px-3 py-2 last:border-0">
                      <span>
                        {u.name}
                        {u.unit > 1 && <span className="text-ink-3"> #{u.unit}</span>}
                        <span className="block text-[12px] text-ink-3">
                          {u.used_by ? `Used by: ${u.used_by}` : "No offer on this item"}
                          {u.cart_eligible ? " · counts towards cart offer" : " · does not count towards cart offer"}
                        </span>
                      </span>
                      <span className="figure">{inr(u.price_inr)}</span>
                    </li>
                  ))}
                </ul>
                <ul className="flex flex-col gap-1.5 text-sm">
                  {result.offers.map((o, i) => (
                    <li key={i} className={`rounded-lg px-3 py-2 ${o.applied ? "bg-gain-wash" : "bg-surface-2"}`}>
                      <span className="font-semibold">{o.applied ? `−${inr(o.discount_inr)}` : "Not applied"}</span> · {o.label}
                      <span className="block text-[12px] text-ink-2">{o.reason}</span>
                    </li>
                  ))}
                </ul>
                <dl className="grid grid-cols-2 gap-2 rounded-xl bg-surface-2 p-3 text-sm">
                  <dt className="text-ink-3">Basket total</dt>
                  <dd className="figure text-right">{inr(result.cart_total_inr)}</dd>
                  <dt className="text-ink-3">Counts towards cart offer</dt>
                  <dd className="figure text-right">{inr(result.cart_eligible_subtotal_inr)}</dd>
                  <dt className="text-ink-3">Total discount</dt>
                  <dd className="figure text-right text-loss">−{inr(result.total_discount_inr)}</dd>
                  <dt className="font-semibold">Customer pays</dt>
                  <dd className="figure text-right font-bold">{inr(result.customer_pays_inr)}</dd>
                  <dt className="text-ink-3">Your profit on this order</dt>
                  <dd className="figure text-right">
                    {inr(result.contribution_after_inr)}{" "}
                    <span className="text-[12px] text-ink-3">(was {inr(result.contribution_before_inr)})</span>
                  </dd>
                </dl>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
