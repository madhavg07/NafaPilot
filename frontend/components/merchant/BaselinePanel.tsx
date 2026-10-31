import type { MerchantAnalysis } from "@/lib/api";
import { date, humanize, num, pct } from "@/lib/format";

import { DailyOrdersChart } from "../charts";

const BASIS_TEXT = {
  order_costs: "Profit uses the cost recorded on each order.",
  merchant_setting: "Profit uses the merchant's cost-per-order setting.",
  unavailable: "Cost data is missing, so profit can't be calculated.",
} as const;

export function BaselinePanel({ a }: { a: MerchantAnalysis }) {
  const b = a.baseline;
  const attention = a.gaps.length > 0 || a.data_warnings.length > 0 || a.data_quality.rows_rejected > 0;
  return (
    <div className="flex flex-col gap-4" data-testid="baseline-observed" data-kind="observed">
      {a.status === "insufficient_data" && (
        <div role="note" className="border-l-2 border-pred bg-pred-wash px-4 py-3 text-sm">
          <p className="font-semibold">Not enough data for a full baseline</p>
          <ul className="mt-1 list-disc pl-5">
            {a.insufficient_reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      )}
      {b.daily.length > 0 ? (
        <DailyOrdersChart daily={b.daily} periodStart={b.period_start} periodEnd={b.period_end} />
      ) : (
        <p className="text-sm text-ink-2">No orders recorded in this period.</p>
      )}
      <p className="text-sm text-ink-2">
        {b.days_with_orders} of {b.calendar_days} days had orders. {BASIS_TEXT[b.contribution_basis]}{" "}
        {b.repeat_customer_share !== null && `${pct(b.repeat_customer_share, 0)} of identified customers are repeat customers.`}
      </p>

      <details className="text-sm">
        <summary className="cursor-pointer text-ink-2">
          Data coverage and quality{attention && <span className="text-pred">, needs attention</span>}
        </summary>
        <div className="mt-2 space-y-2 border-l border-rule pl-4 text-ink-2">
          <p>{a.coverage.limitation}</p>
          <p>
            Orders recorded from {date(a.coverage.first_order_date)} to {date(a.coverage.last_order_date)}; data as of{" "}
            {date(a.as_of)}. {num(a.data_quality.rows_valid)} rows valid, {num(a.data_quality.rows_rejected)} rejected.
          </p>
          {a.gaps.length > 0 && (
            <div>
              <p className="font-medium text-ink">Periods with no recorded orders (cause unknown)</p>
              <ul className="list-disc pl-5">
                {a.gaps.map((g) => (
                  <li key={g.start}>
                    {date(g.start)} to {date(g.end)} ({g.days} days)
                  </li>
                ))}
              </ul>
            </div>
          )}
          {a.data_warnings.length > 0 && (
            <ul className="list-disc pl-5">
              {a.data_warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
          {a.data_quality.issues.length > 0 && (
            <ul className="list-disc pl-5">
              {a.data_quality.issues.map((i) => (
                <li key={i.code}>
                  {humanize(i.code)} ({i.severity}, {i.count}): {i.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      </details>
    </div>
  );
}
