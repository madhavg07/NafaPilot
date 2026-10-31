import type { MerchantAnalysis } from "@/lib/api";
import { date, inr, num, signedPct } from "@/lib/format";

import { WeeklyRateChart } from "../charts";
import { Empty, KindBlock } from "../ui";

export function Opportunities({ a }: { a: MerchantAnalysis }) {
  if (a.opportunity_status === "insufficient_data") {
    return <Empty>Not enough history to compare periods yet.</Empty>;
  }
  if (a.alerts.length === 0) {
    return (
      <Empty>
        No large change in any time slot. A recommendation can still be requested, and
        &ldquo;do nothing&rdquo; is a valid answer.
      </Empty>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      {a.alerts.map((al) => {
        const trend = a.trends.find((t) => t.segment_id === al.segment_id);
        const down = al.direction === "decline";
        return (
          <article key={al.alert_id} aria-labelledby={`alert-${al.alert_id}`} className={`rounded-2xl border border-rule border-l-4 p-4 sm:p-5 ${down ? "border-l-loss" : "border-l-gain"}`}>
            <h3 id={`alert-${al.alert_id}`} className="flex flex-wrap items-center gap-2 text-lg font-bold tracking-tight">
              {al.label}: orders {down ? "down" : "up"}
              <span className={`chip text-sm ${down ? "bg-loss-wash text-loss" : "bg-gain-wash text-gain"}`}>
                {down ? "▼" : "▲"} {signedPct(al.change_pct)}
              </span>
            </h3>
            <p className="mt-1 text-ink-2">
              From {num(al.reference_orders_per_day, 1)} to {num(al.latest_orders_per_day, 1)} orders a day (week of{" "}
              {date(al.latest_week[0])}, compared with {date(al.reference_period[0])} to {date(al.reference_period[1])}). Your sales data can&apos;t tell us why, so we don&apos;t guess.
            </p>
            {trend && (
              <div className="mt-3 max-w-xl">
                <WeeklyRateChart
                  label={al.label}
                  weeks={trend.weekly}
                  reference={al.reference_period}
                  recentFrom={shiftWeek(al.latest_week[0], -1)}
                />
              </div>
            )}
            <p className="mt-2 text-[13px] text-ink-3">
              Screening result, {al.confidence} confidence by rule (not a probability). Nominal p = {al.p_value.toExponential(1)}.
              Rates are per day with recorded orders.
            </p>
            {al.projected_baseline_4w && (
              <div className="mt-4">
                <KindBlock kind="projection" title="Next 4 weeks if nothing changes" tag="Projection, not observed">
                  <dl className="grid grid-cols-3 gap-x-6 text-sm">
                    <div>
                      <dt className="label">Orders</dt>
                      <dd className="figure text-lg">{num(al.projected_baseline_4w.orders)}</dd>
                    </div>
                    <div>
                      <dt className="label">Revenue</dt>
                      <dd className="figure text-lg">{inr(al.projected_baseline_4w.revenue_inr)}</dd>
                    </div>
                    <div>
                      <dt className="label">Contribution</dt>
                      <dd className="figure text-lg">{inr(al.projected_baseline_4w.contribution_profit_inr)}</dd>
                    </div>
                  </dl>
                  <p className="mt-2 text-[13px] text-ink-3">
                    {al.projected_baseline_4w.caveat} It assumes the shop trades on every calendar day in those 4 weeks.
                  </p>
                </KindBlock>
              </div>
            )}
            {al.data_warnings.length > 0 && (
              <ul className="mt-2 list-disc pl-5 text-[13px] text-pred">
                {al.data_warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}
          </article>
        );
      })}
    </div>
  );
}

/** The "recent" window is the last two weeks: the latest week and the one before it. */
function shiftWeek(d: string, weeks: number): string {
  const x = new Date(`${d}T12:00:00`);
  x.setDate(x.getDate() + weeks * 7);
  return x.toISOString().slice(0, 10);
}
