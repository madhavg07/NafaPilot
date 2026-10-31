import { describe, expect, it } from "vitest";

import { ApiError, getCampaign, newIdempotencyKey, runSimulation } from "@/lib/api";

import { fx, json, mockFetch } from "./helpers";

describe("API client", () => {
  it("parses structured backend errors with code, message and details", async () => {
    mockFetch(() => json(422, fx.refusalInsufficient));
    const err = await runSimulation("c1", 1, "sim-key-1").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    const e = err as ApiError;
    expect(e.status).toBe(422);
    expect(e.code).toBe("insufficient_data");
    expect(e.details).toMatchObject({ treatment: 80, holdout: 20 });
    expect(e.retryable).toBe(false);
  });

  it("parses FastAPI validation errors", async () => {
    mockFetch(() => json(422, { detail: [{ loc: ["header", "idempotency-key"], msg: "Field required" }] }));
    const e = (await getCampaign("x").catch((x: unknown) => x)) as ApiError;
    expect(e.code).toBe("validation_error");
    expect(e.message).toContain("idempotency-key");
  });

  it("reports an unreachable server as a retryable network error, not a success", async () => {
    mockFetch(() => {
      throw new TypeError("Failed to fetch");
    });
    const e = (await getCampaign("x").catch((x: unknown) => x)) as ApiError;
    expect(e.status).toBe(0);
    expect(e.code).toBe("network_error");
    expect(e.retryable).toBe(true);
  });

  it("treats 5xx as retryable", async () => {
    mockFetch(() => new Response("boom", { status: 503 }));
    const e = (await getCampaign("x").catch((x: unknown) => x)) as ApiError;
    expect(e.status).toBe(503);
    expect(e.retryable).toBe(true);
  });

  it("sends the Idempotency-Key header and the simulation body to the M8 endpoint", async () => {
    const calls = mockFetch(() => json(201, fx.expDefault()));
    await runSimulation("camp-1", 3, "sim-abc-123", { base_rate_per_customer: "0.5" });
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("http://api.test/api/v1/campaigns/camp-1/versions/3/execute");
    expect(calls[0].method).toBe("POST");
    expect(calls[0].headers["Idempotency-Key"]).toBe("sim-abc-123");
    expect(calls[0].body).toEqual({ requested_by: "merchant", simulation: { base_rate_per_customer: "0.5" } });
  });

  it("creates distinct keys long enough for the backend (8-128 chars)", () => {
    const a = newIdempotencyKey();
    const b = newIdempotencyKey();
    expect(a).not.toBe(b);
    expect(a.length).toBeGreaterThanOrEqual(8);
    expect(a.length).toBeLessThanOrEqual(128);
  });
});
