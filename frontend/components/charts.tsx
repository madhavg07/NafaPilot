// Dependency-free SVG charts for data the API returns. Units are always labelled.

import { inr, num, shortDate } from "@/lib/format";

const AXIS = "fill-ink-3 text-[11px]";

export function DailyOrdersChart({
  daily,
  periodStart,
  periodEnd,
}: {
  daily: { date: string; orders: number }[];
  periodStart: string;
  periodEnd: string;
}) {
  // One slot per calendar day; days with no recorded orders are left empty (not drawn as 0).
  const start = new Date(`${periodStart}T12:00:00`);
  const end = new Date(`${periodEnd}T12:00:00`);
  const days: string[] = [];
  for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
    days.push(d.toISOString().slice(0, 10));
  }
  const byDate = new Map(daily.map((p) => [p.date, p.orders]));
  const max = Math.max(1, ...daily.map((p) => p.orders));
  const W = 880;
  const H = 190;
  const padL = 34;
  const padB = 22;
  const padT = 10;
  const bw = (W - padL) / Math.max(days.length, 1);
  const y = (v: number) => padT + (H - padB - padT) * (1 - v / max);
  const ticks = [0, Math.round(max / 2), max];
  const missing = days.filter((d) => !byDate.has(d)).length;
  const weekend = (d: string) => [0, 6].includes(new Date(`${d}T12:00:00`).getDay());

  return (
    <figure>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`Recorded orders per day from ${shortDate(periodStart)} to ${shortDate(periodEnd)}; highest ${max}.`}
        className="h-auto w-full"
      >
        {ticks.map((t) => (
          <g key={t}>
            <line x1={padL} x2={W} y1={y(t)} y2={y(t)} className="stroke-rule" />
            <text x={padL - 6} y={y(t) + 4} textAnchor="end" className={AXIS}>
              {t}
            </text>
          </g>
        ))}
        {days.map((d, i) => {
          const v = byDate.get(d);
          if (v === undefined) return null;
          return (
            <rect
              key={d}
              x={padL + i * bw + 1.5}
              y={y(v)}
              width={Math.max(bw - 3, 1)}
              height={H - padB - y(v)}
              className={weekend(d) ? "fill-brand-2/45" : "fill-brand"}
            >
              <title>{`${shortDate(d)}: ${v} orders`}</title>
            </rect>
          );
        })}
        <text x={padL} y={H - 4} className={AXIS}>
          {shortDate(periodStart)}
        </text>
        <text x={W} y={H - 4} textAnchor="end" className={AXIS}>
          {shortDate(periodEnd)}
        </text>
      </svg>
      <figcaption className="mt-1 text-[13px] text-ink-3">
        Orders per day; weekends lighter.
        {missing > 0 && ` ${missing} day(s) had no recorded orders and are left blank, not counted as zero.`}
      </figcaption>
    </figure>
  );
}

/**
 * Weekly orders per day for one time segment, with the reference and recent windows
 * marked. Weeks with no data are gaps, not zeros.
 */
export function WeeklyRateChart({
  label,
  weeks,
  reference,
  recentFrom,
}: {
  label: string;
  weeks: { week_start: string; orders_per_day_with_orders: number | null }[];
  reference: [string, string];
  recentFrom: string;
}) {
  const vals = weeks.map((w) => w.orders_per_day_with_orders).filter((v): v is number => v !== null);
  if (vals.length < 2) return null;
  const W = 560;
  const H = 130;
  const padL = 30;
  const padB = 20;
  const padT = 12;
  const lo = Math.min(...vals) * 0.85;
  const hi = Math.max(...vals) * 1.08;
  const x = (i: number) => padL + (i * (W - padL - 8)) / Math.max(weeks.length - 1, 1);
  const y = (v: number) => padT + (H - padB - padT) * (1 - (v - lo) / (hi - lo || 1));
  const inRef = (d: string) => d >= reference[0] && d <= reference[1];
  const refIdx = weeks.map((w, i) => (inRef(w.week_start) ? i : -1)).filter((i) => i >= 0);
  const recIdx = weeks.map((w, i) => (w.week_start >= recentFrom ? i : -1)).filter((i) => i >= 0);
  const path = weeks
    .map((w, i) => (w.orders_per_day_with_orders === null ? null : `${x(i)},${y(w.orders_per_day_with_orders)}`))
    .filter(Boolean)
    .join(" L ");

  const band = (idx: number[], cls: string, text: string) =>
    idx.length ? (
      <g>
        <rect x={x(idx[0]) - 6} y={padT - 6} width={x(idx[idx.length - 1]) - x(idx[0]) + 12} height={H - padB - padT + 6} className={cls} />
        <text x={x(idx[0]) - 4} y={padT + 4} className="fill-ink-3 text-[10px]">
          {text}
        </text>
      </g>
    ) : null;

  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${label}: orders per day by week, from ${num(vals[0], 1)} to ${num(vals[vals.length - 1], 1)}.`} className="h-auto w-full">
        {band(refIdx, "fill-ink/5", "reference")}
        {band(recIdx, "fill-loss/10", "recent")}
        <path d={`M ${path}`} fill="none" className="stroke-brand" strokeWidth={2.25} />
        {weeks.map((w, i) =>
          w.orders_per_day_with_orders === null ? null : (
            <circle key={w.week_start} cx={x(i)} cy={y(w.orders_per_day_with_orders)} r={3} className="fill-brand">
              <title>{`Week of ${shortDate(w.week_start)}: ${num(w.orders_per_day_with_orders, 1)} orders/day`}</title>
            </circle>
          ),
        )}
        <text x={padL - 6} y={y(Math.max(...vals)) + 4} textAnchor="end" className={AXIS}>
          {num(Math.max(...vals), 0)}
        </text>
        <text x={padL - 6} y={y(Math.min(...vals)) + 4} textAnchor="end" className={AXIS}>
          {num(Math.min(...vals), 0)}
        </text>
        <text x={x(0)} y={H - 4} className={AXIS}>
          {shortDate(weeks[0].week_start)}
        </text>
        <text x={x(weeks.length - 1)} y={H - 4} textAnchor="end" className={AXIS}>
          {shortDate(weeks[weeks.length - 1].week_start)}
        </text>
      </svg>
      <figcaption className="text-[13px] text-ink-3">Orders per day, by week, for {label.toLowerCase()}.</figcaption>
    </figure>
  );
}

/**
 * A point estimate with its confidence interval on an axis that always includes zero, so a
 * reader can see whether the interval crosses zero. Renders nothing numeric when absent.
 */
export function IntervalChart({
  label,
  unit,
  point,
  low,
  high,
  money = false,
  predicted,
}: {
  label: string;
  unit: string;
  point: number | null;
  low: number | null;
  high: number | null;
  money?: boolean;
  predicted?: { value: number; label: string };
}) {
  if (point === null || low === null || high === null) {
    return (
      <p className="text-sm text-ink-2" data-testid="no-interval">
        No interval for {label.toLowerCase()}: the backend did not return one.
      </p>
    );
  }
  const fmt = (v: number) => (money ? inr(v, true) : num(v, 1));
  const vals = [low, high, 0, point, ...(predicted ? [predicted.value] : [])];
  let lo = Math.min(...vals);
  let hi = Math.max(...vals);
  const pad = (hi - lo || 1) * 0.12;
  lo -= pad;
  hi += pad;
  const W = 560;
  const H = 76;
  const x = (v: number) => 12 + ((v - lo) / (hi - lo)) * (W - 24);
  const crossesZero = low <= 0 && high >= 0;
  const zeroClash = Math.min(Math.abs(x(0) - x(low)), Math.abs(x(0) - x(high))) < 48;
  const predClash = predicted ? Math.abs(x(predicted.value) - x(0)) < 48 : false;
  const barColor = crossesZero ? "stroke-ink-2" : low > 0 ? "stroke-gain" : "stroke-loss";

  return (
    <figure>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`${label}: estimate ${fmt(point)} ${unit}, 95% interval ${fmt(low)} to ${fmt(high)}${crossesZero ? ", which includes zero" : ""}.`}
        className="h-auto w-full"
      >
        <line x1={x(0)} x2={x(0)} y1={6} y2={H - 20} className="stroke-ink-3" strokeDasharray="2 3" />
        <text
          x={x(0) + (zeroClash ? 4 : 0)}
          y={zeroClash ? 14 : H - 6}
          textAnchor={zeroClash ? "start" : "middle"}
          className={AXIS}
        >
          0
        </text>
        {predicted && (
          <g>
            <line x1={x(predicted.value)} x2={x(predicted.value)} y1={14} y2={H - 26} className="stroke-pred" strokeWidth={1.5} strokeDasharray="4 3" />
            <text x={x(predicted.value)} y={zeroClash && predClash ? 24 : 10} textAnchor="middle" className="fill-pred text-[10px]">
              {predicted.label}
            </text>
          </g>
        )}
        <line x1={x(low)} x2={x(high)} y1={36} y2={36} className={barColor} strokeWidth={4} strokeLinecap="round" />
        <circle cx={x(point)} cy={36} r={6.5} className="fill-surface stroke-ink" strokeWidth={2} />
        <text x={x(low)} y={H - 6} textAnchor="middle" className="fill-ink-2 text-[11px]">
          {fmt(low)}
        </text>
        <text x={x(high)} y={H - 6} textAnchor="middle" className="fill-ink-2 text-[11px]">
          {fmt(high)}
        </text>
      </svg>
      <figcaption className="text-[13px] text-ink-3">
        {label} ({unit}). Circle: estimate. Bar: 95% confidence interval. Dotted line: zero
        {predicted ? "; dashed amber line: prediction" : ""}.
        {crossesZero && <strong className="font-medium text-ink-2"> The interval includes zero.</strong>}
      </figcaption>
    </figure>
  );
}
