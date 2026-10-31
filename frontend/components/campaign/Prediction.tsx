// Prediction (M5 profit engine) and policy (M5 policy engine) as returned by the API.

import type { CampaignEconomics, PolicyDecision } from "@/lib/api";
import { humanize, inr, inrSigned, num, sourceLabel } from "@/lib/format";
import { RULE_LABEL } from "@/lib/messages";

import { Badge, KindBlock } from "../ui";

const SCENARIO_LABEL = { conservative: "Conservative", expected: "Expected", optimistic: "Optimistic" };

export function PredictionBlock({ economics, title = "Predicted profit" }: { economics: CampaignEconomics; title?: string }) {
  return (
    <KindBlock kind="prediction" title={title} tag="Prediction, not a result" testId="prediction">
      {economics.status !== "ok" ? (
        <div className="text-sm">
          <p className="font-medium text-pred">
            No prediction: {economics.status === "insufficient_data" ? "some inputs are missing" : "some inputs are invalid"}.
          </p>
          <ul className="mt-1 list-disc pl-5 text-ink-2">
            {economics.issues.map((i) => (
              <li key={`${i.field}-${i.code}`}>
                {humanize(i.field)}: {i.message}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[28rem] text-sm">
              <caption className="sr-only">Predicted outcomes by scenario</caption>
              <thead>
                <tr className="border-b border-rule text-left text-[13px] text-ink-3">
                  <th scope="col" className="py-1.5 pr-3 font-normal">Case</th>
                  <th scope="col" className="py-1.5 pr-3 text-right font-normal">Extra orders</th>
                  <th scope="col" className="py-1.5 pr-3 text-right font-normal">
                    <abbr title="Orders that would have happened anyway but now get the discount" className="no-underline">
                      Discounted anyway
                    </abbr>
                  </th>
                  <th scope="col" className="py-1.5 pr-3 text-right font-normal">Profit change</th>
                  <th scope="col" className="py-1.5 text-right font-normal">Break-even</th>
                </tr>
              </thead>
              <tbody>
                {economics.scenarios.map((s) => (
                  <tr key={s.scenario} className="border-b border-rule last:border-0">
                    <th scope="row" className="py-2 pr-3 text-left font-normal text-ink-2">
                      {SCENARIO_LABEL[s.scenario]}
                    </th>
                    <td className="py-2 pr-3 text-right">{num(s.incremental_orders)}</td>
                    <td className="py-2 pr-3 text-right">{num(s.existing_discounted_orders)}</td>
                    <td className={`figure py-2 pr-3 text-right ${s.incremental_contribution_profit_inr < 0 ? "text-loss" : ""}`}>
                      {inrSigned(s.incremental_contribution_profit_inr)}
                    </td>
                    <td className="py-2 text-right text-ink-2">{s.break_even_incremental_orders ?? "not reachable"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[13px] text-ink-3">
            Each extra order earns {inr(economics.unit_margin_after_discount_inr)} after the discount and its cost. Break-even
            in the expected case:{" "}
            {economics.break_even_status === "reachable"
              ? `${economics.break_even_incremental_orders} extra orders`
              : humanize(economics.break_even_status)}
            . Order counts come from {sourceLabel(economics.assumption_source)}.
          </p>
          <details className="mt-1 text-[13px] text-ink-3">
            <summary className="cursor-pointer">How this is calculated</summary>
            <p className="mt-1">{economics.accounting_note}</p>
            {economics.scenarios
              .filter((s) => s.scenario === "expected")
              .map((s) => (
                <ul key={s.scenario} className="mt-1 list-disc pl-5">
                  {s.explanation.map((l) => (
                    <li key={l}>{l}</li>
                  ))}
                </ul>
              ))}
          </details>
        </>
      )}
    </KindBlock>
  );
}

export function PolicyBlock({ policy }: { policy: PolicyDecision }) {
  const policyBlocked = !policy.permitted;
  return (
    <section aria-label="Policy check" data-testid="policy" className="min-w-0 rounded-xl border border-rule bg-surface p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-bold">The merchant&apos;s limits</h3>
        {policyBlocked ? (
          <Badge tone="red">Blocked by policy</Badge>
        ) : policy.executable ? (
          <Badge tone="green">Within limits, approved</Badge>
        ) : (
          <Badge tone="green">Within limits</Badge>
        )}
      </div>
      {policyBlocked && (
        <p className="mt-2 border-l-2 border-loss pl-3 text-sm text-loss" data-testid="blocked-reasons">
          Blocked by: {policy.blocked_by.filter((r) => r !== "merchant_approval").map((r) => RULE_LABEL[r] ?? r).join(", ")}.
          Approval can&apos;t override a failed rule; the inputs have to change.
        </p>
      )}
      <ul className="mt-3 text-sm">
        {policy.rules.map((r) => {
          const state = r.passed ? "Passes" : r.blocks === "execution" ? "Waiting" : "Fails";
          const color = r.passed ? "bg-gain-wash text-gain" : r.blocks === "execution" ? "bg-pred-wash text-pred" : "bg-loss-wash text-loss";
          return (
            <li key={r.rule_id} className="grid grid-cols-[5rem_minmax(0,1fr)] gap-x-3 border-b border-rule py-2 last:border-0">
              <span className={`chip h-fit justify-center ${color}`}>{state}</span>
              <span>
                <span className="font-medium">{RULE_LABEL[r.rule_id] ?? r.rule_id}</span>
                <span className="block text-ink-2">{r.reason}</span>
              </span>
            </li>
          );
        })}
      </ul>
      <p className="mt-2 text-[13px] text-ink-3">
        Checked by code (policy version {policy.policy_version}; the loss limit uses the {policy.risk_scenario} case). The AI
        can&apos;t change these results.
      </p>
    </section>
  );
}
