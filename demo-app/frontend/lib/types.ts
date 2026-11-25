export interface StoreRuntimeConfig {
  demoApiUrl: string;
  flagsmithEnvKey: string;
  flagsmithEdgeApiUrl: string;
  releaseGuardUrl: string;
}

export interface CatalogItem {
  sku: string;
  name: string;
  tagline: string;
  price: number;
  quantity: number;
}

export interface HealthStatus {
  status: string;
  flagsmith_connected: boolean;
  environment_name: string;
  flag_key: string;
  releaseguard_api_url: string;
}

export interface FlagEvaluation {
  user_id: string;
  user_identifier: string;
  flag_key: string;
  environment_name: string;
  enabled: boolean;
  value: unknown;
  variation_key: string;
  checkout_version: "v1" | "v2";
  flagsmith_connected: boolean;
  evaluation_source: "flagsmith_sdk" | "fallback_default";
  event_emitted: boolean;
  error?: string | null;
}

export interface CheckoutResult {
  order_id: string;
  session_key: string;
  status: "completed" | "abandoned" | "payment_failed";
  checkout_version: "v1" | "v2";
  variation_key: string;
  flag_key: string;
  environment_name: string;
  user_identifier: string;
  subtotal: number;
  discount_amount: number;
  total_charged: number;
  promo_applied?: string | null;
  payment_method: string;
  steps_completed: string[];
  message: string;
  error_code?: string | null;
  error_message?: string | null;
  flagsmith_connected: boolean;
  emitted_events: string[];
}

export interface VariantMetrics {
  variation_key: string;
  exposures: number;
  started: number;
  completed: number;
  abandoned: number;
  payment_failed: number;
  conversion_rate: number;
  error_rate: number;
  avg_order_value: number;
}

export interface SimulationReport {
  scenario: "healthy_v2" | "buggy_v2" | "neutral";
  users_count: number;
  evaluation_mode: "flagsmith_live" | "simulated_split";
  flagsmith_connected: boolean;
  environment_name: string;
  flag_key: string;
  events_generated: number;
  batches_sent: number;
  ingest_accepted: number;
  ingest_deduplicated: number;
  ingest_failed_batches: number;
  ingest_error?: string | null;
  control: VariantMetrics;
  treatment: VariantMetrics;
}

export interface TelemetryEventRecord {
  event_id: string;
  flag_key: string;
  environment_name: string;
  variation_key: string;
  event_type: string;
  user_identifier: string;
  session_key?: string | null;
  numeric_value?: number | null;
  properties: Record<string, unknown>;
  occurred_at: string;
  delivery_status: "delivered" | "offline" | "error";
  delivery_detail?: string | null;
}
