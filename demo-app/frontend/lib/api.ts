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
