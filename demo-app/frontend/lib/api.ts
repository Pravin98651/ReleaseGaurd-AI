import type {
  CheckoutResult,
  FlagEvaluation,
  HealthStatus,
  SimulationReport,
  TelemetryEventRecord,
} from "./types";

export async function fetchDemoHealth(baseUrl: string): Promise<HealthStatus> {
  const res = await fetch(`${baseUrl.replace(/\/+$/, "")}/api/health`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`Demo backend health check failed (${res.status})`);
  }
  return res.json();
}

export async function evaluateBackendFlag(
  baseUrl: string,
  userId: string,
): Promise<FlagEvaluation> {
  const url = new URL(`${baseUrl.replace(/\/+$/, "")}/api/flags`);
  url.searchParams.set("user_id", userId);
  const res = await fetch(url.toString(), { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`Flag evaluation failed (${res.status})`);
  }
  return res.json();
}

export async function submitCheckout(
  baseUrl: string,
  payload: {
    user_id: string;
    cart_total: number;
    payment_method: string;
    promo_code?: string;
    wallet_provider?: string;
    simulate_payment_bug?: boolean;
    abandon?: boolean;
    items?: Array<{ sku: string; name: string; price: number; quantity: number }>;
    shipping?: Record<string, string>;
  },
): Promise<CheckoutResult> {
  const res = await fetch(`${baseUrl.replace(/\/+$/, "")}/api/checkout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Checkout request failed (${res.status}): ${detail}`);
  }
  return res.json();
}

export async function runBackendSimulation(
  baseUrl: string,
  payload: {
    users_count: number;
    scenario: "healthy_v2" | "buggy_v2" | "neutral";
    split_mode?: "auto" | "flagsmith" | "simulated_50_50";
  },
): Promise<SimulationReport> {
  const res = await fetch(`${baseUrl.replace(/\/+$/, "")}/api/simulate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Simulation failed (${res.status}): ${detail}`);
  }
  return res.json();
}

export async function fetchRecentTelemetry(
  baseUrl: string,
  limit = 25,
): Promise<{
  events: TelemetryEventRecord[];
  total_buffered: number;
  releaseguard_api_url: string;
}> {
  const url = new URL(`${baseUrl.replace(/\/+$/, "")}/api/telemetry/recent`);
  url.searchParams.set("limit", String(limit));
  const res = await fetch(url.toString(), { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`Telemetry fetch failed (${res.status})`);
  }
  return res.json();
}

export interface NovaShopFeatureProduct {
  sku: string;
  name: string;
  category: string;
  tagline: string;
  price: number;
  score?: number;
  relevance?: number;
  recommendation_reason?: string;
  match_reason?: string;
}

export async function fetchRecommendations(
  baseUrl: string,
  params: {
    user_id: string;
    cart_skus?: string[];
    subscription_plan?: string;
    user_type?: string;
  },
): Promise<{
  flag_key: string;
  variation_key: string;
  kill_switch_enabled: boolean;
  products: NovaShopFeatureProduct[];
}> {
  const url = new URL(`${baseUrl.replace(/\/+$/, "")}/api/recommendations`);
  url.searchParams.set("user_id", params.user_id);
  if (params.cart_skus?.length) url.searchParams.set("cart_skus", params.cart_skus.join(","));
  if (params.subscription_plan) url.searchParams.set("subscription_plan", params.subscription_plan);
  if (params.user_type) url.searchParams.set("user_type", params.user_type);
  const res = await fetch(url.toString(), { cache: "no-store" });
  if (!res.ok) throw new Error(`Recommendations fetch failed (${res.status})`);
  return res.json();
}

export async function fetchSearchResults(
  baseUrl: string,
  params: { q: string; user_id: string },
): Promise<{
  flag_key: string;
  variation_key: string;
  kill_switch_enabled: boolean;
  results: NovaShopFeatureProduct[];
}> {
  const url = new URL(`${baseUrl.replace(/\/+$/, "")}/api/search`);
  url.searchParams.set("q", params.q);
  url.searchParams.set("user_id", params.user_id);
  const res = await fetch(url.toString(), { cache: "no-store" });
  if (!res.ok) throw new Error(`Search failed (${res.status})`);
  return res.json();
}

export async function trackStorefrontEvent(
  baseUrl: string,
  payload: {
    user_id: string;
    event_type: string;
    flag_key: string;
    variation_key: string;
    sku?: string;
  },
): Promise<void> {
  await fetch(`${baseUrl.replace(/\/+$/, "")}/api/events/track`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
