"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Code2,
  Database,
  Flag,
  FlaskConical,
  HeartPulse,
  Layers,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Terminal,
  UserCheck,
} from "lucide-react";
import {
  explainFlagsmithIdentity,
  getErrorMessage,
  getFlagsmithCommandCenter,
  promoteExperimentWinner,
  provisionFlagsmithCatalog,
  runFlagsmithTestHarness,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  EmptyState,
  ErrorBanner,
  LoadingSkeleton,
  MetricCard,
  PageHeader,
  SectionCard,
} from "@/components/ui/StatePrimitives";
import {
  EnvironmentBadge,
  RecommendationBadge,
  StatusBadge,
} from "@/components/StatusBadges";

interface EnvStateEntry {
  enabled?: boolean;
  feature_state_value?: unknown;
  percentage_allocation?: number | null;
  segment_overrides?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

interface CatalogItem {
  key: string;
  registered_in_flagsmith: boolean;
  flagsmith_feature_id: number | null;
  flag_type: string;
  category: string;
  owner: string;
  purpose: string;
  default_enabled?: boolean;
  safe_default_value?: unknown;
  variants: string[];
  code_references: Array<string | { file_path?: string; line_number?: number; snippet?: string }>;
  linked_tests: string[];
  primary_metric: string | null;
  guardrail_metrics?: string[];
  env_states?: Record<string, EnvStateEntry>;
  inconsistent_environments: boolean;
  health_score: number | null;
  health_status: string;
  permanent?: boolean;
  review_due_at?: string | null;
  stale: boolean;
  cleanup_recommendations: string[];
}

interface HubExperiment {
  id: string;
  name: string;
  hypothesis: string;
  environment_name: string;
  status: string;
  analytics_mode: string;
  flagsmith_experiment_id: string | null;
  control_variation: string;
  treatment_variation: string;
  traffic_allocation?: Record<string, number>;
  primary_metric: string;
  latest_recommendation: string | null;
}

interface PendingApprovalRow {
  id: string;
  title: string;
  action_type: string;
  environment_name: string;
  policy_category: string;
  rationale: string;
}

interface ExecutionRow {
  id: string;
  event_action: string;
  resource_type: string;
  environment_name: string | null;
  status: string;
  policy_category: string;
  approval_id: string | null;
  created_at: string;
}

interface CapabilityFlag {
  flag_key: string;
  enabled: boolean;
  source: string;
}

interface TargetingPreset {
  segment_name: string;
  description?: string;
  match_type?: string;
  conditions?: Array<Record<string, unknown>>;
}

interface CliStatus {
  cli_binary_found?: boolean;
  cli_version?: string;
  npx_available?: boolean;
  auth_note?: string;
}

interface FlagsmithHubData {
  project_id?: number | null;
  environments?: string[];
  cli?: CliStatus;
  metrics?: {
    catalog_flags_total?: number;
    provisioned_in_flagsmith?: number;
    flags_with_warnings?: number;
    stale_flags?: number;
    active_experiments?: number;
    total_experiments?: number;
    pending_approvals?: number;
    rollouts_in_progress?: number;
  };
  catalog?: CatalogItem[];
  capability_flags?: CapabilityFlag[];
  targeting_presets?: TargetingPreset[];
  experiments?: HubExperiment[];
  pending_approvals?: PendingApprovalRow[];
  recent_executions?: ExecutionRow[];
}

interface IdentityEvalFlag {
  flag_key: string;
  enabled?: boolean;
  value?: unknown;
  reason?: string;
}

interface IdentityExplainResult {
  consistent_on_repeat_evaluation?: boolean;
  flags?: IdentityEvalFlag[];
}

function formatCodeRef(
  ref: string | { file_path?: string; line_number?: number; snippet?: string }
): string {
  if (typeof ref === "string") return ref;
  const path = ref.file_path ?? "unknown";
  return ref.line_number ? `${path}:${ref.line_number}` : path;
}

function formatSafeFallback(item: CatalogItem): string {
  if (item.safe_default_value !== null && item.safe_default_value !== undefined) {
    return `${item.default_enabled ? "enabled" : "disabled"} (${String(item.safe_default_value)})`;
  }
  return item.default_enabled ? "enabled (true)" : "disabled (false)";
}

export default function FlagsmithHubPage() {
  const [data, setData] = useState<FlagsmithHubData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [expandedFlagKey, setExpandedFlagKey] = useState<string | null>(null);
  const [catalogFilter, setCatalogFilter] = useState<"all" | "live" | "local_only">("all");

  // Identity Debugger state
  const [envName, setEnvName] = useState("development");
  const [identifier, setIdentifier] = useState("shopper_42");
  const [userType, setUserType] = useState("internal");
  const [plan, setPlan] = useState("enterprise");
  const [identityResult, setIdentityResult] = useState<IdentityExplainResult | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = (await getFlagsmithCommandCenter()) as FlagsmithHubData;
      setData(res);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    getFlagsmithCommandCenter()
      .then((res) => {
        if (!cancelled) {
          setData(res as FlagsmithHubData);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(getErrorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleProvision = async () => {
    setBusy("provision");
    setError(null);
    setNotice(null);
    try {
      const res = await provisionFlagsmithCatalog({ include_targeting_segments: true });
      const prov = (res.provisioned as string[]) || [];
      const segs = (res.segments_synced as string[]) || [];
      setNotice(
        `Provisioned ${prov.length} new catalog flags to Flagsmith and synced ${segs.length} targeting segments.`
      );
      await loadData();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const handleExplainIdentity = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy("identity");
    setError(null);
    try {
      const res = (await explainFlagsmithIdentity({
        environment_name: envName,
        identifier,
        traits: {
          user_type: userType,
          subscription_plan: plan,
          is_internal_tester: userType === "internal",
        },
      })) as IdentityExplainResult;
      setIdentityResult(res);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const handleRunHarness = async (exp: HubExperiment) => {
    setBusy(exp.id);
    setError(null);
    setNotice(null);
    try {
      const res = await runFlagsmithTestHarness(exp.id, { identities_per_arm: 20 });
      setNotice(
        `Synthetic test harness batch ${String(res.batch_id)} evaluated ${String(
          res.identities_evaluated
        )} Flagsmith identities (${String(res.conversions_recorded)} conversions).`
      );
      await loadData();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const handlePromote = async (exp: HubExperiment) => {
    setBusy(`promote:${exp.id}`);
    setError(null);
    setNotice(null);
    try {
      const outcome = await promoteExperimentWinner(exp.id, exp.treatment_variation);
      if (outcome.execution_status === "approval_required" && outcome.approval) {
        setNotice(
          `Queued human approval #${outcome.approval.id.slice(0, 8)} to promote '${exp.treatment_variation}'. Review in Approvals.`
        );
      } else {
        setNotice(`Promoted winning variant for '${exp.name}'.`);
      }
      await loadData();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  if (loading && !data) {
    return <LoadingSkeleton rows={5} />;
  }

  const metrics = data?.metrics ?? {};
  const cli = data?.cli ?? {};
  const catalog = data?.catalog ?? [];
  const environments = data?.environments ?? ["development", "staging", "production"];
  const experiments = data?.experiments ?? [];
  const approvals = data?.pending_approvals ?? [];
  const executions = data?.recent_executions ?? [];
  const capabilityFlags = data?.capability_flags ?? [];
  const targetingPresets = data?.targeting_presets ?? [];

  const filteredCatalog = catalog.filter((item) => {
    if (catalogFilter === "live") return item.registered_in_flagsmith;
    if (catalogFilter === "local_only") return !item.registered_in_flagsmith;
    return true;
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Flagsmith Hub"
        description="Connected Flagsmith project, environment rollout state, governed flag catalog with code paths & safe fallbacks, identity targeting, and active experiments."
        badge={
          <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/35 bg-emerald-500/10 px-2.5 py-0.5 text-[11px] font-medium text-emerald-300">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            {data?.project_id ? `Live Project #${data.project_id}` : "Project Not Connected"}
          </span>
        }
        actions={
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => void handleProvision()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
            >
              <Flag className="h-3.5 w-3.5" />
              {busy === "provision" ? "Syncing Catalog..." : "Sync Governed Catalog to Flagsmith"}
            </button>
            <button
              type="button"
              onClick={() => void loadData()}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-medium text-slate-200 hover:bg-slate-800"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Refresh
            </button>
          </div>
        }
      />

      {/* Data Provenance Legend: Live Flagsmith vs Local ReleaseGuard Metadata */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800 bg-[#0d1119] px-4 py-2.5 text-xs">
        <div className="flex flex-wrap items-center gap-4">
          <span className="inline-flex items-center gap-1.5 text-emerald-300">
            <span className="h-2 w-2 rounded-full bg-emerald-400" />
            <strong className="font-semibold">Live Flagsmith Data:</strong> Project ID, Feature IDs, Environment States, Edge Identity Evaluation
          </span>
          <span className="inline-flex items-center gap-1.5 text-indigo-300">
            <Database className="h-3.5 w-3.5" />
            <strong className="font-semibold">Local ReleaseGuard Metadata:</strong> Owner, Safe Fallback, Code Paths, Linked Tests, SLA Warnings
          </span>
        </div>
        <Link href="/settings" className="text-[11px] font-medium text-cyan-300 hover:underline">
          Manage Connection in Settings
        </Link>
      </div>

      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {notice && (
        <div className="flex items-center gap-2 rounded-xl border border-emerald-500/40 bg-emerald-500/10 px-4 py-3 text-xs text-emerald-200">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" />
          <span>{notice}</span>
        </div>
      )}

      {/* Overview Metrics */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricCard
          label="Flagsmith Project"
          value={data?.project_id ? `#${String(data.project_id)}` : "Not set"}
          subtext={`${metrics.provisioned_in_flagsmith ?? 0} / ${metrics.catalog_flags_total ?? 0} flags live in Flagsmith`}
          icon={<Flag className="h-4 w-4" />}
          tone="indigo"
        />
        <MetricCard
          label="Active Experiments"
          value={`${metrics.active_experiments ?? 0} active`}
          subtext={`${metrics.total_experiments ?? 0} total tracked experiments`}
          icon={<FlaskConical className="h-4 w-4" />}
          tone="sky"
        />
        <MetricCard
          label="Pending Approvals"
          value={metrics.pending_approvals ?? 0}
          subtext={`${metrics.rollouts_in_progress ?? 0} progressive rollouts observing`}
          icon={<ShieldCheck className="h-4 w-4" />}
          tone="amber"
        />
        <MetricCard
          label="Lifecycle Warnings"
          value={metrics.flags_with_warnings ?? 0}
          subtext={`${metrics.stale_flags ?? 0} expired review SLA`}
          icon={<HeartPulse className="h-4 w-4" />}
          tone={metrics.stale_flags ? "rose" : "emerald"}
        />
      </div>

      {/* Connected Environments & Governance Status */}
      <SectionCard
        title="Connected Project & Environment Status"
        subtitle="Live Flagsmith environment coverage and PolicyEngine governance rules"
        actions={<Layers className="h-4 w-4 text-indigo-400" />}
      >
        <div className="grid gap-3 md:grid-cols-3">
          {environments.map((env) => {
            const liveCount = catalog.filter(
              (c) => c.registered_in_flagsmith && c.env_states && c.env_states[env]
            ).length;
            const enabledCount = catalog.filter(
              (c) => c.env_states?.[env]?.enabled === true
            ).length;
            const isProd = env === "production";
            return (
              <div
                key={env}
                className={cn(
                  "rounded-xl border p-3.5 text-xs space-y-2",
                  isProd
                    ? "border-rose-500/30 bg-rose-950/20"
                    : "border-slate-800 bg-[#0d1119]"
                )}
              >
                <div className="flex items-center justify-between">
                  <EnvironmentBadge environment={env} />
                  <span className="font-mono text-[11px] text-slate-400">
                    {isProd ? "Cat C: Human Approval" : "Cat B: Guarded Auto"}
                  </span>
                </div>
                <div className="flex items-baseline justify-between pt-1">
                  <span className="text-slate-300">
                    <strong className="font-mono text-slate-100">{enabledCount}</strong> enabled /{" "}
                    <span className="font-mono">{liveCount}</span> synced
                  </span>
                  <span className="text-[11px] text-emerald-300">
                    {data?.project_id ? "Connected" : "Disconnected"}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </SectionCard>

      {/* Governed Feature Flag Catalog with Per-Flag Explanations */}
      <SectionCard
        title="Governed Feature Flag Catalog & Lifecycle"
        subtitle="Click any flag to inspect what it controls, code paths, safe fallback, owner, per-environment rollout status, and variants"
        actions={
          <div className="flex items-center gap-1.5 text-xs">
            {(
              [
                { id: "all", label: `All (${catalog.length})` },
                {
                  id: "live",
                  label: `Live Flagsmith (${catalog.filter((c) => c.registered_in_flagsmith).length})`,
                },
                {
                  id: "local_only",
                  label: `Local Metadata Only (${catalog.filter((c) => !c.registered_in_flagsmith).length})`,
                },
              ] as const
            ).map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setCatalogFilter(tab.id)}
                className={cn(
                  "rounded-lg border px-2.5 py-1 text-[11px] font-medium transition-colors",
                  catalogFilter === tab.id
                    ? "border-indigo-500/50 bg-indigo-500/15 text-indigo-200"
                    : "border-slate-800 bg-[#0d1119] text-slate-400 hover:border-slate-700"
                )}
              >
                {tab.label}
              </button>
            ))}
          </div>
        }
      >
        {filteredCatalog.length === 0 ? (
          <EmptyState title="No matching flags in catalog" description="Sync or adjust filter to view flags." />
        ) : (
          <div className="space-y-2.5">
            {filteredCatalog.map((item) => {
              const isExpanded = expandedFlagKey === item.key;
              return (
                <div
                  key={item.key}
                  className="rounded-xl border border-slate-800 bg-[#0d1119] transition-colors"
                >
                  <button
                    type="button"
                    onClick={() => setExpandedFlagKey(isExpanded ? null : item.key)}
                    className="flex w-full flex-wrap items-center justify-between gap-3 p-3.5 text-left text-xs hover:bg-slate-900/40"
                  >
                    <div className="flex items-start gap-2.5 min-w-0">
                      {isExpanded ? (
                        <ChevronDown className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
                      ) : (
                        <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
                      )}
                      <div className="space-y-1 min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono font-semibold text-indigo-300">{item.key}</span>
                          {item.registered_in_flagsmith ? (
                            <span className="rounded-full border border-emerald-500/35 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
                              Live Flagsmith #{item.flagsmith_feature_id}
                            </span>
                          ) : (
                            <span className="rounded-full border border-amber-500/35 bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-300">
                              Local Metadata Only (Unprovisioned)
                            </span>
                          )}
                          <span className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                            {item.flag_type}
                          </span>
                          {item.inconsistent_environments && (
                            <span className="inline-flex items-center gap-1 rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-300">
                              <AlertTriangle className="h-3 w-3" />
                              Env Drift
                            </span>
                          )}
                        </div>
                        <p className="text-[11px] text-slate-400 line-clamp-1">{item.purpose}</p>
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-3 shrink-0">
                      <span className="text-[11px] text-slate-400">
                        Owner: <span className="text-slate-200">{item.owner}</span>
                      </span>
                      <StatusBadge status={item.health_status} />
                    </div>
                  </button>

                  {isExpanded && (
                    <div className="border-t border-slate-800/80 px-4 py-3.5 text-xs space-y-4">
                      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
                        {/* Local Metadata: Purpose & Safe Fallback */}
                        <div className="rounded-lg border border-slate-800 bg-[#090c12] p-3 space-y-1.5">
                          <div className="flex items-center justify-between">
                            <span className="text-[10px] font-semibold uppercase tracking-wider text-indigo-300">
                              What It Controls (Local)
                            </span>
                          </div>
                          <p className="text-slate-200 leading-relaxed">{item.purpose}</p>
                          <div className="pt-1 text-[11px] text-slate-400">
                            Safe Fallback:{" "}
                            <span className="font-mono text-emerald-300">
                              {formatSafeFallback(item)}
                            </span>
                          </div>
                        </div>

                        {/* Local Metadata: Code Path & Tests */}
                        <div className="rounded-lg border border-slate-800 bg-[#090c12] p-3 space-y-1.5">
                          <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-indigo-300">
                            <Code2 className="h-3 w-3" />
                            Code Path &amp; Tests (Local)
                          </div>
                          {item.code_references.length > 0 ? (
                            <ul className="space-y-1 font-mono text-[11px] text-slate-300">
                              {item.code_references.slice(0, 3).map((ref, idx) => (
                                <li key={idx} className="truncate" title={formatCodeRef(ref)}>
                                  {formatCodeRef(ref)}
                                </li>
                              ))}
                            </ul>
                          ) : (
                            <p className="text-[11px] text-amber-300">No code references scanned yet</p>
                          )}
                          <div className="pt-1 text-[11px] text-slate-400">
                            Tests:{" "}
                            <span className="font-mono text-slate-300">
                              {item.linked_tests.length > 0
                                ? item.linked_tests.join(", ")
                                : "None linked"}
                            </span>
                          </div>
                        </div>

                        {/* Variants & Metrics */}
                        <div className="rounded-lg border border-slate-800 bg-[#090c12] p-3 space-y-1.5">
                          <div className="text-[10px] font-semibold uppercase tracking-wider text-cyan-300">
                            Variants &amp; Metrics
                          </div>
                          <div className="flex flex-wrap gap-1">
                            {item.variants.length > 0 ? (
                              item.variants.map((v) => (
                                <span
                                  key={v}
                                  className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-mono text-[11px] text-slate-200"
                                >
                                  {v}
                                </span>
                              ))
                            ) : (
                              <span className="font-mono text-[11px] text-slate-300">
                                boolean (true / false)
                              </span>
                            )}
                          </div>
                          <div className="pt-1 text-[11px] text-slate-400">
                            Primary Metric:{" "}
                            <span className="font-mono text-cyan-300">
                              {item.primary_metric ?? "N/A"}
                            </span>
                          </div>
                        </div>

                        {/* Live Flagsmith: Env Config & Rollout Status */}
                        <div className="rounded-lg border border-slate-800 bg-[#090c12] p-3 space-y-1.5">
                          <div className="text-[10px] font-semibold uppercase tracking-wider text-emerald-300">
                            Env Config &amp; Rollout (Live Flagsmith)
                          </div>
                          <div className="space-y-1">
                            {environments.map((env) => {
                              const st = item.env_states?.[env];
                              return (
                                <div
                                  key={env}
                                  className="flex items-center justify-between font-mono text-[11px]"
                                >
                                  <span className="text-slate-400">{env}</span>
                                  {st ? (
                                    <span
                                      className={
                                        st.enabled ? "text-emerald-300" : "text-slate-400"
                                      }
                                    >
                                      {st.enabled ? "ON" : "OFF"}
                                      {st.percentage_allocation != null
                                        ? ` · ${st.percentage_allocation}%`
                                        : ""}
                                      {st.feature_state_value != null &&
                                      String(st.feature_state_value) !== ""
                                        ? ` · ${String(st.feature_state_value)}`
                                        : ""}
                                    </span>
                                  ) : (
                                    <span className="text-slate-500">not synced</span>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      </div>

                      {/* Lifecycle Recommendations */}
                      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800/80 bg-[#090c12] px-3 py-2 text-[11px]">
                        <div className="flex items-center gap-2 text-slate-300">
                          <ShieldAlert className="h-3.5 w-3.5 text-amber-400" />
                          <span>
                            Health Score:{" "}
                            <strong className="font-mono">
                              {item.health_score !== null ? `${item.health_score}/100` : "N/A"}
                            </strong>
                            {" · "}
                            {item.cleanup_recommendations.length > 0
                              ? `Recommendations: ${item.cleanup_recommendations.join(", ")}`
                              : "All governance checks passed (linked code & tests)."}
                          </span>
                        </div>
                        {item.stale && (
                          <span className="rounded border border-rose-500/40 bg-rose-500/10 px-2 py-0.5 font-medium text-rose-300">
                            Review SLA Expired
                          </span>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </SectionCard>

      {/* Identity Targeting & Live Edge Debugger */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SectionCard
          title="Identity & Targeting Explainer"
          subtitle="Evaluate any shopper ID + traits directly against live Flagsmith Edge"
          actions={
            <span className="rounded-full border border-emerald-500/35 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
              Live Flagsmith Edge
            </span>
          }
        >
          <form onSubmit={(e) => void handleExplainIdentity(e)} className="grid grid-cols-2 gap-2 text-xs">
            <input
              type="text"
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
              placeholder="Identifier (e.g. shopper_42)"
              className="rounded-lg border border-slate-700 bg-[#090c12] px-2.5 py-1.5 font-mono text-slate-100"
            />
            <select
              value={envName}
              onChange={(e) => setEnvName(e.target.value)}
              className="rounded-lg border border-slate-700 bg-[#090c12] px-2.5 py-1.5 text-slate-100"
            >
              <option value="development">development</option>
              <option value="staging">staging</option>
              <option value="production">production</option>
            </select>
            <select
              value={userType}
              onChange={(e) => setUserType(e.target.value)}
              className="rounded-lg border border-slate-700 bg-[#090c12] px-2.5 py-1.5 text-slate-100"
            >
              <option value="internal">user_type: internal</option>
              <option value="qa">user_type: qa</option>
              <option value="beta">user_type: beta</option>
              <option value="consumer">user_type: consumer</option>
            </select>
            <div className="flex gap-2">
              <select
                value={plan}
                onChange={(e) => setPlan(e.target.value)}
                className="flex-1 rounded-lg border border-slate-700 bg-[#090c12] px-2.5 py-1.5 text-slate-100"
              >
                <option value="enterprise">plan: enterprise</option>
                <option value="pro">plan: pro</option>
                <option value="beta">plan: beta</option>
                <option value="free">plan: free</option>
              </select>
              <button
                type="submit"
                disabled={busy === "identity"}
                className="rounded-lg bg-indigo-500 px-3 py-1.5 font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
              >
                {busy === "identity" ? "..." : "Explain"}
              </button>
            </div>
          </form>

          {identityResult && (
            <div className="mt-3 rounded-lg border border-slate-800 bg-[#090c12] p-2.5 text-[11px] space-y-1">
              <div className="flex items-center justify-between text-emerald-300 font-medium">
                <span>
                  Consistent assignment verified:{" "}
                  {identityResult.consistent_on_repeat_evaluation ? "YES" : "NO"}
                </span>
                <UserCheck className="h-3.5 w-3.5" />
              </div>
              {(identityResult.flags ?? []).map((f) => (
                <div key={f.flag_key} className="font-mono text-slate-300">
                  {f.flag_key} = {String(f.value ?? f.enabled)} ({f.reason ?? "evaluated"})
                </div>
              ))}
            </div>
          )}
        </SectionCard>

        {/* Targeting Segments & Presets */}
        <SectionCard
          title="Governed Identity Targeting Segments"
          subtitle="Synced Flagsmith segments for internal QA, beta cohorts, and enterprise tiers"
        >
          {targetingPresets.length === 0 ? (
            <EmptyState
              title="No targeting presets configured"
              description="Sync the governed catalog to provision default Flagsmith segments."
            />
          ) : (
            <div className="space-y-2 text-xs">
              {targetingPresets.map((preset) => (
                <div
                  key={preset.segment_name}
                  className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 space-y-1"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono font-semibold text-cyan-300">
                      {preset.segment_name}
                    </span>
                    <span className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                      {preset.match_type ?? "ALL"}
                    </span>
                  </div>
                  {preset.description && (
                    <p className="text-[11px] text-slate-400">{preset.description}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      </div>

      {/* Active Experiments & Synthetic Test Harness */}
      <SectionCard
        title="Active Experiments & Recommendations"
        subtitle="Trigger explicitly labelled synthetic test traffic in non-prod or inspect full Bayesian posteriors in Experiments"
        actions={
          <Link
            href="/experiments"
            className="text-xs font-medium text-indigo-300 hover:underline"
          >
            Open Full Bayesian Experiments View →
          </Link>
        }
      >
        {experiments.length === 0 ? (
          <EmptyState
            title="No experiments yet"
            description="Plan an experiment from /experiments or via the AI Command Center."
          />
        ) : (
          <div className="space-y-3">
            {experiments.slice(0, 6).map((exp) => (
              <div
                key={exp.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800 bg-[#0d1119] p-3.5 text-xs"
              >
                <div className="space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link
                      href={`/experiments?id=${exp.id}`}
                      className="font-semibold text-slate-100 hover:text-indigo-300 hover:underline"
                    >
                      {exp.name}
                    </Link>
                    <EnvironmentBadge environment={exp.environment_name} />
                    <StatusBadge status={exp.status} />
                    <RecommendationBadge recommendation={exp.latest_recommendation} />
                  </div>
                  <div className="text-[11px] text-slate-400">
                    Variants:{" "}
                    <span className="font-mono text-slate-300">
                      {exp.control_variation} vs {exp.treatment_variation}
                    </span>{" "}
                    · Metric: <span className="font-mono text-cyan-300">{exp.primary_metric}</span>
                    {exp.flagsmith_experiment_id
                      ? ` · Flagsmith Exp #${exp.flagsmith_experiment_id}`
                      : " · Local Tracked-Events"}
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  {exp.environment_name !== "production" && (
                    <button
                      type="button"
                      disabled={busy !== null}
                      onClick={() => void handleRunHarness(exp)}
                      className="rounded-lg border border-amber-500/40 bg-amber-500/15 px-2.5 py-1.5 text-[11px] font-semibold text-amber-200 hover:bg-amber-500/25 disabled:opacity-50"
                    >
                      {busy === exp.id ? "Running Synthetic Harness..." : "Run Synthetic Test Traffic"}
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => void handlePromote(exp)}
                    className="rounded-lg bg-indigo-500 px-2.5 py-1.5 text-[11px] font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
                  >
                    Promote Winner (Requires Approval)
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </SectionCard>

      {/* Approvals & Execution History */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SectionCard
          title="Pending Flagsmith Approvals"
          subtitle="Consequential mutations paused for human sign-off"
          actions={
            <Link href="/approvals" className="text-xs font-medium text-indigo-300 hover:underline">
              Open Approval Queue
            </Link>
          }
        >
          {approvals.length === 0 ? (
            <EmptyState title="No pending approvals" description="All Flagsmith approvals are resolved." />
          ) : (
            <div className="space-y-2 text-xs">
              {approvals.map((app) => (
                <div key={app.id} className="rounded-lg border border-slate-800 bg-[#0d1119] p-2.5">
                  <div className="font-semibold text-slate-100">{app.title}</div>
                  <div className="font-mono text-[11px] text-amber-300">
                    Cat {app.policy_category} · {app.environment_name} · {app.action_type}
                  </div>
                  <p className="mt-1 text-[11px] text-slate-400">{app.rationale}</p>
                </div>
              ))}
            </div>
          )}
        </SectionCard>

        <SectionCard
          title="Recent Flagsmith Execution History"
          subtitle="Audited mutations and verification records"
        >
          {executions.length === 0 ? (
            <EmptyState title="No recent executions" description="Mutations will appear here." />
          ) : (
            <div className="space-y-2 max-h-64 overflow-y-auto text-xs">
              {executions.map((ex) => (
                <div
                  key={ex.id}
                  className="flex items-center justify-between rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2"
                >
                  <div>
                    <div className="font-mono text-slate-200">{ex.event_action}</div>
                    <div className="text-[11px] text-slate-400">
                      {ex.resource_type} · {ex.environment_name ?? "global"} · Cat{" "}
                      {ex.policy_category}
                    </div>
                  </div>
                  <StatusBadge status={ex.status} />
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      </div>

      {/* Collapsible CLI & Capability Diagnostics (reduced clutter) */}
      <details className="rounded-xl border border-slate-800 bg-[#0d1119] p-4 text-xs">
        <summary className="flex cursor-pointer items-center justify-between font-medium text-slate-300 hover:text-slate-100">
          <span className="inline-flex items-center gap-2">
            <Terminal className="h-4 w-4 text-cyan-400" />
            Integration &amp; CLI Diagnostics (Advanced)
          </span>
          <span className="font-mono text-[11px] text-slate-400">
            {cli.cli_binary_found
              ? `CLI ${cli.cli_version ?? "flagsmith"}`
              : cli.npx_available
                ? "npx @flagsmith/cli"
                : "Management API Mode"}
          </span>
        </summary>
        <div className="mt-3 space-y-2 border-t border-slate-800/80 pt-3 text-slate-300">
          <p className="text-[11px] text-slate-400">{cli.auth_note ?? "Authenticated via Flagsmith Management API."}</p>
          <div className="flex flex-wrap gap-2">
            {capabilityFlags.map((cf) => (
              <span
                key={cf.flag_key}
                className="rounded border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-[11px]"
              >
                {cf.flag_key}: {cf.enabled ? "ON" : "OFF"} ({cf.source})
              </span>
            ))}
          </div>
        </div>
      </details>
    </div>
  );
}
