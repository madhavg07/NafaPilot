import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { DecisionForm } from "@/components/campaign/DecisionForm";
import { PolicyBlock, PredictionBlock } from "@/components/campaign/Prediction";
import { SimulatePanel } from "@/components/campaign/SimulatePanel";
import { MerchantPicker } from "@/components/merchant/MerchantPicker";

import { deferred, fx, json, mockFetch } from "./helpers";

describe("loading and error states", () => {
  it("shows loading, then a plain-language error with the raw code, and retries", async () => {
    let fail = true;
    const calls = mockFetch(() =>
      fail
        ? json(503, { error_code: "dataset_missing", message: "Synthetic dataset not found" })
        : json(200, { data_source: "synthetic_csv", is_synthetic: true, merchants: [] }),
    );
    render(<MerchantPicker />);
    expect(screen.getByRole("status")).toHaveTextContent(/Loading merchants/);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Synthetic dataset not found");
    expect(alert).toHaveAttribute("data-error-code", "dataset_missing");
    fail = false;
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/No merchants in the dataset/)).toBeInTheDocument();
    expect(calls).toHaveLength(2);
  });
});

describe("policy-blocked and unapproved campaigns", () => {
  it("shows the blocking rule and its reason", () => {
    const c = fx.blocked();
    render(<PolicyBlock policy={c.current.evaluation.policy} />);
    expect(screen.getAllByText("Blocked by policy")[0]).toBeInTheDocument();
    expect(screen.getByTestId("blocked-reasons")).toHaveTextContent("Worst-case loss within limit");
    expect(screen.getByText(/possible loss exceeds the limit/)).toBeInTheDocument();
  });

  it("does not offer approval or simulation for a policy-blocked campaign", () => {
    const c = fx.blocked();
    mockFetch(() => {
      throw new Error("no request expected");
    });
    render(<DecisionForm c={c} onDecided={() => {}} />);
    expect(screen.getByRole("button", { name: /Approve version 1/ })).toBeDisabled();
    expect(screen.getByText(/Approval is not available: a policy rule fails/)).toBeInTheDocument();
    render(<SimulatePanel c={c} merchant={fx.merchant()} onCompleted={() => {}} />);
    expect(screen.getByRole("button", { name: "Run simulation…" })).toBeDisabled();
    expect(screen.getByText(/Blocked by policy: it cannot be approved or simulated/)).toBeInTheDocument();
  });

  it("does not offer simulation before approval", () => {
    render(<SimulatePanel c={fx.pending()} merchant={fx.merchant()} onCompleted={() => {}} />);
    expect(screen.getByRole("button", { name: "Run simulation…" })).toBeDisabled();
    expect(screen.getByText(/Approve the current version first/)).toBeInTheDocument();
  });

  it("labels the prediction as a prediction with all three scenarios and break-even", () => {
    render(<PredictionBlock economics={fx.pending().current.evaluation.economics} />);
    const block = screen.getByTestId("prediction");
    expect(block).toHaveAttribute("data-kind", "prediction");
    expect(block).toHaveTextContent("Prediction, not a result");
    for (const s of ["Conservative", "Expected", "Optimistic"]) expect(block).toHaveTextContent(s);
    expect(block).toHaveTextContent("+₹2,400");
    expect(block).toHaveTextContent("26 extra orders");
  });
});

describe("approval", () => {
  it("records the decision bound to the input hash and never calls the execution endpoint", async () => {
    const c = fx.pending();
    const calls = mockFetch(() => json(200, fx.approved()));
    let updated = null as unknown;
    render(<DecisionForm c={c} onDecided={(u) => (updated = u)} />);
    await userEvent.click(screen.getByRole("button", { name: "Approve version 1" }));
    await waitFor(() => expect(updated).not.toBeNull());
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toMatch(/\/versions\/1\/decision$/);
    expect(calls[0].body).toMatchObject({ decision: "approved", input_hash: c.current.input_hash });
    expect(calls.some((x) => x.url.includes("/execute"))).toBe(false);
    expect((updated as { execution_status: string }).execution_status).toBe("not_executed");
  });
});

describe("simulated execution", () => {
  async function openConfirm() {
    render(<SimulatePanel c={fx.approved()} merchant={fx.merchant()} onCompleted={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Run simulation…" }));
    return screen.getByTestId("sim-confirm");
  }

  it("asks for confirmation with merchant, version, prediction, holdout and synthetic-only notice", async () => {
    const calls = mockFetch(() => json(201, fx.expDefault()));
    const box = await openConfirm();
    expect(box).toHaveTextContent("Anand Café (M001)");
    expect(box).toHaveTextContent("version 1");
    expect(box).toHaveTextContent("+₹2,400 expected");
    expect(box).toHaveTextContent("20% of eligible customers get no offer");
    expect(box).toHaveTextContent("This is a synthetic simulation only.");
    expect(calls).toHaveLength(0); // opening the confirmation sends nothing
  });

  it("sends one request on a double click and shows success only after the server confirms", async () => {
    const d = deferred<Response>();
    const calls = mockFetch(() => d.promise);
    await openConfirm();
    const btn = screen.getByRole("button", { name: "Confirm and run simulation" });
    await userEvent.dblClick(btn);
    expect(calls).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Running simulation…" })).toBeDisabled();
    expect(screen.queryByTestId("sim-success")).toBeNull();
    await act(async () => d.resolve(json(201, fx.expDefault())));
    expect(await screen.findByTestId("sim-success")).toHaveTextContent("Simulation completed");
    expect(calls[0].headers["Idempotency-Key"]).toMatch(/^sim-/);
  });

  it("retries a failed request with the SAME key, and a new run after a refusal with a NEW key", async () => {
    const responses = [
      () => new Response("unavailable", { status: 503 }),
      () => json(409, fx.refusalNotApproved),
      () => json(201, fx.expDefault()),
    ];
    const calls = mockFetch(() => responses.shift()!());
    await openConfirm();
    await userEvent.click(screen.getByRole("button", { name: "Confirm and run simulation" }));
    const alert = await screen.findByRole("alert");
    expect(screen.queryByTestId("sim-success")).toBeNull();
    await userEvent.click(within(alert).getByRole("button", { name: "Retry the same request" }));
    const refusal = await screen.findByRole("alert");
    await waitFor(() => expect(refusal).toHaveAttribute("data-error-code", "not_approved"));
    expect(refusal).toHaveTextContent("This version has not been approved");
    expect(within(refusal).queryByRole("button", { name: /Retry/ })).toBeNull(); // refusals are final
    await userEvent.click(screen.getByRole("button", { name: "Confirm and run simulation" }));
    await screen.findByTestId("sim-success");
    const keys = calls.map((c) => c.headers["Idempotency-Key"]);
    expect(keys[0]).toBe(keys[1]);
    expect(keys[2]).not.toBe(keys[1]);
  });

  it.each([
    ["not_approved", fx.refusalNotApproved, 409, "This version has not been approved"],
    ["already_executed", fx.refusalAlready, 409, "Already simulated"],
    ["insufficient_data", fx.refusalInsufficient, 422, "Not enough data to run this"],
  ])("explains the %s refusal and keeps the technical details", async (code, body, status, title) => {
    mockFetch(() => json(status, body));
    await openConfirm();
    await userEvent.click(screen.getByRole("button", { name: "Confirm and run simulation" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveAttribute("data-error-code", code);
    expect(alert).toHaveTextContent(title);
    expect(alert).toHaveTextContent((body as { message: string }).message);
    expect(screen.queryByTestId("sim-success")).toBeNull();
    if (code === "already_executed") {
      expect(screen.getByRole("link", { name: "Open the existing experiment" })).toBeInTheDocument();
    }
  });

  it("sends optional simulation assumptions exactly as entered", async () => {
    const calls = mockFetch(() => json(201, fx.expDefault()));
    await openConfirm();
    await userEvent.click(screen.getByText("Simulation assumptions (optional)"));
    await userEvent.type(screen.getByLabelText("Base orders per customer (28 days)"), "0.5");
    await userEvent.click(screen.getByRole("button", { name: "Confirm and run simulation" }));
    await screen.findByTestId("sim-success");
    expect(calls[0].body).toEqual({ requested_by: "merchant", simulation: { base_rate_per_customer: "0.5" } });
  });
});
