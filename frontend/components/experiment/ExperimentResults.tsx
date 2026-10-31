"use client";

import Link from "next/link";

import { getCampaign, getExperiment, type Experiment } from "@/lib/api";
import { dateTime, humanize, inr, inrSigned, num, pct, signed, splitReason, typeLabel } from "@/lib/format";
import { useApi } from "@/lib/useApi";

import { Breadcrumbs } from "../AppHeader";
import { IntervalChart } from "../charts";
import { ErrorBox, KindBlock, KindTag, Loading, Panel } from "../ui";
import { AssignmentsTable } from "./AssignmentsTable";

const EFFECT_TEXT = {
  positive: "The whole 95% interval for extra orders is above zero.",
  negative: "The whole 95% interval for extra orders is below zero: the offer lowered orders.",
  inconclusive: "The data cannot tell whether the offer changed orders.",
} as const;
const PROFIT_TEXT = {
  profitable: "The whole 95% interval for measured profit is above zero.",
  loss: "The whole 95% interval for measured profit is below zero.",
  inconclusive: "The data cannot tell whether the campaign made or lost money.",
} as const;
const VERDICT_TONE = {
  positive: "green",
  profitable: "green",
  negative: "red",
  loss: "red",
  inconclusive: "amber",
} as const;

const numOrNull = (v: string | null) => (v === null ? null : Number(v));
const roundRupee = (v: string | null) => (v === null ? null : Math.round(Number(v)));

export function ExperimentResults({ experimentId }: { experimentId: string }) {
  const s = useApi(`experiment:${experimentId}`, (signal) => getExperiment(experimentId, signal));
  if (s.kind === "loading") return <Loading what="experiment" />;
  if (s.kind === "error") return <ErrorBox error={s.error} onRetry={s.reload} />;
  return <ExperimentView x={s.data} />;
}

export function ExperimentView({ x }: { x: Experiment }) {
  const campaign = useApi(`campaign:${x.campaign_id}`, (signal) => getCampaign(x.campaign_id, signal));
  const c = campaign.kind === "ok" ? campaign.data : null;
  const inc = x.incremental;
  const mp = x.measured_profit;
  const expected = x.prediction.scenarios.find((p) => p.scenario === "expected");
  const byConstruction = x.simulation.lift_source === "version_expected_scenario";
  const excluded = Object.entries(x.assignment.excluded ?? {});
  const missingTotal = x.observed.treatment.missing_outcomes + x.observed.holdout.missing_outcomes;

  return (
    <>
      <Breadcrumbs
        items={[
          { href: "/", label: "Shops" },
          ...(c ? [{ href: `/merchants/${c.merchant_id}`, label: c.merchant_id }] : []),
          { href: `/campaigns/${x.campaign_id}`, label: "Campaign" },
          { label: "Experiment" },
        ]}
      />
      <div className="hero-sim animate-rise rounded-3xl p-6 text-white shadow-lift sm:p-8">
        <p className="chip bg-white/15 text-white ring-1 ring-white/25">Test run results</p>
        <h1 className="mt-3 text-[26px] font-extrabold leading-tight tracking-tight sm:text-[32px]">
          {c
            ? (c.current.inputs.promotion?.label ?? `${typeLabel(c.campaign_type)}, ₹${c.current.inputs.discount_inr} off`)
            : "Simulated experiment"}: did it pay?
        </h1>
        <p className="mt-2 text-white/85">
          Version {x.campaign_version}, run in simulation on {dateTime(x.completed_at)}. {num(x.assignment.treatment)} customers got
          the offer; {num(x.assignment.holdout)} did not.
        </p>
      </div>

      <section aria-label="Verdicts" className="card grid gap-x-10 gap-y-8 p-5 sm:p-6 md:grid-cols-2">
        <Verdict
          title="Extra orders caused by the offer"
          verdict={x.effect_verdict}
          figure={signed(inc.estimated_incremental_orders, 0)}
          interval={
            inc.incremental_orders_ci_low === null || inc.incremental_orders_ci_high === null
              ? null
              : `${signed(inc.incremental_orders_ci_low, 0)} to ${signed(inc.incremental_orders_ci_high, 0)}`
          }
          text={EFFECT_TEXT[x.effect_verdict]}
          reasons={inc.reasons}
          testId="effect-verdict"
        />
        <Verdict
          title="Profit the campaign added"
          verdict={x.profit_verdict}
          figure={inrSigned(roundRupee(mp.measured_incremental_profit_inr))}
          interval={mp.ci_low_inr === null || mp.ci_high_inr === null ? null : `${inrSigned(roundRupee(mp.ci_low_inr))} to ${inrSigned(roundRupee(mp.ci_high_inr))}`}
          text={PROFIT_TEXT[x.profit_verdict]}
          reasons={[]}
          testId="profit-verdict"
          predicted={expected ? `Predicted ${inrSigned(expected.incremental_contribution_profit_inr)} in the expected case.` : undefined}
        />
      </section>

      <div role="note" data-testid="sim-banner" className="kind-hatch rounded-r-xl border-l-4 border-sim bg-sim-wash px-4 py-3 text-sm">
        <p className="font-semibold text-sim">Simulation, not real-world evidence</p>
        <p className="mt-1 text-ink-2">{x.notice}</p>
        <p className="mt-1 text-[13px] text-ink-3">
          Mode: {x.mode}. Simulated: {x.is_simulated ? "yes" : "no"}. Data source: {x.data_source}. Experiment{" "}
          <code className="break-all">{x.experiment_id}</code>.
        </p>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <KindBlock kind="prediction" title="Prediction" tag="Stored forecast" testId="block-prediction">
          <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">Predicted profit by scenario</caption>
            <thead>
              <tr className="text-left text-ink-3">
                <th scope="col" className="font-medium">Scenario</th>
                <th scope="col" className="px-1 text-right font-medium">Extra orders</th>
                <th scope="col" className="px-1 text-right font-medium">Profit change</th>
              </tr>
            </thead>
            <tbody>
              {x.prediction.scenarios.map((p) => (
                <tr key={p.scenario} className="border-t border-rule">
                  <th scope="row" className="py-1 text-left font-medium capitalize">{p.scenario}</th>
                  <td className="py-1 text-right">{num(p.incremental_orders)}</td>
                  <td className="py-1 text-right font-semibold">{inrSigned(p.incremental_contribution_profit_inr)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
          <p className="mt-2 text-[13px] text-ink-3">
            From the version&apos;s stored evaluation ({humanize(x.prediction.evaluation_trigger)},{" "}
            {dateTime(x.prediction.evaluated_at)}). Never changed by the experiment. Break-even:{" "}
            {x.prediction.break_even_incremental_orders ?? "not available"} extra orders.
          </p>
        </KindBlock>

        <KindBlock kind="simulated" title="What happened" tag="Simulated, synthetic" testId="block-observed">
          <div className="overflow-x-auto">
          <table className="w-full min-w-[30rem] text-sm">
            <caption className="sr-only">Simulated outcomes by group</caption>
            <thead>
              <tr className="text-left text-ink-3">
                <th scope="col" className="font-medium">Group</th>
                <th scope="col" className="px-1 text-right font-medium">Assigned</th>
                <th scope="col" className="px-1 text-right font-medium">Measured</th>
                <th scope="col" className="px-1 text-right font-medium">Missing</th>
                <th scope="col" className="px-1 text-right font-medium">Orders</th>
                <th scope="col" className="px-1 text-right font-medium">Orders per customer</th>
                <th scope="col" className="px-1 text-right font-medium">Redeemed</th>
              </tr>
            </thead>
            <tbody>
              {(["treatment", "holdout"] as const).map((g) => {
                const a = x.observed[g];
                return (
                  <tr key={g} className="border-t border-rule">
                    <th scope="row" className="py-1 text-left font-medium">{g === "treatment" ? "Got the offer" : "Holdout (no offer)"}</th>
                    <td className="py-1 text-right">{num(a.assigned)}</td>
                    <td className="py-1 text-right">{num(a.measured)}</td>
                    <td className="py-1 text-right" data-testid={`missing-${g}`}>{num(a.missing_outcomes)}</td>
                    <td className="py-1 text-right">{num(a.orders)}</td>
                    <td className="py-1 text-right">
                      {a.orders_per_measured_customer === null ? "no data" : num(a.orders_per_measured_customer, 3)}
                    </td>
                    <td className="py-1 text-right">{num(a.redemptions)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
          <p className="mt-2 text-[13px] text-ink-3">
            Orders per customer are per measured customer over the campaign window. Missing
            outcomes are left missing, not counted as zero.
          </p>
        </KindBlock>

        <KindBlock kind="estimate" title="Effect of the offer" tag="Estimate with uncertainty" testId="block-incremental">
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <dt className="text-ink-3">Extra orders (estimate)</dt>
              <dd className="text-xl font-semibold">{signed(inc.estimated_incremental_orders, 1)}</dd>
            </div>
            <div>
              <dt className="text-ink-3">95% interval</dt>
              <dd className="text-xl font-semibold" data-testid="orders-ci">
                {inc.incremental_orders_ci_low === null || inc.incremental_orders_ci_high === null
                  ? "not available"
                  : (<><span className="whitespace-nowrap">{signed(inc.incremental_orders_ci_low, 1)}</span> to <span className="whitespace-nowrap">{signed(inc.incremental_orders_ci_high, 1)}</span></>)}
              </dd>
            </div>
            <div>
              <dt className="text-ink-3">Per customer difference</dt>
              <dd className="font-medium">
                {signed(inc.per_customer_difference, 4)}
                {inc.ci_low !== null && inc.ci_high !== null && ` (${signed(inc.ci_low, 4)} to ${signed(inc.ci_high, 4)})`}
              </dd>
            </div>
            <div>
              <dt className="text-ink-3">Sample sizes</dt>
              <dd className="font-medium">
                {num(x.observed.treatment.measured)} offer vs {num(x.observed.holdout.measured)} holdout measured
              </dd>
            </div>
          </dl>
          <div className="mt-3">
            <IntervalChart
              label="Extra orders"
              unit="orders over the campaign window"
              point={inc.estimated_incremental_orders}
              low={inc.incremental_orders_ci_low}
              high={inc.incremental_orders_ci_high}
              predicted={expected ? { value: expected.incremental_orders, label: "predicted" } : undefined}
            />
          </div>
          <p className="mt-2 text-[13px] text-ink-3">
            Method: {humanize(inc.method)} ({pct(inc.confidence_level, 0)} confidence). Primary metric: {inc.primary_metric}.
            Share assigned to the offer: {pct(inc.assignment_proportion_treatment, 1)}.
            {inc.standard_error !== null && ` Standard error ${num(inc.standard_error, 4)}, df ${num(inc.degrees_of_freedom, 1)}, p = ${inc.p_value === null ? "—" : num(inc.p_value, 3)}.`}
          </p>
        </KindBlock>

        <KindBlock kind="simulated" title="Profit, measured" tag="From simulated outcomes" testId="block-profit">
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <dt className="text-ink-3">Measured profit change</dt>
              <dd className="text-xl font-semibold">{inrSigned(mp.measured_incremental_profit_inr, true)}</dd>
            </div>
            <div>
              <dt className="text-ink-3">95% interval</dt>
              <dd className="text-xl font-semibold" data-testid="profit-ci">
                {mp.ci_low_inr === null || mp.ci_high_inr === null
                  ? "not available"
                  : (<><span className="whitespace-nowrap">{inrSigned(mp.ci_low_inr, true)}</span> to <span className="whitespace-nowrap">{inrSigned(mp.ci_high_inr, true)}</span></>)}
              </dd>
            </div>
          </dl>
          <div className="mt-3">
            <IntervalChart
              label="Measured profit"
              unit="₹"
              money
              point={numOrNull(mp.measured_incremental_profit_inr)}
              low={numOrNull(mp.ci_low_inr)}
              high={numOrNull(mp.ci_high_inr)}
              predicted={expected ? { value: expected.incremental_contribution_profit_inr, label: "predicted" } : undefined}
            />
          </div>
          <p className="mt-2 text-[13px] text-ink-3">
            Formula: {mp.formula}. Inputs: order value {inr(mp.inputs.average_order_value_inr)}, variable cost{" "}
            {inr(mp.inputs.variable_cost_per_order_inr)}, discount {inr(mp.inputs.discount_inr)}, campaign cost{" "}
            {inr(mp.inputs.campaign_cost_inr)}, redemptions R = {num(mp.inputs.treatment_redemptions_R)}.
          </p>
          <p className="mt-1 text-[13px] font-medium text-ink-2" data-testid="profit-interval-caveat">
            This interval treats redemptions as fixed, so it does not include every source of uncertainty.
          </p>
        </KindBlock>
      </div>

      <Panel title="Caveats from the experiment engine" id="caveats">
        <ul className="flex flex-col gap-2 text-sm" data-testid="caveats">
          {x.caveats.map((cv) => {
            const key = /by construction/i.test(cv) || /not observations of real/i.test(cv);
            return (
              <li key={cv} className={`border-l-2 pl-3 ${key ? "border-sim font-medium" : "border-rule"}`}>
                {cv}
              </li>
            );
          })}
        </ul>
        {byConstruction && !x.caveats.some((cv) => /by construction/i.test(cv)) && (
          <p className="mt-3 border-l-2 border-pred bg-pred-wash px-4 py-2 text-sm" data-testid="by-construction">
            The simulated true effect was taken from this campaign&apos;s own expected scenario, so
            agreement between prediction and measurement is by construction. It checks the
            measurement pipeline, not the prediction.
          </p>
        )}
        {missingTotal > 0 && (
          <p className="mt-3 text-sm text-ink-2">
            {num(missingTotal)} outcome(s) are missing. Estimates assume they are missing at random.
          </p>
        )}
      </Panel>

      <>
        <Panel title="Who was in the experiment" id="assignment">
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <dt className="text-ink-3">Eligible and assigned</dt>
              <dd className="font-medium">{num(x.assignment.eligible)}</dd>
            </div>
            <div>
              <dt className="text-ink-3">Offer / holdout</dt>
              <dd className="font-medium">
                {num(x.assignment.treatment)} / {num(x.assignment.holdout)} ({x.assignment.holdout_pct}% holdout)
              </dd>
            </div>
          </dl>
          {x.assignment.rule && <p className="mt-2 text-sm">Eligibility: {x.assignment.rule}</p>}
          <div className="mt-3">
            <p className="text-sm font-medium">Not included</p>
            {excluded.length === 0 ? (
              <p className="text-sm text-ink-3">No records were excluded.</p>
            ) : (
              <ul className="list-disc pl-5 text-sm" data-testid="excluded">
                {excluded.map(([k, v]) => (
                  <li key={k}>
                    {humanize(k)}: {num(v)}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <p className="mt-3 text-[13px] text-ink-3">
            Assignment method: {humanize(x.assignment.method)}. Seed{" "}
            <code className="break-all">{x.assignment.seed.slice(0, 16)}…</code>. Holdout customers never receive the offer.
          </p>
        </Panel>
        <Panel title="Simulation settings" id="simulation">
          <p className="text-sm text-ink-2">{x.simulation.label}</p>
          <dl className="mt-2 grid max-w-xl grid-cols-1 gap-1 text-sm">
            {Object.entries(x.simulation)
              .filter(([k]) => k !== "label")
              .map(([k, v]) => (
                <div key={k} className="flex flex-wrap justify-between gap-2 border-b border-rule py-1">
                  <dt className="text-ink-3">{humanize(k)}</dt>
                  <dd className="font-medium">{v === null ? "—" : typeof v === "number" ? (Number.isInteger(v) ? num(v) : num(v, 4)) : String(v)}</dd>
                </div>
              ))}
          </dl>
          <p className="mt-2 text-[13px] text-ink-3">
            Approved by {x.approval.decided_by} on {dateTime(x.approval.decided_at)}. Requested by {x.requested_by}.
          </p>
        </Panel>
      </>

      <Panel title="Customers and simulated outcomes" id="customers" action={<KindTag kind="simulated" />}>
        <AssignmentsTable experimentId={x.experiment_id} />
      </Panel>
      <p className="text-sm">
        <Link href={`/campaigns/${x.campaign_id}`} className="link">
          Back to the campaign
        </Link>
      </p>
    </>
  );
}

function Verdict({
  title,
  verdict,
  figure,
  interval,
  text,
  reasons,
  testId,
  predicted,
}: {
  title: string;
  verdict: keyof typeof VERDICT_TONE;
  figure: string;
  interval: string | null;
  text: string;
  reasons: string[];
  testId: string;
  predicted?: string;
}) {
  const tone = VERDICT_TONE[verdict];
  const color = tone === "green" ? "text-gain" : tone === "red" ? "text-loss" : "text-pred";
  return (
    <div data-testid={testId} className="min-w-0">
      <h2 className="label">{title}</h2>
      <p className="mt-1 flex flex-wrap items-baseline gap-x-3">
        <span className="figure text-[40px] leading-none">{figure}</span>
        <span className={`text-lg font-semibold ${color}`} data-testid={`${testId}-value`}>
          {verdict}
        </span>
      </p>
      <p className="mt-2 text-sm text-ink-2">
        95% interval: <span className="font-medium text-ink">{interval ?? "not available"}</span>
      </p>
      <p className="mt-2 text-sm">{text}</p>
      {predicted && <p className="mt-1 text-[13px] text-pred">{predicted}</p>}
      {reasons.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-sm text-ink-2">
          {reasons.map((r) => {
            const { code, text: t } = splitReason(r);
            return (
              <li key={r}>
                {t}
                {code && <span className="text-[13px] text-ink-3"> ({code})</span>}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
