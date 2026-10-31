import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ExperimentView } from "@/components/experiment/ExperimentResults";
import type { Experiment } from "@/lib/api";

import { fx, json, mockFetch } from "./helpers";

function show(x: Experiment) {
  // ExperimentView loads its campaign and assignment page; give it real fixtures.
  mockFetch((c) =>
    c.url.includes("/assignments")
      ? json(200, { experiment_id: x.experiment_id, total: 0, items: [], is_simulated: true })
      : json(200, fx.simulated()),
  );
  render(<ExperimentView x={x} />);
}

describe("experiment results", () => {
  it("keeps the backend's inconclusive verdict even when the point estimate is positive", () => {
    const x = fx.expInconclusive();
    expect(x.incremental.estimated_incremental_orders!).toBeGreaterThan(0);
    expect(x.incremental.incremental_orders_ci_low!).toBeLessThan(0);
    show(x);
    expect(screen.getByTestId("effect-verdict-value")).toHaveTextContent(/^inconclusive$/);
    expect(screen.getByTestId("effect-verdict")).toHaveTextContent("cannot rule out no effect");
    expect(screen.getByTestId("orders-ci")).toHaveTextContent(/−\d.* to \+\d/);
    expect(screen.getAllByText(/The interval includes zero/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/^positive$/)).toBeNull();
  });

  it("shows a small-sample result as inconclusive even when its interval excludes zero", () => {
    const x = fx.expSmall();
    expect(x.incremental.ci_low!).toBeGreaterThan(0);
    show(x);
    expect(screen.getByTestId("effect-verdict-value")).toHaveTextContent("inconclusive");
    expect(screen.getByTestId("effect-verdict")).toHaveTextContent(/fewer than 30 measured customers/);
  });

  it("separates prediction, observed, incremental effect and measured profit", () => {
    const x = fx.expDefault();
    show(x);
    const kinds = ["block-prediction", "block-observed", "block-incremental", "block-profit"].map(
      (id) => screen.getByTestId(id).getAttribute("data-kind"),
    );
    expect(kinds).toEqual(["prediction", "simulated", "estimate", "simulated"]);
    const pred = screen.getByTestId("block-prediction");
    const profit = screen.getByTestId("block-profit");
    expect(pred).toHaveTextContent("+₹2,400"); // stored forecast
    expect(profit).not.toHaveTextContent("+₹2,400");
    expect(profit).toHaveTextContent(x.measured_profit.measured_incremental_profit_inr!.split(".")[0].slice(-3));
  });

  it("shows the simulation, by-construction and profit-interval caveats", () => {
    show(fx.expDefault());
    expect(screen.getByTestId("sim-banner")).toHaveTextContent("Simulation, not real-world evidence");
    const caveats = screen.getByTestId("caveats");
    expect(caveats).toHaveTextContent("not observations of real customers");
    expect(caveats).toHaveTextContent("by construction");
    expect(caveats).toHaveTextContent("redemptions are treated as fixed");
    expect(screen.getByTestId("profit-interval-caveat")).toHaveTextContent("treats redemptions as fixed");
  });

  it("shows missing-outcome counts and the missing-at-random assumption, and exclusions", () => {
    const x = fx.expNullMissing();
    show(x);
    expect(screen.getByTestId("missing-treatment")).toHaveTextContent(String(x.observed.treatment.missing_outcomes));
    expect(screen.getByTestId("missing-holdout")).toHaveTextContent(String(x.observed.holdout.missing_outcomes));
    expect(screen.getByTestId("caveats")).toHaveTextContent(/missing at random/);
    expect(screen.getByTestId("excluded")).toHaveTextContent("anonymous orders without customer id last 28 days: 774");
  });

  it("does not turn missing intervals or per-customer rates into zeros", () => {
    const x = fx.expDefault();
    x.observed.holdout = { ...x.observed.holdout, measured: 0, missing_outcomes: x.observed.holdout.assigned, orders: 0, orders_per_measured_customer: null };
    x.incremental = { ...x.incremental, ci_low: null, ci_high: null, incremental_orders_ci_low: null, incremental_orders_ci_high: null, standard_error: null, reasons: ["insufficient_measured_outcomes: fewer than 2 measured customers in an arm"] };
    x.effect_verdict = "inconclusive";
    x.measured_profit = { ...x.measured_profit, ci_low_inr: null, ci_high_inr: null };
    x.profit_verdict = "inconclusive";
    show(x);
    expect(screen.getByTestId("orders-ci")).toHaveTextContent("not available");
    expect(screen.getByTestId("profit-ci")).toHaveTextContent("not available");
    expect(screen.getAllByTestId("no-interval").length).toBe(2);
    const observed = screen.getByTestId("block-observed");
    expect(within(observed).getByText("no data")).toBeInTheDocument();
    expect(screen.getByTestId("effect-verdict")).toHaveTextContent("fewer than 2 measured customers");
  });
});
