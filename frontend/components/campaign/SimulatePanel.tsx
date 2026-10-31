"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";

import {
  ApiError,
  newIdempotencyKey,
  runSimulation,
  type CampaignOut,
  type Experiment,
  type MerchantProfile,
  type SimulationOverrides,
} from "@/lib/api";
import { inrSigned, num, typeLabel } from "@/lib/format";
import { toApiError } from "@/lib/useApi";

import { ErrorBox, KindTag } from "../ui";

type Overrides = { lift: string; baseRate: string; missing: string; maxCustomers: string };
const EMPTY: Overrides = { lift: "", baseRate: "", missing: "", maxCustomers: "" };

function toBody(o: Overrides): { body: SimulationOverrides; invalid: string[] } {
  const invalid: string[] = [];
  const body: SimulationOverrides = {};
  const dec = (v: string, name: string, re = /^-?\d+(\.\d+)?$/) => {
    if (v === "") return undefined;
    if (!re.test(v)) invalid.push(name);
    return v;
  };
  body.true_lift_per_treated_customer = dec(o.lift, "true lift");
  body.base_rate_per_customer = dec(o.baseRate, "base order rate", /^\d+(\.\d+)?$/);
  body.missing_outcome_share = dec(o.missing, "missing share", /^(0(\.\d+)?|\.\d+)$/);
  if (o.maxCustomers !== "") {
    if (/^\d+$/.test(o.maxCustomers)) body.max_customers = Number(o.maxCustomers);
    else invalid.push("customer cap");
  }
  for (const k of Object.keys(body) as (keyof SimulationOverrides)[]) if (body[k] === undefined) delete body[k];
  return { body, invalid };
}

/**
 * The only place that calls the M8 execution endpoint.
 * - A fresh Idempotency-Key is created when the user confirms a NEW run.
 * - "Retry" after a network or server error re-sends the SAME key (same logical request),
 *   so a run that actually completed is returned instead of duplicated.
 * - A definitive refusal (4xx) or a change of assumptions discards the key.
 * - Success is shown only after the server returns the experiment.
 */
export function SimulatePanel({
  c,
  merchant,
  onCompleted,
}: {
  c: CampaignOut;
  merchant: MerchantProfile | null;
  onCompleted: (e: Experiment) => void;
}) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [overrides, setOverrides] = useState<Overrides>(EMPTY);
  const [done, setDone] = useState<Experiment | null>(null);
  const [canRetry, setCanRetry] = useState(false);
  const keyRef = useRef<string | null>(null);
  const inFlight = useRef(false);
  const titleId = useId();
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (open) headingRef.current?.focus();
  }, [open]);

  const approved = c.approval_status === "approved";
  const eligible = approved && c.permitted && c.execution_status === "not_executed";
  const reason =
    c.execution_status === "simulated"
      ? "This version has already been simulated."
      : !c.permitted
        ? "Blocked by policy: it cannot be approved or simulated."
        : c.approval_status === "rejected"
          ? "You rejected this version."
          : !approved
            ? "Approve the current version first. Approval alone never runs anything."
            : null;

  const { body, invalid } = toBody(overrides);
  const expected = c.current.evaluation.economics.scenarios.find((s) => s.scenario === "expected");
  const lowest = c.current.evaluation.economics.scenarios.find((s) => s.scenario === "conservative");
  const highest = c.current.evaluation.economics.scenarios.find((s) => s.scenario === "optimistic");

  function change(k: keyof Overrides, v: string) {
    setOverrides((o) => ({ ...o, [k]: v.trim() }));
    keyRef.current = null; // different assumptions = a different logical request
    setCanRetry(false);
  }

  async function run(isRetry: boolean) {
    if (inFlight.current || invalid.length) return;
    inFlight.current = true;
    if (!isRetry || keyRef.current === null) keyRef.current = newIdempotencyKey();
    setPending(true);
    setError(null);
    setCanRetry(false);
    try {
      const exp = await runSimulation(c.campaign_id, c.current_version, keyRef.current, body);
      keyRef.current = null;
      setDone(exp);
      onCompleted(exp);
    } catch (err) {
      const e = toApiError(err);
      if (!e.retryable) keyRef.current = null;
      setCanRetry(e.retryable);
      setError(e);
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }

  if (done) {
    return (
      <p role="status" className="text-sm font-medium text-ink" data-testid="sim-success">
        Simulation completed{done.replayed ? " (this returned the earlier result of the same request)" : ""}.{" "}
        <Link href={`/experiments/${done.experiment_id}`} className="link">
          Open the results
        </Link>
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {!open && (
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            className="btn-sim"
            disabled={!eligible}
            aria-describedby={reason ? `${titleId}-why` : undefined}
            onClick={() => setOpen(true)}
          >
            Run simulation…
          </button>
          {reason && (
            <p id={`${titleId}-why`} className="text-sm text-ink-2">
              {reason}
            </p>
          )}
        </div>
      )}

      {open && eligible && (
        <section
          role="group"
          aria-labelledby={titleId}
          data-testid="sim-confirm"
          className="max-w-3xl border-l-[3px] border-sim bg-sim-wash px-5 py-4"
        >
          <h3 id={titleId} className="font-semibold" tabIndex={-1} ref={headingRef}>
            Confirm simulation
          </h3>
          <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-ink-3">Merchant</dt>
              <dd className="font-medium">{merchant ? `${merchant.name} (${c.merchant_id})` : c.merchant_id}</dd>
            </div>
            <div>
              <dt className="text-ink-3">Campaign</dt>
              <dd className="font-medium">
                {typeLabel(c.campaign_type)}, ₹{c.current.inputs.discount_inr} off, version {c.current_version}
              </dd>
            </div>
            <div>
              <dt className="flex items-center gap-2 text-ink-3">
                Predicted profit <KindTag kind="prediction" />
              </dt>
              <dd className="font-medium">
                {expected ? `${inrSigned(expected.incremental_contribution_profit_inr)} expected` : "unavailable"}
                {lowest && highest &&
                  ` (range ${inrSigned(lowest.incremental_contribution_profit_inr)} to ${inrSigned(highest.incremental_contribution_profit_inr)})`}
              </dd>
            </div>
            <div>
              <dt className="text-ink-3">Holdout group</dt>
              <dd className="font-medium">
                {merchant?.policy ? `${num(merchant.policy.holdout_pct)}% of eligible customers get no offer` : "per merchant policy"}
              </dd>
            </div>
          </dl>
          <p className="mt-3 text-sm">
            <strong>This is a synthetic simulation only.</strong> No offer is sent, no customer is
            contacted and no payment system is called. Outcomes are generated from the
            synthetic dataset, so they are not evidence of real-world results. Each version can
            be simulated once.
          </p>

          <details className="mt-3 text-sm">
            <summary className="cursor-pointer font-medium">Simulation assumptions (optional)</summary>
            <p className="mt-1 text-[13px] text-ink-2">
              Leave blank to use the backend defaults: the true effect comes from this version&apos;s
              expected scenario and the base order rate from the merchant&apos;s past synthetic
              experiments. A merchant without past experiments needs a base rate.
            </p>
            <div className="mt-2 grid gap-3 sm:grid-cols-2">
              <label>
                <span className="font-medium">True extra orders per treated customer</span>
                <input className="field mt-1" inputMode="decimal" disabled={pending} value={overrides.lift} onChange={(e) => change("lift", e.target.value)} placeholder="e.g. 0.3 or -0.1" />
              </label>
              <label>
                <span className="font-medium">Base orders per customer (28 days)</span>
                <input className="field mt-1" inputMode="decimal" disabled={pending} value={overrides.baseRate} onChange={(e) => change("baseRate", e.target.value)} placeholder="e.g. 0.5" />
              </label>
              <label>
                <span className="font-medium">Share of outcomes missing</span>
                <input className="field mt-1" inputMode="decimal" disabled={pending} value={overrides.missing} onChange={(e) => change("missing", e.target.value)} placeholder="0 to 0.95" />
              </label>
              <label>
                <span className="font-medium">Cap on customers</span>
                <input className="field mt-1" inputMode="numeric" disabled={pending} value={overrides.maxCustomers} onChange={(e) => change("maxCustomers", e.target.value)} placeholder="all eligible" />
              </label>
            </div>
            {invalid.length > 0 && (
              <p className="mt-2 text-loss">Check: {invalid.join(", ")}.</p>
            )}
          </details>

          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              className="btn-sim"
              disabled={pending || invalid.length > 0}
              aria-busy={pending}
              onClick={() => run(false)}
            >
              {pending ? "Running simulation…" : "Confirm and run simulation"}
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={pending}
              onClick={() => {
                setOpen(false);
                setError(null);
                setCanRetry(false);
                keyRef.current = null;
              }}
            >
              Cancel
            </button>
          </div>
          {pending && (
            <p role="status" className="mt-2 text-sm text-ink-2">
              Waiting for the server. Nothing is shown as done until it confirms.
            </p>
          )}
        </section>
      )}

      {error && (
        <div className="flex flex-col gap-2">
          <ErrorBox
            error={error}
            onRetry={canRetry ? () => run(true) : undefined}
            retryLabel="Retry the same request"
          />
          {error.code === "already_executed" && typeof error.details?.experiment_id === "string" && (
            <Link href={`/experiments/${error.details.experiment_id}`} className="link text-sm">
              Open the existing experiment
            </Link>
          )}
        </div>
      )}
    </div>
  );
}
