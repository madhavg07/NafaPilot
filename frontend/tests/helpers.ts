// Fixtures in tests/fixtures are real responses captured from the running M8 backend
// (synthetic data), not hand-written values.
import { vi } from "vitest";

import approved from "./fixtures/campaign_approved.json";
import blocked from "./fixtures/campaign_blocked.json";
import pending from "./fixtures/campaign_pending.json";
import simulated from "./fixtures/campaign_simulated.json";
import expDefault from "./fixtures/experiment_default.json";
import expInconclusive from "./fixtures/experiment_inconclusive.json";
import expNullMissing from "./fixtures/experiment_null_missing.json";
import expSmall from "./fixtures/experiment_small_sample.json";
import merchant from "./fixtures/merchant_M001.json";
import refusalAlready from "./fixtures/refusal_already_executed.json";
import refusalInsufficient from "./fixtures/refusal_insufficient.json";
import refusalNotApproved from "./fixtures/refusal_not_approved.json";

import type { CampaignOut, Experiment, MerchantProfile } from "@/lib/api";

const clone = <T,>(o: unknown) => structuredClone(o) as T;

export const fx = {
  approved: () => clone<CampaignOut>(approved),
  blocked: () => clone<CampaignOut>(blocked),
  pending: () => clone<CampaignOut>(pending),
  simulated: () => clone<CampaignOut>(simulated),
  expDefault: () => clone<Experiment>(expDefault),
  expInconclusive: () => clone<Experiment>(expInconclusive),
  expNullMissing: () => clone<Experiment>(expNullMissing),
  expSmall: () => clone<Experiment>(expSmall),
  merchant: () => clone<MerchantProfile>(merchant),
  refusalAlready,
  refusalInsufficient,
  refusalNotApproved,
};

export function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };

/** Stub fetch with a handler; records every call. */
export function mockFetch(handler: (c: Call) => Response | Promise<Response>) {
  const calls: Call[] = [];
  const fn = vi.fn(async (url: string, init: RequestInit = {}) => {
    const c: Call = {
      url,
      method: init.method ?? "GET",
      headers: (init.headers ?? {}) as Record<string, string>,
      body: init.body ? JSON.parse(String(init.body)) : undefined,
    };
    calls.push(c);
    return handler(c);
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

export function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
