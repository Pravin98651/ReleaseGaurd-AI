"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ExternalLink,
  Flag,
  Layers,
  LogIn,
  LogOut,
  Minus,
  Plus,
  ShoppingBag,
  ShieldCheck,
  UserCheck,
  XCircle,
} from "lucide-react";
import {
  evaluateBackendFlag,
  fetchDemoHealth,
  fetchRecentTelemetry,
  fetchRecommendations,
  fetchSearchResults,
  runBackendSimulation,
  submitCheckout,
  trackStorefrontEvent,
  type NovaShopFeatureProduct,
} from "@/lib/api";
import { demoFlagsmithClient, type ClientFlagState } from "@/lib/flagsmith";
import type {
  CatalogItem,
  CheckoutResult,
  FlagEvaluation,
  HealthStatus,
  SimulationReport,
  StoreRuntimeConfig,
  TelemetryEventRecord,
} from "@/lib/types";
import { CheckoutV1Form } from "./CheckoutV1Form";
import { CheckoutV2Express } from "./CheckoutV2Express";
import { ShopperLoginModal, type ShopperSession } from "./ShopperLoginModal";
import { SimulatorBar } from "./SimulatorBar";
import { TelemetryInspector } from "./TelemetryInspector";

const INITIAL_CART: CatalogItem[] = [
  {
    sku: "NV-KEY-PRO",
    name: "NovaKey Mechanical Pro",
    tagline: "Gasket-mounted aluminum wireless 75%",
    price: 149.0,
    quantity: 1,
  },
  {
    sku: "NV-DOCK-C",
    name: "Studio Thunderbolt 4 Dock",
    tagline: "Dual 4K60, 96W host charging",
    price: 89.0,
    quantity: 1,
  },
  {
    sku: "NV-STAND-M",
    name: "MagStand CNC Aluminum",
    tagline: "Anodized vertical laptop & tablet riser",
    price: 45.0,
    quantity: 0,
  },
];

const USER_PRESETS = ["shopper_42", "user_001", "user_002", "vip_shopper_99"];

interface NovaShopStorefrontProps {
  initialConfig: StoreRuntimeConfig;
}

export function NovaShopStorefront({ initialConfig }: NovaShopStorefrontProps) {
  const [userId, setUserId] = useState("shopper_42");
  const [userInput, setUserInput] = useState("shopper_42");
  const [cart, setCart] = useState<CatalogItem[]>(INITIAL_CART);
  const [previewOverride, setPreviewOverride] = useState<"auto" | "v1" | "v2">("auto");

  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [clientFlag, setClientFlag] = useState<ClientFlagState | null>(null);
  const [backendFlag, setBackendFlag] = useState<FlagEvaluation | null>(null);
  const [backendError, setBackendError] = useState<string | null>(null);

  const [submittingCheckout, setSubmittingCheckout] = useState(false);
  const [checkoutResult, setCheckoutResult] = useState<CheckoutResult | null>(null);

  const [simRunning, setSimRunning] = useState(false);
  const [simReport, setSimReport] = useState<SimulationReport | null>(null);

  const [telemetryEvents, setTelemetryEvents] = useState<TelemetryEventRecord[]>([]);
  const [totalBuffered, setTotalBuffered] = useState(0);
  const [userType, setUserType] = useState("consumer");
  const [subscriptionPlan, setSubscriptionPlan] = useState("free");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchAlgo, setSearchAlgo] = useState("control");
  const [searchResults, setSearchResults] = useState<NovaShopFeatureProduct[]>([]);
  const [recAlgo, setRecAlgo] = useState("control");
  const [recProducts, setRecProducts] = useState<NovaShopFeatureProduct[]>([]);
  const [loginOpen, setLoginOpen] = useState(false);
  const [shopperSession, setShopperSession] = useState<ShopperSession | null>(null);

  const refreshTelemetry = useCallback(async () => {
    try {
      const data = await fetchRecentTelemetry(initialConfig.demoApiUrl, 30);
      setTelemetryEvents(data.events);
      setTotalBuffered(data.total_buffered);
    } catch {
      // Handled via backendError banner when health/flags check runs
    }
  }, [initialConfig.demoApiUrl]);

  const loadUserEvaluation = useCallback(
    async (targetUser: string) => {
      const clientPromise = demoFlagsmithClient.evaluateUser({
        envKey: initialConfig.flagsmithEnvKey,
        edgeApiUrl: initialConfig.flagsmithEdgeApiUrl,
        userId: targetUser,
      });

      const backendPromise = Promise.all([
        fetchDemoHealth(initialConfig.demoApiUrl),
        evaluateBackendFlag(initialConfig.demoApiUrl, targetUser),
        fetchRecentTelemetry(initialConfig.demoApiUrl, 30).catch(() => null),
      ]);

      const clientRes = await clientPromise;
      try {
        const [healthRes, flagRes, telemRes] = await backendPromise;
        return {
          clientRes,
          healthRes,
          flagRes,
          telemRes,
          error: null as string | null,
        };
      } catch (err) {
        return {
          clientRes,
          healthRes: null,
          flagRes: null,
          telemRes: null,
          error:
            err instanceof Error
              ? err.message
              : `Cannot reach Demo Backend at ${initialConfig.demoApiUrl}`,
        };
      }
    },
    [
      initialConfig.demoApiUrl,
      initialConfig.flagsmithEdgeApiUrl,
      initialConfig.flagsmithEnvKey,
    ],
  );

  const applyEvaluationSnapshot = useCallback(
    (snapshot: Awaited<ReturnType<typeof loadUserEvaluation>>) => {
      setClientFlag(snapshot.clientRes);
      setBackendError(snapshot.error);
      if (snapshot.healthRes) setHealth(snapshot.healthRes);
      if (snapshot.flagRes) setBackendFlag(snapshot.flagRes);
      if (snapshot.telemRes) {
        setTelemetryEvents(snapshot.telemRes.events);
        setTotalBuffered(snapshot.telemRes.total_buffered);
      }
    },
    [],
  );

  useEffect(() => {
    let cancelled = false;
    void loadUserEvaluation("shopper_42").then((snapshot) => {
      if (!cancelled) {
        applyEvaluationSnapshot(snapshot);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [applyEvaluationSnapshot, loadUserEvaluation]);

  const subtotal = Number(
    cart.reduce((acc, item) => acc + item.price * item.quantity, 0).toFixed(2),
  );

  function updateQuantity(sku: string, delta: number) {
    setCart((prev) =>
      prev.map((item) =>
        item.sku === sku
          ? { ...item, quantity: Math.max(0, Math.min(10, item.quantity + delta)) }
          : item,
      ),
    );
  }

  async function handleSwitchUser(nextUserId: string) {
    const trimmed = nextUserId.trim();
    if (!trimmed) return;
    setUserId(trimmed);
    setUserInput(trimmed);
    setCheckoutResult(null);
    setBackendError(null);
    const snapshot = await loadUserEvaluation(trimmed);
    applyEvaluationSnapshot(snapshot);
  }

  const liveVersion: "v1" | "v2" = clientFlag?.connected
    ? clientFlag.checkoutVersion
    : backendFlag?.checkout_version ?? "v1";

  const activeVersion: "v1" | "v2" =
    previewOverride === "auto" ? liveVersion : previewOverride;

  const isFlagsmithConnected = Boolean(
    clientFlag?.connected || backendFlag?.flagsmith_connected || health?.flagsmith_connected,
  );

  const activeVariationLabel =
    previewOverride !== "auto"
      ? `preview_${previewOverride}`
      : clientFlag?.connected
        ? clientFlag.variationKey
        : backendFlag?.variation_key ?? "v1";

  async function handleCheckoutSubmit(payload: {
    payment_method: string;
    wallet_provider?: string;
    promo_code?: string;
    simulate_payment_bug?: boolean;
    abandon?: boolean;
    shipping?: Record<string, string>;
  }) {
    if (subtotal <= 0) {
      setBackendError("Add at least one item to the NovaShop cart before checking out.");
      return;
    }
    setSubmittingCheckout(true);
    setBackendError(null);
    try {
      const result = await submitCheckout(initialConfig.demoApiUrl, {
        user_id: userId,
        cart_total: subtotal,
        payment_method: payload.payment_method,
        wallet_provider: payload.wallet_provider,
        promo_code: payload.promo_code,
        simulate_payment_bug: payload.simulate_payment_bug,
        abandon: payload.abandon,
        items: cart.filter((item) => item.quantity > 0),
        shipping: payload.shipping,
      });
      setCheckoutResult(result);
      await refreshTelemetry();
    } catch (err) {
      setBackendError(err instanceof Error ? err.message : "Checkout submission failed");
    } finally {
      setSubmittingCheckout(false);
    }
  }

  async function handleRunSimulation(
    scenario: "healthy_v2" | "buggy_v2" | "neutral",
    usersCount: number,
  ) {
    setSimRunning(true);
    setBackendError(null);
    try {
      const report = await runBackendSimulation(initialConfig.demoApiUrl, {
        scenario,
        users_count: usersCount,
        split_mode: "auto",
      });
      setSimReport(report);
      await refreshTelemetry();
    } catch (err) {
      setBackendError(err instanceof Error ? err.message : "Simulation request failed");
    } finally {
      setSimRunning(false);
    }
  }

  return (
    <div className="min-h-screen bg-[#0b0f17] text-slate-100">
      {/* Top Bar */}
      <header className="border-b border-slate-800/90 bg-slate-950/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-col gap-3 px-4 py-4 sm:px-6 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-indigo-500/40 bg-indigo-500/15 text-indigo-300">
              <ShoppingBag className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-base font-semibold tracking-tight text-white">
                  NovaShop Checkout Demo
                </h1>
                <span className="rounded border border-slate-700 bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                  PORT 3001
                </span>
              </div>
              <p className="text-xs text-slate-400">
                Flagsmith-gated Checkout v1 vs v2 storefront emitting live events to ReleaseGuard AI
              </p>
            </div>
          </div>

          {/* Flagsmith Variation Badge + User Switcher */}
          <div className="flex flex-wrap items-center gap-2.5">
            <div
              className={`inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs ${
                isFlagsmithConnected
                  ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
                  : "border-amber-500/40 bg-amber-500/10 text-amber-200"
              }`}
            >
              <Flag className="h-3.5 w-3.5" />
              <span className="font-mono font-medium">checkout_v2</span>
              <span className="rounded bg-slate-950/60 px-1.5 py-0.5 font-mono text-[11px]">
                {activeVersion.toUpperCase()} ({activeVariationLabel})
              </span>
              <span className="text-[11px] opacity-85">
                {isFlagsmithConnected ? "Flagsmith Live" : "Fallback Default"}
              </span>
            </div>

            {shopperSession ? (
              <div className="inline-flex items-center gap-2 rounded-lg border border-indigo-500/35 bg-indigo-500/10 px-2.5 py-1.5 text-xs text-indigo-200">
                <UserCheck className="h-3.5 w-3.5 text-indigo-300" />
                <span className="font-medium">{shopperSession.display_name}</span>
                <button
                  type="button"
                  onClick={() => {
                    setShopperSession(null);
                    setUserType("consumer");
                    setSubscriptionPlan("free");
                  }}
                  className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-slate-300 hover:text-white"
                  title="Sign out of NovaShop"
                >
                  <LogOut className="h-3 w-3" />
                  Sign Out
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setLoginOpen(true)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-500/40 bg-indigo-500/15 px-3 py-1.5 text-xs font-medium text-indigo-200 hover:bg-indigo-500/25 transition"
              >
                <LogIn className="h-3.5 w-3.5" />
                Sign In
              </button>
            )}

            <a
              href={`${initialConfig.releaseGuardUrl.replace(/\/+$/, "")}/experiments`}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800 transition"
            >
              <ShieldCheck className="h-3.5 w-3.5 text-sky-400" />
              Open ReleaseGuard
              <ExternalLink className="h-3 w-3 text-slate-400" />
            </a>
          </div>
        </div>

        {/* User ID Switcher Strip */}
        <div className="border-t border-slate-800/70 bg-slate-900/40">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-2.5 sm:px-6 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1 font-medium text-slate-300">
                <UserCheck className="h-3.5 w-3.5 text-sky-400" />
                Active Shopper ID:
              </span>
              {USER_PRESETS.map((preset) => (
                <button
                  key={preset}
                  type="button"
                  onClick={() => void handleSwitchUser(preset)}
                  className={`rounded-md border px-2 py-1 font-mono text-[11px] transition ${
                    userId === preset
                      ? "border-sky-500/60 bg-sky-500/15 text-sky-300"
                      : "border-slate-800 bg-slate-950 text-slate-400 hover:border-slate-700"
                  }`}
                >
                  {preset}
                </button>
              ))}
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void handleSwitchUser(userInput);
                }}
                className="flex items-center gap-1.5"
              >
                <input
                  type="text"
                  value={userInput}
                  onChange={(e) => setUserInput(e.target.value)}
                  placeholder="Custom user_id..."
                  className="w-36 rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1 font-mono text-[11px] text-white outline-none focus:border-sky-500"
                />
                <button
                  type="submit"
                  className="rounded-md border border-slate-700 bg-slate-800 px-2.5 py-1 text-[11px] font-medium text-slate-200 hover:bg-slate-700"
                >
                  Evaluate
                </button>
              </form>
            </div>

            <div className="flex items-center gap-2">
              <Layers className="h-3.5 w-3.5 text-slate-400" />
              <span className="text-slate-400">UI Flow:</span>
              {(["auto", "v1", "v2"] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setPreviewOverride(mode)}
                  className={`rounded px-2 py-0.5 font-mono text-[11px] transition ${
                    previewOverride === mode
                      ? "bg-indigo-600 text-white"
                      : "bg-slate-800 text-slate-400 hover:text-slate-200"
                  }`}
                >
                  {mode === "auto" ? "Auto (Flagsmith)" : `Preview ${mode}`}
                </button>
              ))}
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl space-y-5 px-4 py-6 sm:px-6">
        {backendError && (
          <div className="flex items-center gap-2.5 rounded-xl border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-xs text-rose-200">
            <AlertCircle className="h-4 w-4 shrink-0 text-rose-400" />
            <span>{backendError}</span>
          </div>
        )}

        {previewOverride !== "auto" && (
          <div className="flex items-center justify-between rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-2.5 text-xs text-amber-200">
            <span>
              Local UI preview override is set to{" "}
              <strong className="font-mono">{previewOverride.toUpperCase()}</strong>. Server-side{" "}
              <code className="font-mono">POST /api/checkout</code> still evaluates the live Flagsmith variation ({liveVersion.toUpperCase()}).
            </span>
            <button
              type="button"
              onClick={() => setPreviewOverride("auto")}
              className="rounded bg-amber-500/20 px-2 py-0.5 font-medium text-amber-100 hover:bg-amber-500/30"
            >
              Reset to Auto
            </button>
          </div>
        )}

        {/* Simulator Bar */}
        <SimulatorBar
          running={simRunning}
          lastReport={simReport}
          onRunSimulation={handleRunSimulation}
        />

        {/* Identity Traits, Smart Search & Recommendations Strip */}
        <section className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          <div className="lg:col-span-6 rounded-xl border border-slate-800 bg-slate-900/75 p-4 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="text-xs font-semibold text-white">
                Smart Search (<span className="font-mono text-sky-400">smart_product_search</span>: {searchAlgo})
              </div>
              <div className="flex items-center gap-1.5 text-[11px]">
                <select
                  value={userType}
                  onChange={(e) => setUserType(e.target.value)}
                  className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200"
                >
                  <option value="consumer">user_type: consumer</option>
                  <option value="internal">user_type: internal</option>
                  <option value="qa">user_type: qa</option>
                  <option value="beta">user_type: beta</option>
                </select>
                <select
                  value={subscriptionPlan}
                  onChange={(e) => setSubscriptionPlan(e.target.value)}
                  className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200"
                >
                  <option value="free">plan: free</option>
                  <option value="pro">plan: pro</option>
                  <option value="enterprise">plan: enterprise</option>
                  <option value="beta">plan: beta</option>
                </select>
              </div>
            </div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void fetchSearchResults(initialConfig.demoApiUrl, {
                  q: searchQuery,
                  user_id: userId,
                }).then((r) => {
                  setSearchAlgo(r.variation_key);
                  setSearchResults(r.results);
                });
              }}
              className="flex gap-2"
            >
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search (try 'keyboard', 'charging', or 'office')..."
                className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs text-white"
              />
              <button
                type="submit"
                className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-500"
              >
                Search
              </button>
            </form>
            {searchResults.length > 0 && (
              <div className="space-y-1.5 max-h-36 overflow-y-auto text-xs">
                {searchResults.map((p) => (
                  <div
                    key={p.sku}
                    className="flex items-center justify-between rounded border border-slate-800 bg-slate-950/70 px-2.5 py-1.5"
                  >
                    <div>
                      <span className="font-medium text-white">{p.name}</span>{" "}
                      <span className="font-mono text-[10px] text-slate-400">
                        {p.match_reason}
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        void trackStorefrontEvent(initialConfig.demoApiUrl, {
                          user_id: userId,
                          event_type: "search_result_clicked",
                          flag_key: "smart_product_search",
                          variation_key: searchAlgo,
                          sku: p.sku,
                        });
                      }}
                      className="rounded border border-sky-500/40 px-2 py-0.5 text-[11px] text-sky-300 hover:bg-sky-500/15"
                    >
                      Select
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="lg:col-span-6 rounded-xl border border-slate-800 bg-slate-900/75 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-xs font-semibold text-white">
                Recommendations (<span className="font-mono text-emerald-400">recommendation_engine_v2</span>: {recAlgo})
              </div>
              <button
                type="button"
                onClick={() => {
                  void fetchRecommendations(initialConfig.demoApiUrl, {
                    user_id: userId,
                    cart_skus: cart.filter((i) => i.quantity > 0).map((i) => i.sku),
                    subscription_plan: subscriptionPlan,
                    user_type: userType,
                  }).then((r) => {
                    setRecAlgo(r.variation_key);
                    setRecProducts(r.products);
                  });
                }}
                className="rounded-lg border border-emerald-500/40 bg-emerald-500/15 px-2.5 py-1 text-[11px] font-semibold text-emerald-200 hover:bg-emerald-500/25"
              >
                Load Recommendations
              </button>
            </div>
            {recProducts.length === 0 ? (
              <p className="text-xs text-slate-400">
                Click &ldquo;Load Recommendations&rdquo; to evaluate{" "}
                <code className="font-mono">recommendation_engine_v2</code> &amp;{" "}
                <code className="font-mono">enable_ai_recommendations</code> kill switch.
              </p>
            ) : (
              <div className="space-y-1.5 text-xs">
                {recProducts.map((p) => (
                  <div
                    key={p.sku}
                    className="flex items-center justify-between rounded border border-slate-800 bg-slate-950/70 px-2.5 py-1.5"
                  >
                    <div>
                      <span className="font-medium text-white">{p.name}</span>{" "}
                      <span className="text-[11px] text-slate-400">${p.price.toFixed(2)}</span>
                      <div className="text-[10px] text-slate-500">{p.recommendation_reason}</div>
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        void trackStorefrontEvent(initialConfig.demoApiUrl, {
                          user_id: userId,
                          event_type: "product_clicked_from_recommendation",
                          flag_key: "recommendation_engine_v2",
                          variation_key: recAlgo,
                          sku: p.sku,
                        });
                      }}
                      className="rounded border border-emerald-500/40 px-2 py-0.5 text-[11px] text-emerald-300 hover:bg-emerald-500/15"
                    >
                      Inspect
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>

        {/* Cart + Checkout Grid */}
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-12">
          {/* Left: Cart */}
          <section className="lg:col-span-5 rounded-xl border border-slate-800 bg-slate-900/75 p-5 flex flex-col justify-between">
            <div>
              <div className="mb-4 flex items-center justify-between border-b border-slate-800 pb-3">
                <h2 className="text-base font-semibold text-white">Your Shopping Bag</h2>
                <span className="font-mono text-xs text-slate-400">
                  {cart.reduce((sum, i) => sum + i.quantity, 0)} items
                </span>
              </div>

              <div className="space-y-3">
                {cart.map((item) => (
                  <div
                    key={item.sku}
                    className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-950/70 p-3"
                  >
                    <div>
                      <div className="text-sm font-medium text-white">{item.name}</div>
                      <div className="text-xs text-slate-400">{item.tagline}</div>
                      <div className="mt-1 font-mono text-xs text-sky-400">
                        ${item.price.toFixed(2)}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        aria-label={`Decrease ${item.name}`}
                        onClick={() => updateQuantity(item.sku, -1)}
                        className="rounded-md border border-slate-700 bg-slate-900 p-1 text-slate-300 hover:bg-slate-800"
                      >
                        <Minus className="h-3.5 w-3.5" />
                      </button>
                      <span className="w-6 text-center font-mono text-xs text-white">
                        {item.quantity}
                      </span>
                      <button
                        type="button"
                        aria-label={`Increase ${item.name}`}
                        onClick={() => updateQuantity(item.sku, 1)}
                        className="rounded-md border border-slate-700 bg-slate-900 p-1 text-slate-300 hover:bg-slate-800"
                      >
                        <Plus className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="mt-5 border-t border-slate-800 pt-4 space-y-2 text-xs">
              <div className="flex justify-between text-slate-400">
                <span>Edge API</span>
                <span className="font-mono text-slate-300 truncate max-w-[230px]">
                  {initialConfig.flagsmithEdgeApiUrl}
                </span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Pseudonymized ID</span>
                <span className="font-mono text-slate-300">
                  {backendFlag?.user_identifier ?? userId}
                </span>
              </div>
              <div className="flex justify-between pt-2 text-base font-semibold text-white">
                <span>Cart Subtotal</span>
                <span className="font-mono">${subtotal.toFixed(2)}</span>
              </div>
            </div>
          </section>

          {/* Right: Active Checkout Flow + Result */}
          <section className="lg:col-span-7 space-y-4">
            {activeVersion === "v2" ? (
              <CheckoutV2Express
                subtotal={subtotal}
                submitting={submittingCheckout}
                onSubmit={handleCheckoutSubmit}
              />
            ) : (
              <CheckoutV1Form
                subtotal={subtotal}
                submitting={submittingCheckout}
                onSubmit={handleCheckoutSubmit}
              />
            )}

            {checkoutResult && (
              <div
                className={`rounded-xl border p-4 text-xs ${
                  checkoutResult.status === "completed"
                    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-100"
                    : checkoutResult.status === "payment_failed"
                      ? "border-rose-500/50 bg-rose-500/10 text-rose-100"
                      : "border-amber-500/40 bg-amber-500/10 text-amber-100"
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2">
                    {checkoutResult.status === "completed" ? (
                      <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                    ) : checkoutResult.status === "payment_failed" ? (
                      <XCircle className="h-4 w-4 text-rose-400" />
                    ) : (
                      <AlertCircle className="h-4 w-4 text-amber-400" />
                    )}
                    <span className="text-sm font-semibold">{checkoutResult.message}</span>
                  </div>
                  <span className="rounded bg-slate-950/60 px-2 py-0.5 font-mono text-[11px]">
                    {checkoutResult.order_id}
                  </span>
                </div>

                {checkoutResult.error_code && (
                  <div className="mt-2 rounded border border-rose-500/30 bg-slate-950/70 px-3 py-2 font-mono text-[11px] text-rose-300">
                    {checkoutResult.error_code}: {checkoutResult.error_message}
                  </div>
                )}

                <div className="mt-3 flex flex-wrap items-center gap-4 font-mono text-[11px] opacity-90">
                  <span>version: {checkoutResult.checkout_version}</span>
                  <span>variation: {checkoutResult.variation_key}</span>
                  <span>charged: ${checkoutResult.total_charged.toFixed(2)}</span>
                  <span>events: {checkoutResult.emitted_events.join(" -> ")}</span>
                </div>
              </div>
            )}
          </section>
        </div>

        {/* Live Telemetry Inspector */}
        <TelemetryInspector
          events={telemetryEvents}
          totalBuffered={totalBuffered}
          releaseGuardApiUrl={health?.releaseguard_api_url ?? "http://localhost:8000"}
          onRefresh={() => void refreshTelemetry()}
        />
      </main>

      <ShopperLoginModal
        open={loginOpen}
        demoApiUrl={initialConfig.demoApiUrl}
        onClose={() => setLoginOpen(false)}
        onLoggedIn={(sess) => {
          setShopperSession(sess);
          setUserType(sess.user_type);
          setSubscriptionPlan(sess.subscription_plan);
          void handleSwitchUser(sess.user_id);
        }}
      />
    </div>
  );
}
