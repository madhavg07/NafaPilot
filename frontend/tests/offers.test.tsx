import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ProductSelector } from "@/components/merchant/ProductSelector";
import { OfferComparison, OfferFacts } from "@/components/recommendation/OfferParts";
import type { CandidateOut, CatalogueOut, PromotionSpec } from "@/lib/api";

import { json, mockFetch } from "./helpers";

const stats = (units: number, prev: number) => ({
  units_28d: units,
  orders_28d: units,
  revenue_28d_inr: units * 100,
  contribution_28d_inr: units * 50,
  units_prev_28d: prev,
  trend_pct: prev ? ((units - prev) / prev) * 100 : null,
  share_of_orders: 0.1,
});

const catalogue: CatalogueOut = {
  merchant_id: "M001",
  period_start: "2026-09-28",
  period_end: "2026-10-25",
  products: [
    { sku: "CF001", name: "Cappuccino", category: "coffee", price_inr: 180, unit_cost_inr: 84, margin_inr: 96, margin_pct: 53.3, selected: true, stats: stats(400, 500) },
    { sku: "SW001", name: "Veg sandwich", category: "snacks", price_inr: 150, unit_cost_inr: 105, margin_inr: 45, margin_pct: 30, selected: true, stats: stats(300, 290) },
    { sku: "BK001", name: "Veg burger", category: "meals", price_inr: 220, unit_cost_inr: 136, margin_inr: 84, margin_pct: 38.2, selected: false, stats: stats(280, 260) },
  ],
  pairs: [{ sku_a: "SW001", sku_b: "CF001", name_a: "Veg sandwich", name_b: "Cappuccino", orders_together_28d: 191, confidence_a_to_b: 0.376, lift: 2.27 }],
  basket: { orders_28d: 3188, average_value_inr: 206.67, median_value_inr: 200, p75_value_inr: 250, average_items: 1.76 },
  selection_source: "merchant",
  selection_version: 1,
  selected_skus: ["CF001", "SW001"],
  is_synthetic: true,
  data_source: "synthetic_csv",
  notice: "",
};

describe("product selection", () => {
  it("shows margins, trends and pairs, and saves the chosen products", async () => {
    const calls = mockFetch(() => json(200, catalogue));
    const onSaved = vi.fn();
    render(<ProductSelector merchantId="M001" catalogue={catalogue} onSaved={onSaved} />);

    expect(screen.getByTestId("selected-count")).toHaveTextContent("2 of 3");
    expect(screen.getByText("▼ 20%")).toBeInTheDocument(); // cappuccino 400 vs 500
    expect(within(screen.getByTestId("pairs")).getByText("Veg sandwich + Cappuccino")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Save selection/ })).toBeDisabled();

    await userEvent.click(screen.getByRole("checkbox", { name: "Use Veg burger in offers" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Use Veg sandwich in offers" }));
    expect(screen.getByTestId("selected-count")).toHaveTextContent("2 of 3");
    await userEvent.click(screen.getByRole("button", { name: /Save selection/ }));

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(calls[0].method).toBe("PUT");
    expect(calls[0].url).toBe("http://api.test/api/v1/merchants/M001/products/selection");
    expect((calls[0].body as { skus: string[] }).skus.sort()).toEqual(["BK001", "CF001"]);
  });
});

const bundle: PromotionSpec = {
  kind: "bundle",
  skus: ["CF001", "SW001"],
  discount_inr: 25,
  min_cart_value_inr: null,
  hours: null,
  weekdays_only: false,
  label: "Cappuccino + Veg sandwich: ₹25 off the combo",
  eligible_skus: ["CF001", "SW001"],
  list_price_inr: 330,
  item_cost_inr: 189,
  qualifying_orders_28d: 191,
  basket_basis: "Average of the 191 past orders…",
};

function cand(record: string, spec: PromotionSpec, expected: number, conservative: number, permitted: boolean): CandidateOut {
  const scen = (scenario: "conservative" | "expected" | "optimistic", v: number) => ({
    scenario,
    incremental_orders: 0,
    existing_discounted_orders: 0,
    incremental_net_revenue_inr: 0,
    incremental_variable_cost_inr: 0,
    campaign_cost_inr: 0,
    incremental_contribution_profit_inr: v,
    discount_cost_inr: 0,
    break_even_incremental_orders: null,
    explanation: [],
  });
  return {
    record_id: record,
    status: "evaluated",
    campaign_type: "bundle_offer",
    discount_inr: spec.discount_inr,
    target_group: "all_customers",
    llm_rationale: "",
    filter_reasons: [],
    cost_source: "request",
    campaign_id: `c-${record}`,
    input_hash: "x",
    economics: { scenarios: [scen("conservative", conservative), scen("expected", expected), scen("optimistic", expected * 2)] } as unknown as CandidateOut["economics"],
    policy: { permitted } as CandidateOut["policy"],
    promotion: spec,
    notes: [],
  };
}

describe("offer comparison", () => {
  it("lists every offer with likely and bad-case profit and the limit check", () => {
    const cart: PromotionSpec = { ...bundle, kind: "cart", skus: [], label: "Cart: ₹20 off on eligible items worth ₹300 or more", min_cart_value_inr: 300, list_price_inr: 300, discount_inr: 20 };
    render(<OfferComparison candidates={[cand("A", bundle, 1805, -2825, false), cand("B", cart, 900, 120, true)]} selected="B" />);
    const rows = within(screen.getByTestId("offer-comparison")).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("Cappuccino + Veg sandwich: ₹25 off the combo");
    expect(rows[1]).toHaveTextContent("+₹1,805");
    expect(rows[1]).toHaveTextContent("−₹2,825");
    expect(rows[1]).toHaveTextContent("Blocked");
    expect(rows[2]).toHaveTextContent("Pass · recommended");
  });

  it("shows the facts code used, including orders that already qualify", () => {
    render(<OfferFacts p={bundle} notes={["The AI's estimate of extra orders was capped at 392."]} />);
    const f = screen.getByTestId("offer-facts");
    expect(f).toHaveTextContent("Combo list price₹330");
    expect(f).toHaveTextContent("₹305");
    expect(f).toHaveTextContent("191 orders");
    expect(f).toHaveTextContent("capped at 392");
  });
});
