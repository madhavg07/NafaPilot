"use client";

import { BarChart3, Boxes, IndianRupee, Lightbulb, MapPin, Megaphone, Receipt, ShoppingBag, SlidersHorizontal, TrendingUp, Wallet } from "lucide-react";
import { useCallback, useState, type ReactNode } from "react";

import { getBaseline, getMerchant, getProducts, getSettings } from "@/lib/api";
import { capitalize, date, humanize, inr, num } from "@/lib/format";
import { useApi } from "@/lib/useApi";

import { Breadcrumbs } from "../AppHeader";
import { ErrorBox, HelpTip, Loading, Panel, Toast } from "../ui";
import { BaselinePanel } from "./BaselinePanel";
import { CampaignsPanel } from "./CampaignsPanel";
import { OfferPlanner } from "./OfferPlanner";
import { Opportunities } from "./Opportunities";
import { ProductSelector } from "./ProductSelector";
import { RecommendPanel } from "./RecommendPanel";
import { SettingsEditor } from "./SettingsEditor";

function HeroStat({
  icon,
  label,
  value,
  sub,
  help,
}: {
  icon: ReactNode;
  label: string;
  value: ReactNode;
  sub: ReactNode;
  help?: ReactNode;
}) {
  return (
    <div className="rounded-2xl bg-white/12 p-4 ring-1 ring-white/20 backdrop-blur-sm">
      <dt className="flex items-center gap-1.5 text-[13px] font-semibold text-white/85">
        <span aria-hidden="true">{icon}</span>
        {label}
        {help && <span className="text-white [&_button]:text-white/80">{<HelpTip label={label}>{help}</HelpTip>}</span>}
      </dt>
      <dd className="mt-1 text-[26px] font-extrabold leading-tight tracking-tight">{value}</dd>
      <dd className="text-[12px] text-white/75">{sub}</dd>
    </div>
  );
}

const NAV = [
  ["#opps", "What changed"],
  ["#products", "Your products"],
  ["#recommend", "Get offer ideas"],
  ["#campaigns", "Your offers"],
  ["#policy", "Settings"],
  ["#recent", "Sales history"],
] as const;

export function MerchantDashboard({ merchantId }: { merchantId: string }) {
  const profile = useApi(`merchant:${merchantId}`, (s) => getMerchant(merchantId, s));
  const baseline = useApi(`baseline:${merchantId}`, (s) => getBaseline(merchantId, s));
  const settings = useApi(`settings:${merchantId}`, (s) => getSettings(merchantId, s));
  const catalogue = useApi(`products:${merchantId}`, (s) => getProducts(merchantId, s));
  const [toast, setToast] = useState<string | null>(null);
  const clearToast = useCallback(() => setToast(null), []);

  if (profile.kind === "error") {
    return <ErrorBox error={profile.error} onRetry={profile.reload} />;
  }
  const p = profile.kind === "ok" ? profile.data : null;
  const b = baseline.kind === "ok" ? baseline.data.baseline : null;
  const alerts = baseline.kind === "ok" ? baseline.data.alerts : [];
  const s = settings.kind === "ok" ? settings.data : null;

  const cat = catalogue.kind === "ok" ? catalogue.data : null;
  // No product data (e.g. only order totals): fall back to shop-wide campaigns.
  const noProducts = catalogue.kind === "error" && catalogue.error.code === "no_product_data";

  function onSaved(message: string) {
    setToast(message);
    settings.reload();
    profile.reload();
  }

  function onSelectionSaved(message: string) {
    setToast(message);
    catalogue.reload();
  }

  return (
    <>
      <Breadcrumbs items={[{ href: "/", label: "Shops" }, { label: p?.name ?? merchantId }]} />

      <section className="hero animate-rise overflow-hidden rounded-3xl p-6 text-white shadow-lift sm:p-8">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-[28px] font-extrabold leading-tight tracking-tight sm:text-[34px]">{p?.name ?? merchantId}</h1>
            {p ? (
              <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-white/85">
                <span>{capitalize(humanize(p.category))}</span>
                <span className="flex items-center gap-1">
                  <MapPin aria-hidden="true" className="h-4 w-4" />
                  {p.city}
                </span>
                <span>
                  Avg. order {inr(p.average_order_value_inr)} · cost per order {inr(p.variable_cost_per_order_inr)}
                </span>
              </p>
            ) : (
              <p className="mt-1 text-white/80">Loading shop…</p>
            )}
          </div>
          <a href="#recommend" className="inline-flex items-center gap-2 rounded-xl bg-white px-4 py-2.5 text-sm font-bold text-teal-800 shadow-sm transition hover:-translate-y-0.5">
            <Lightbulb aria-hidden="true" className="h-4 w-4" />
            Get offer ideas
          </a>
        </div>
        {b ? (
          <dl className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="Last 28 days">
            <HeroStat icon={<ShoppingBag className="h-4 w-4" />} label="Orders, last 28 days" value={num(b.orders)} sub={`${num(b.orders_per_day_with_orders, 1)} per trading day`} />
            <HeroStat icon={<IndianRupee className="h-4 w-4" />} label="Revenue" value={inr(Math.round(b.revenue_inr))} sub={`${date(b.period_start)} to ${date(b.period_end)}`} />
            <HeroStat
              icon={<Wallet className="h-4 w-4" />}
              label="Contribution profit"
              value={inr(b.contribution_profit_inr === null ? null : Math.round(b.contribution_profit_inr))}
              sub="what you kept after costs per order"
              help="Money left from sales after paying for what each order costs you (ingredients, packaging). Rent and salaries are not included."
            />
            <HeroStat icon={<Receipt className="h-4 w-4" />} label="Average order" value={inr(b.average_order_value_inr)} sub={`${num(b.customer_id_coverage * 100, 0)}% of orders have a customer ID`} />
          </dl>
        ) : baseline.kind === "loading" ? (
          <p className="mt-6 text-sm text-white/80">Loading your sales…</p>
        ) : null}
      </section>

      <nav aria-label="On this page" className="-mt-1 flex gap-2 overflow-x-auto pb-1">
        {NAV.map(([href, label]) => (
          <a key={href} href={href} className="chip shrink-0 border border-rule bg-surface px-3 py-1.5 text-ink-2 shadow-card transition hover:border-brand hover:text-brand">
            {label}
          </a>
        ))}
      </nav>

      <NextStep alerts={alerts.length} loaded={baseline.kind === "ok"} />

      <Panel
        title="What changed in your sales"
        id="opps"
        icon={<TrendingUp className="h-5 w-5" />}
        accent="est"
        note="We compare each time of day with your own earlier weeks. Only big changes that are unlikely to be chance are shown."
      >
        {baseline.kind === "loading" && <Loading what="opportunities" />}
        {baseline.kind === "error" && <ErrorBox error={baseline.error} onRetry={baseline.reload} />}
        {baseline.kind === "ok" && <Opportunities a={baseline.data} />}
      </Panel>

      {!noProducts && (
        <Panel
          title="Your products"
          id="products"
          icon={<Boxes className="h-5 w-5" />}
          accent="est"
          note="Tick the products you are happy to put on offer. Selecting a product doesn't mean it will be discounted."
        >
          {catalogue.kind === "loading" && <Loading what="products" />}
          {catalogue.kind === "error" && <ErrorBox error={catalogue.error} onRetry={catalogue.reload} />}
          {cat && (
            <ProductSelector
              key={`${cat.selection_version}`}
              merchantId={merchantId}
              catalogue={cat}
              onSaved={onSelectionSaved}
            />
          )}
        </Panel>
      )}

      <Panel
        title="Plan a campaign"
        id="recommend"
        icon={<Lightbulb className="h-5 w-5" />}
        note="The AI suggests product, bundle and cart offers. Code works out the profit on whole orders and checks your limits."
      >
        {(settings.kind === "loading" || catalogue.kind === "loading") && <Loading what="your usual costs" />}
        {cat && settings.kind !== "loading" && (
          <OfferPlanner
            key={`${s?.updated_at ?? "default"}-${cat.selection_version}`}
            merchantId={merchantId}
            settings={s}
            selectedCount={cat.selected_skus.length}
          />
        )}
        {noProducts && (
          <p className="mb-4 rounded-xl bg-pred-wash px-4 py-3 text-sm text-pred">
            This shop has no product-level sales data, so only shop-wide offers can be suggested below. They are not tied
            to any product.
          </p>
        )}
        {(noProducts || cat) && settings.kind !== "loading" && (
          <details className="mt-5 rounded-xl border border-rule p-4" open={noProducts}>
            <summary className="cursor-pointer text-sm font-semibold">
              Shop-wide campaign instead (not tied to products)
            </summary>
            <div className="mt-4">
              <RecommendPanel key={s?.updated_at ?? "default"} merchantId={merchantId} settings={s} />
            </div>
          </details>
        )}
      </Panel>

      <Panel
        title="Your offers"
        id="campaigns"
        icon={<Megaphone className="h-5 w-5" />}
        accent="sim"
        note="Expected profit is a forecast. Measured profit comes from a test run (simulation)."
      >
        <CampaignsPanel merchantId={merchantId} />
      </Panel>

      <Panel
        title="Limits and preferences"
        id="policy"
        icon={<SlidersHorizontal className="h-5 w-5" />}
        accent="pred"
        note="Your rules for every offer. Starting values are filled in for you; change them whenever you like."
      >
        {settings.kind === "loading" && <Loading what="settings" />}
        {settings.kind === "error" && <ErrorBox error={settings.error} onRetry={settings.reload} />}
        {s && <SettingsEditor merchantId={merchantId} settings={s} onSaved={onSaved} />}
      </Panel>

      <Panel
        title="Sales history, last 28 days"
        id="recent"
        icon={<BarChart3 className="h-5 w-5" />}
        note="Recorded orders from the demo data."
      >
        {baseline.kind === "loading" && <Loading what="baseline" />}
        {baseline.kind === "error" && <p className="text-sm text-ink-3">Unavailable.</p>}
        {baseline.kind === "ok" && <BaselinePanel a={baseline.data} />}
      </Panel>

      <Toast message={toast} onDone={clearToast} />
    </>
  );
}

function NextStep({ alerts, loaded }: { alerts: number; loaded: boolean }) {
  if (!loaded) return null;
  return (
    <aside className="animate-rise flex flex-wrap items-center gap-3 rounded-2xl border border-brand/25 bg-brand-wash px-5 py-4">
      <span aria-hidden="true" className="grid h-9 w-9 place-items-center rounded-full bg-brand text-white dark:text-slate-900">
        <Lightbulb className="h-4 w-4" />
      </span>
      <p className="min-w-0 flex-1 text-sm">
        <strong>Next step: </strong>
        {alerts > 0
          ? `We spotted ${alerts} big change${alerts > 1 ? "s" : ""} in your sales. Check your limits once, then ask for offer ideas.`
          : "No big change in your sales right now. You can still ask for offer ideas; “do nothing” is a fine answer too."}
      </p>
      <a href="#recommend" className="btn-primary">
        Get offer ideas
      </a>
    </aside>
  );
}
