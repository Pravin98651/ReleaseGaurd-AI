"use client";

import React, { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Download, RefreshCw, BookOpen, Info } from "lucide-react";
import {
  getAgentMetrics,
  getErrorMessage,
  getEventMetrics,
  getExperimentMetrics,
  getFeatureFlagMetrics,
  getMetricsExportUrl,
  getMetricsOverview,
  getPrMetrics,
  getReleaseMetrics,
  type DashboardQueryFilters,
} from "@/lib/api";
import type { ExperimentPerformancePoint, TimeRangePreset } from "@/types/releaseguard";
import {
  EmptyState,
  ErrorBanner,
  LoadingSkeleton,
  MetricCard,
  PageHeader,
  SectionCard,
} from "@/components/ui/StatePrimitives";
import { RecommendationBadge, StatusBadge, EnvironmentBadge } from "@/components/StatusBadges";

type MetricsTab =
  | "releases"
  | "pull_requests"
  | "feature_flags"
  | "experiments"
  | "agents"
  | "events"
  | "definitions";

const emptySubscribe = () => () => {};
function useIsHydrated(): boolean {
  return useSyncExternalStore(emptySubscribe, () => true, () => false);
}

function formatInTimezone(iso: string | null | undefined, tz: string): string {
  if (!iso) return "Unavailable";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  try {
    return new Intl.DateTimeFormat("en-US", {
      timeZone: tz === "local" ? undefined : tz,
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(d);
  } catch {
    return d.toISOString();
  }
}

function UnavailableValue({ reason }: { reason: string }) {
  return (
    <span title={reason} className="inline-flex items-center gap-1 text-slate-400">
      Unavailable <Info className="h-3.5 w-3.5 text-slate-500" />
    </span>
  );
}

export default function MetricsPage() {
  const hydrated = useIsHydrated();
  const [tab, setTab] = useState<MetricsTab>("releases");
  const [range, setRange] = useState<TimeRangePreset>("30d");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [environment, setEnvironment] = useState("all");
  const [repositoryId, setRepositoryId] = useState("all");
  const [flagKey, setFlagKey] = useState("all");
  const [experimentId, setExperimentId] = useState("all");
  const [eventSource, setEventSource] = useState<"all" | "simulator_generated" | "application_telemetry">("all");
  const [timezone, setTimezone] = useState("UTC");

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [overview, setOverview] = useState<Record<string, unknown> | null>(null);
  const [releasesData, setReleasesData] = useState<Record<string, unknown> | null>(null);
  const [prData, setPrData] = useState<Record<string, unknown> | null>(null);
  const [flagData, setFlagData] = useState<Record<string, unknown> | null>(null);
  const [expData, setExpData] = useState<Record<string, unknown> | null>(null);
  const [agentData, setAgentData] = useState<Record<string, unknown> | null>(null);
  const [eventData, setEventData] = useState<Record<string, unknown> | null>(null);

  const queryFilters: DashboardQueryFilters = useMemo(
    () => ({
      range,
      start_date: range === "custom" && startDate ? startDate : undefined,
      end_date: range === "custom" && endDate ? endDate : undefined,
      environment: environment !== "all" ? environment : undefined,
      repository_id: repositoryId !== "all" ? repositoryId : undefined,
      flag_key: flagKey !== "all" ? flagKey : undefined,
      experiment_id: experimentId !== "all" ? experimentId : undefined,
      event_source: eventSource,
    }),
    [range, startDate, endDate, environment, repositoryId, flagKey, experimentId, eventSource]
  );

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [ov, rel, pr, fl, ex, ag, ev] = await Promise.all([
        getMetricsOverview(queryFilters),
        getReleaseMetrics(queryFilters),
        getPrMetrics(queryFilters),
        getFeatureFlagMetrics(queryFilters),
        getExperimentMetrics(queryFilters),
        getAgentMetrics(queryFilters),
        getEventMetrics(queryFilters),
      ]);
      setOverview(ov);
      setReleasesData(rel);
      setPrData(pr);
      setFlagData(fl);
      setExpData(ex);
      setAgentData(ag);
      setEventData(ev);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [queryFilters]);

  useEffect(() => {
    const t = setTimeout(() => {
      void loadAll();
    }, 0);
    return () => clearTimeout(t);
  }, [loadAll]);

  const filterOpts = (overview?.filter_options as Record<string, Array<Record<string, string>>>) ?? {};
  const definitions = (overview?.metric_definitions as Array<Record<string, unknown>>) ?? [];

  const relM = (releasesData?.metrics as Record<string, unknown>) ?? {};
  const prM = (prData?.metrics as Record<string, unknown>) ?? {};
  const flM = (flagData?.metrics as Record<string, unknown>) ?? {};
  const agM = (agentData?.metrics as Record<string, unknown>) ?? {};
  const evTotals = (eventData?.totals as Record<string, number>) ?? {};
  const experiments = (expData?.experiments as ExperimentPerformancePoint[]) ?? [];

  const exportSection: "releases" | "pull_requests" | "feature_flags" | "experiments" | "agents" | "events" =
    tab === "definitions" ? "releases" : tab;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Metrics & Analytics"
        description="Operational release engineering, PR validation, Flagsmith configuration, Bayesian experimentation, agent telemetry, and event source attribution."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <a
              href={getMetricsExportUrl(exportSection, queryFilters)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-medium text-slate-200 hover:bg-slate-800"
            >
              <Download className="h-3.5 w-3.5" />
              Export CSV
            </a>
            <button
              type="button"
              onClick={() => void loadAll()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-400"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Refresh
            </button>
          </div>
        }
      />

      <ErrorBanner error={error} onRetry={() => void loadAll()} />

      {/* Global Analytical Filter Bar */}
      <SectionCard title="Global Filters & Timezone" subtitle="All metrics recalculate via backend REST aggregation with strict UTC persistence">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
          <select
            aria-label="Metrics range"
            value={range}
            onChange={(e) => setRange(e.target.value as TimeRangePreset)}
            className="rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-2 text-xs text-slate-200"
          >
            <option value="24h">Last 24 hours</option>
            <option value="7d">Last 7 days</option>
            <option value="30d">Last 30 days</option>
            <option value="90d">Last 90 days</option>
            <option value="custom">Custom range</option>
          </select>

          <select
            aria-label="Filter repository"
            value={repositoryId}
            onChange={(e) => setRepositoryId(e.target.value)}
            className="rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-2 text-xs text-slate-200"
          >
            <option value="all">All Repositories</option>
            {(filterOpts.repositories ?? []).map((r) => (
              <option key={r.id} value={r.id}>
                {r.full_name}
              </option>
            ))}
          </select>

          <select
            aria-label="Filter environment"
            value={environment}
            onChange={(e) => setEnvironment(e.target.value)}
            className="rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-2 text-xs text-slate-200"
          >
            <option value="all">All Environments</option>
            <option value="development">development</option>
            <option value="staging">staging</option>
            <option value="production">production</option>
          </select>

          <select
            aria-label="Filter feature flag"
            value={flagKey}
            onChange={(e) => setFlagKey(e.target.value)}
            className="rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-2 text-xs text-slate-200"
          >
            <option value="all">All Feature Flags</option>
            {(filterOpts.flags ?? []).map((f) => (
              <option key={f.id} value={f.key}>
                {f.key}
              </option>
            ))}
          </select>

          <select
            aria-label="Filter experiment"
            value={experimentId}
            onChange={(e) => setExperimentId(e.target.value)}
            className="rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-2 text-xs text-slate-200"
          >
            <option value="all">All Experiments</option>
            {(filterOpts.experiments ?? []).map((ex) => (
              <option key={ex.id} value={ex.id}>
                {ex.name}
              </option>
            ))}
          </select>

          <select
            aria-label="Filter event source"
            value={eventSource}
            onChange={(e) =>
              setEventSource(e.target.value as "all" | "simulator_generated" | "application_telemetry")
            }
            className="rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-2 text-xs text-slate-200"
          >
            <option value="all">All Event Sources</option>
            <option value="application_telemetry">App Telemetry Only</option>
            <option value="simulator_generated">Simulator Data Only</option>
          </select>

          <select
            aria-label="Select display timezone"
            value={timezone}
            onChange={(e) => setTimezone(e.target.value)}
            className="rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-2 text-xs text-slate-200"
          >
            <option value="UTC">Timezone: UTC</option>
            <option value="Asia/Kolkata">Asia/Kolkata (IST)</option>
            <option value="America/New_York">America/New_York</option>
            <option value="Europe/London">Europe/London</option>
            <option value="local">Browser Local</option>
          </select>
        </div>

        {range === "custom" && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <input
              type="date"
              aria-label="Custom start"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="rounded border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-200"
            />
            <span className="text-xs text-slate-500">to</span>
            <input
              type="date"
              aria-label="Custom end"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="rounded border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-200"
            />
          </div>
        )}
      </SectionCard>

      {/* Data Source Classification Legend */}
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-800 bg-[#111622] px-4 py-2.5 text-[11px]">
        <span className="font-semibold uppercase tracking-wider text-slate-400">Data Sources:</span>
        <span className="rounded border border-sky-500/30 bg-sky-500/10 px-2 py-0.5 text-sky-300">
          Live External Data (GitHub / Flagsmith)
        </span>
        <span className="rounded border border-indigo-500/30 bg-indigo-500/10 px-2 py-0.5 text-indigo-300">
          Calculated Metric (Bayesian Monte Carlo)
        </span>
        <span className="rounded border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-emerald-300">
          Application Telemetry ({evTotals.application_telemetry_events ?? 0})
        </span>
        <span className="rounded border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-amber-200">
          Simulated Demo Data ({evTotals.simulator_events ?? 0})
        </span>
      </div>

      {/* Section Tabs */}
      <div className="flex flex-wrap gap-2">
        {(
          [
            ["releases", "Release Engineering"],
            ["pull_requests", "Pull Requests"],
            ["feature_flags", "Feature Flags"],
            ["experiments", "Experiments & Bayesian"],
            ["agents", "AI Agent Performance"],
            ["events", "Event Explorer"],
            ["definitions", "Metric Definitions"],
          ] as Array<[MetricsTab, string]>
        ).map(([k, lbl]) => (
          <button
            key={k}
            type="button"
            onClick={() => setTab(k)}
            className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
              tab === k
                ? "border-indigo-500/50 bg-indigo-500/20 text-indigo-200"
                : "border-slate-800 bg-[#111622] text-slate-400 hover:text-slate-200"
            }`}
          >
            {lbl}
          </button>
        ))}
      </div>

      {loading && !overview ? (
        <LoadingSkeleton rows={4} />
      ) : (
        <>
          {tab === "releases" && (
            <div className="space-y-6">
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <MetricCard
                  label="Release Rollout Steps"
                  value={Number(relM.release_frequency_rollouts ?? 0)}
                  subtext="Flagsmith verified transitions (Source: rollout_history)"
                />
                <MetricCard
                  label="Release Success Rate"
                  value={
                    relM.release_success_rate !== null && relM.release_success_rate !== undefined
                      ? `${relM.release_success_rate}%`
                      : "Unavailable"
                  }
                  subtext="Verified non-rollback / completed rollouts"
                  tone="emerald"
                />
                <MetricCard
                  label="Rollback Rate"
                  value={
                    relM.rollback_rate !== null && relM.rollback_rate !== undefined
                      ? `${relM.rollback_rate}%`
                      : "Unavailable"
                  }
                  subtext="Rollbacks / total transitions in window"
                  tone="amber"
                />
                <MetricCard
                  label="CD Deployment Frequency"
                  value="Unavailable"
                  subtext="Draft PRs & flags are NOT mislabeled as deployments"
                />
              </div>

              <div className="grid gap-4 sm:grid-cols-3">
                <MetricCard
                  label="Draft PRs Prepared"
                  value={Number(relM.release_preparation_draft_prs ?? 0)}
                  subtext="Release preparation artifacts (GitHub Draft PRs)"
                />
                <MetricCard
                  label="Mean Time To Recovery"
                  value={
                    relM.mean_time_to_recovery_seconds !== null &&
                    relM.mean_time_to_recovery_seconds !== undefined
                      ? `${relM.mean_time_to_recovery_seconds}s`
                      : "Unavailable"
                  }
                  subtext="Step-to-verified-rollback interval when recorded"
                />
                <MetricCard
                  label="Avg Approval Latency"
                  value={
                    relM.average_approval_duration_seconds !== null &&
                    relM.average_approval_duration_seconds !== undefined
                      ? `${relM.average_approval_duration_seconds}s`
                      : "Unavailable"
                  }
                  subtext="Approval creation to decision timestamp"
                />
              </div>

              <SectionCard title="Recent Release Rollout Operations" subtitle={`Displayed in timezone: ${timezone}`}>
                {((releasesData?.recent_operations as Array<Record<string, unknown>>) ?? []).length === 0 ? (
                  <EmptyState title="No rollout operations in window" description="Adjust filters or step a feature flag in Releases." />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs">
                      <thead className="border-b border-slate-800 text-slate-400">
                        <tr>
                          <th className="py-2 pr-3">Flag</th>
                          <th className="py-2 pr-3">Environment</th>
                          <th className="py-2 pr-3">Action</th>
                          <th className="py-2 pr-3">Verified</th>
                          <th className="py-2 pr-3">Reason</th>
                          <th className="py-2">Time</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800/70">
                        {((releasesData?.recent_operations as Array<Record<string, unknown>>) ?? []).map((r) => (
                          <tr key={String(r.id)}>
                            <td className="py-2 pr-3 font-mono text-slate-100">{String(r.flag_key ?? "—")}</td>
                            <td className="py-2 pr-3">
                              <EnvironmentBadge environment={String(r.environment_name)} />
                            </td>
                            <td className="py-2 pr-3 font-mono text-slate-300">{String(r.action)}</td>
                            <td className="py-2 pr-3">
                              <StatusBadge status={r.verified ? "verified" : "failed"} />
                            </td>
                            <td className="py-2 pr-3 text-slate-400">{String(r.reason ?? "—")}</td>
                            <td className="py-2 font-mono text-slate-400">
                              {formatInTimezone(String(r.created_at), timezone)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </SectionCard>
            </div>
          )}

          {tab === "pull_requests" && (
            <div className="space-y-6">
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
                <MetricCard label="Total PRs" value={Number(prM.total_prs ?? 0)} subtext="Within selected window" />
                <MetricCard label="Analysed PRs" value={Number(prM.analyzed_prs ?? 0)} subtext="Completed AI risk audits" tone="emerald" />
                <MetricCard label="High / Critical Risk" value={Number(prM.high_risk_prs ?? 0)} tone="rose" />
                <MetricCard label="Draft PRs Generated" value={Number(prM.draft_prs_generated ?? 0)} />
                <MetricCard
                  label="Patch Validation Rate"
                  value={
                    prM.patch_validation_success_rate !== null &&
                    prM.patch_validation_success_rate !== undefined
                      ? `${prM.patch_validation_success_rate}%`
                      : "Unavailable"
                  }
                  subtext={`${Number(prM.patch_validation_passed ?? 0)} / ${Number(prM.patch_validation_total ?? 0)} checks passed`}
                />
              </div>

              <div className="grid gap-6 lg:grid-cols-2">
                <SectionCard title="PR Risk Distribution" subtitle="Source: pull_requests (GitHub + Gemini analysis)">
                  {hydrated ? (
                    <div className="h-60 w-full">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={(prData?.risk_distribution as Array<Record<string, unknown>>) ?? []}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                          <XAxis dataKey="level" stroke="#94a3b8" tick={{ fontSize: 11 }} />
                          <YAxis stroke="#94a3b8" allowDecimals={false} tick={{ fontSize: 11 }} />
                          <Tooltip contentStyle={{ backgroundColor: "#0f172a", borderColor: "#334155", borderRadius: 8 }} />
                          <Bar dataKey="count" fill="#6366f1" radius={[4, 4, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  ) : null}
                </SectionCard>

                <SectionCard title="Pull Requests by Repository" subtitle="Synced GitHub repositories with PR counts">
                  {((prData?.by_repository as Array<Record<string, unknown>>) ?? []).length === 0 ? (
                    <EmptyState title="No PRs in window" description="Sync PRs from Repositories or Pull Requests." />
                  ) : (
                    <table className="w-full text-left text-xs">
                      <thead className="border-b border-slate-800 text-slate-400">
                        <tr>
                          <th className="py-2 pr-3">Repository</th>
                          <th className="py-2 pr-3">Total</th>
                          <th className="py-2 pr-3">Analysed</th>
                          <th className="py-2 pr-3">Open</th>
                          <th className="py-2">High Risk</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800/70">
                        {((prData?.by_repository as Array<Record<string, unknown>>) ?? []).map((r) => (
                          <tr key={String(r.repository_id)}>
                            <td className="py-2 pr-3 font-mono text-slate-100">{String(r.full_name)}</td>
                            <td className="py-2 pr-3 tabular-nums">{String(r.total_prs)}</td>
                            <td className="py-2 pr-3 tabular-nums text-emerald-300">{String(r.analyzed_prs)}</td>
                            <td className="py-2 pr-3 tabular-nums">{String(r.open_prs)}</td>
                            <td className="py-2 tabular-nums text-amber-300">{String(r.high_risk_prs)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </SectionCard>
              </div>
            </div>
          )}

          {tab === "feature_flags" && (
            <div className="space-y-6">
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
                <MetricCard label="Total Flags" value={Number(flM.total_flags ?? 0)} />
                <MetricCard label="Active (100%)" value={Number(flM.active_flags ?? 0)} tone="emerald" />
                <MetricCard label="Partial Rollout" value={Number(flM.gradual_rollout_flags ?? 0)} tone="sky" />
                <MetricCard label="State Changes" value={Number(flM.flag_state_changes ?? 0)} />
                <MetricCard label="Config Errors" value={Number(flM.configuration_errors ?? 0)} tone="rose" />
              </div>

              <SectionCard title="Environment-Specific Flag Configuration" subtitle="Live Flagsmith synced state across development, staging, and production">
                {((flagData?.flags as Array<Record<string, unknown>>) ?? []).length === 0 ? (
                  <EmptyState title="No flags synced" description="Connect Flagsmith and sync flags." />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs">
                      <thead className="border-b border-slate-800 text-slate-400">
                        <tr>
                          <th className="py-2 pr-3">Flag Key</th>
                          <th className="py-2 pr-3">Health Score</th>
                          <th className="py-2 pr-3">State Changes</th>
                          <th className="py-2 pr-3">Rollbacks</th>
                          <th className="py-2">Environment Allocations (Live Flagsmith)</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800/70">
                        {((flagData?.flags as Array<Record<string, unknown>>) ?? []).map((f) => {
                          const envs = (f.environments as Record<string, { enabled: boolean; rollout_percentage: number }>) ?? {};
                          return (
                            <tr key={String(f.id)}>
                              <td className="py-2.5 pr-3 font-mono font-semibold text-slate-100">{String(f.key)}</td>
                              <td className="py-2.5 pr-3 font-mono">{String(f.health_score)}/100</td>
                              <td className="py-2.5 pr-3 tabular-nums">{String(f.state_changes)}</td>
                              <td className="py-2.5 pr-3 tabular-nums">{String(f.rollbacks)}</td>
                              <td className="py-2.5">
                                <div className="flex flex-wrap gap-2">
                                  {Object.entries(envs).map(([envName, st]) => (
                                    <span
                                      key={envName}
                                      className="inline-flex items-center gap-1.5 rounded border border-slate-800 bg-[#0d1119] px-2 py-0.5 font-mono text-[11px]"
                                    >
                                      <EnvironmentBadge environment={envName} />
                                      <span className={st.enabled || st.rollout_percentage > 0 ? "text-emerald-300" : "text-slate-400"}>
                                        {st.rollout_percentage}%{st.enabled ? " (enabled)" : ""}
                                      </span>
                                    </span>
                                  ))}
                                </div>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </SectionCard>
            </div>
          )}

          {tab === "experiments" && (
            <div className="space-y-6">
              <div className="rounded-xl border border-indigo-500/30 bg-indigo-500/5 px-4 py-3 text-xs text-indigo-200">
                {String(expData?.statistical_note ?? "Calculated strictly via Beta-Binomial Monte Carlo.")}
              </div>

              {experiments.length === 0 ? (
                <EmptyState title="No experiments matched filters" description="Create an experiment or clear filters." />
              ) : (
                <div className="space-y-4">
                  {experiments.map((ex) => (
                    <SectionCard
                      key={ex.experiment_id}
                      title={`${ex.name} (${ex.environment_name})`}
                      subtitle={ex.hypothesis}
                      actions={
                        <div className="flex flex-wrap items-center gap-2">
                          <StatusBadge status={ex.status} />
                          <RecommendationBadge recommendation={ex.recommendation} />
                        </div>
                      }
                    >
                      <div className="grid gap-4 md:grid-cols-4 text-xs">
                        <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3">
                          <div className="text-slate-400">Control ({ex.control_exposures} users)</div>
                          <div className="mt-1 text-lg font-semibold text-slate-100 tabular-nums">
                            {(ex.control_rate * 100).toFixed(2)}%
                          </div>
                          <div className="text-[11px] text-slate-400">
                            {ex.control_conversions} conversions · err {(ex.control_error_rate * 100).toFixed(2)}%
                          </div>
                        </div>
                        <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3">
                          <div className="text-slate-400">Treatment ({ex.treatment_exposures} users)</div>
                          <div className="mt-1 text-lg font-semibold text-sky-300 tabular-nums">
                            {(ex.treatment_rate * 100).toFixed(2)}%
                          </div>
                          <div className="text-[11px] text-slate-400">
                            {ex.treatment_conversions} conversions · err {(ex.treatment_error_rate * 100).toFixed(2)}%
                          </div>
                        </div>
                        <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3">
                          <div className="text-slate-400">Bayesian Lift & Loss</div>
                          <div className="mt-1 text-sm font-semibold text-slate-100 tabular-nums">
                            Abs {(ex.absolute_improvement * 100).toFixed(2)}pp · Rel {(ex.relative_improvement * 100).toFixed(1)}%
                          </div>
                          <div className="text-[11px] text-slate-400">
                            P(T&gt;C) {(ex.prob_treatment_beats_control * 100).toFixed(1)}% · Loss {(ex.expected_loss_treatment * 100).toFixed(3)}%
                          </div>
                        </div>
                        <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3">
                          <div className="text-slate-400">Telemetry & Guardrails</div>
                          <div className="mt-1 text-sm font-semibold text-slate-100">
                            {ex.payment_failed_breached ? (
                              <span className="text-rose-300">Guardrail Breached ({ex.payment_failed_severity})</span>
                            ) : (
                              <span className="text-emerald-300">Guardrails Healthy</span>
                            )}
                          </div>
                          <div className="text-[11px] text-slate-400">
                            Sim events: {ex.simulator_event_count} · Live: {ex.telemetry_event_count}
                          </div>
                        </div>
                      </div>
                    </SectionCard>
                  ))}
                </div>
              )}
            </div>
          )}

          {tab === "agents" && (
            <div className="space-y-6">
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
                <MetricCard label="Total Runs" value={Number(agM.total_runs ?? 0)} />
                <MetricCard label="Completed" value={Number(agM.completed_runs ?? 0)} tone="emerald" />
                <MetricCard label="Failed / Errors" value={Number(agM.failed_runs ?? 0)} tone="rose" />
                <MetricCard
                  label="Tool Success Rate"
                  value={
                    agM.tool_success_rate !== null && agM.tool_success_rate !== undefined
                      ? `${agM.tool_success_rate}%`
                      : "Unavailable"
                  }
                  subtext={`${Number(agM.tool_invocation_count ?? 0)} tool calls`}
                />
                <MetricCard
                  label="Recorded Tokens"
                  value={agM.total_tokens !== null && agM.total_tokens !== undefined ? Number(agM.total_tokens) : "Unavailable"}
                  subtext="Exact logged API tokens (cost unavailable)"
                />
              </div>

              <SectionCard title="Model Breakdown & Recent Agent Executions" subtitle="Token cost intentionally omitted unless pricing card is configured">
                {((agentData?.by_model as Array<Record<string, unknown>>) ?? []).length === 0 ? (
                  <EmptyState title="No agent runs recorded" description="Invoke the Gemini agent in AI Agent to gather metrics." />
                ) : (
                  <div className="space-y-4">
                    <table className="w-full text-left text-xs">
                      <thead className="border-b border-slate-800 text-slate-400">
                        <tr>
                          <th className="py-2 pr-3">Gemini Model</th>
                          <th className="py-2 pr-3">Runs</th>
                          <th className="py-2 pr-3">Completed</th>
                          <th className="py-2 pr-3">Failed</th>
                          <th className="py-2">Logged Tokens</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800/70">
                        {((agentData?.by_model as Array<Record<string, unknown>>) ?? []).map((row) => (
                          <tr key={String(row.model)}>
                            <td className="py-2 pr-3 font-mono text-cyan-300">{String(row.model)}</td>
                            <td className="py-2 pr-3 tabular-nums">{String(row.runs)}</td>
                            <td className="py-2 pr-3 tabular-nums text-emerald-300">{String(row.completed)}</td>
                            <td className="py-2 pr-3 tabular-nums text-rose-300">{String(row.failed)}</td>
                            <td className="py-2 font-mono tabular-nums">{String(row.tokens)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <div className="text-xs text-slate-400">
                      Estimated USD Task Cost: <UnavailableValue reason={String(agM.cost_note ?? "")} />
                    </div>
                  </div>
                )}
              </SectionCard>
            </div>
          )}

          {tab === "events" && (
            <div className="space-y-6">
              <div className="grid gap-4 sm:grid-cols-3">
                <MetricCard label="Total Filtered Events" value={evTotals.total_events ?? 0} />
                <MetricCard label="App Telemetry Events" value={evTotals.application_telemetry_events ?? 0} tone="emerald" />
                <MetricCard label="Simulator Demo Events" value={evTotals.simulator_events ?? 0} tone="amber" />
              </div>

              <SectionCard title="Ingested Events by Type" subtitle="Source: experiment_events">
                {hydrated && ((eventData?.by_event_type as Array<Record<string, unknown>>) ?? []).length > 0 ? (
                  <div className="h-56 w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={(eventData?.by_event_type as Array<Record<string, unknown>>) ?? []}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                        <XAxis dataKey="event_type" stroke="#94a3b8" tick={{ fontSize: 11 }} />
                        <YAxis stroke="#94a3b8" allowDecimals={false} tick={{ fontSize: 11 }} />
                        <Tooltip contentStyle={{ backgroundColor: "#0f172a", borderColor: "#334155", borderRadius: 8 }} />
                        <Legend />
                        <Bar dataKey="count" name="Event Count" fill="#38bdf8" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <EmptyState title="No matching events" description="Send NovaShop checkouts or adjust filters." />
                )}
              </SectionCard>

              <SectionCard title="Recent Event Explorer (Latest 50)" subtitle={`Displayed in ${timezone}`}>
                {((eventData?.recent_events as Array<Record<string, unknown>>) ?? []).length === 0 ? (
                  <EmptyState title="No events" description="No events match current filter." />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs">
                      <thead className="border-b border-slate-800 text-slate-400">
                        <tr>
                          <th className="py-2 pr-3">Event Type</th>
                          <th className="py-2 pr-3">Flag</th>
                          <th className="py-2 pr-3">Env</th>
                          <th className="py-2 pr-3">Variation</th>
                          <th className="py-2 pr-3">Source Class</th>
                          <th className="py-2">Occurred At</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800/70">
                        {((eventData?.recent_events as Array<Record<string, unknown>>) ?? []).map((ev) => (
                          <tr key={String(ev.event_id)}>
                            <td className="py-2 pr-3 font-mono text-slate-100">{String(ev.event_type)}</td>
                            <td className="py-2 pr-3 font-mono">{String(ev.flag_key)}</td>
                            <td className="py-2 pr-3">
                              <EnvironmentBadge environment={String(ev.environment_name)} />
                            </td>
                            <td className="py-2 pr-3 font-mono">{String(ev.variation_key)}</td>
                            <td className="py-2 pr-3">
                              <span
                                className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                                  ev.source_classification === "simulator_generated"
                                    ? "bg-amber-500/15 text-amber-200"
                                    : "bg-emerald-500/15 text-emerald-300"
                                }`}
                              >
                                {String(ev.source_classification)}
                              </span>
                            </td>
                            <td className="py-2 font-mono text-slate-400">
                              {formatInTimezone(String(ev.occurred_at), timezone)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </SectionCard>
            </div>
          )}

          {tab === "definitions" && (
            <SectionCard
              title="Metric Definitions, Formulas & Data Boundaries"
              subtitle="Documented definitions enforced by ReleaseGuard AI (Section 12)"
            >
              <div className="space-y-4">
                {definitions.map((def) => (
                  <div
                    key={String(def.name)}
                    className="rounded-lg border border-slate-800 bg-[#0d1119] p-4 text-xs space-y-1.5"
                  >
                    <div className="flex items-center gap-2 font-semibold text-slate-100">
                      <BookOpen className="h-3.5 w-3.5 text-indigo-400" />
                      {String(def.name)}
                    </div>
                    <p className="text-slate-300">{String(def.definition)}</p>
                    <div className="font-mono text-[11px] text-cyan-300">
                      Formula: {String(def.formula)}
                    </div>
                    <div className="text-slate-400">
                      Source: {String(def.source)} · Window: {String(def.time_window)}
                    </div>
                    <div className="text-[11px] text-amber-200/90">
                      Limitation: {String(def.limitations)}
                    </div>
                  </div>
                ))}
              </div>
            </SectionCard>
          )}
        </>
      )}
    </div>
  );
}
