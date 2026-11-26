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
import {
  Download,
  RefreshCw,
  BookOpen,
  Info,
  Play,
  AlertTriangle,
  Clock,
  ShieldCheck,
  ChevronDown,
  ChevronRight,
  Gauge,
} from "lucide-react";
import {
  getAgentMetrics,
  getBenchmarkSummary,
  getErrorMessage,
  getEventMetrics,
  getExperimentMetrics,
  getFeatureFlagMetrics,
  getMetricsExportUrl,
  getMetricsOverview,
  getPrMetrics,
  getReleaseMetrics,
  listBenchmarkRuns,
  triggerBenchmarkRun,
  type BenchmarkEvidenceClassification,
  type BenchmarkGroupMetrics,
  type BenchmarkRunRecord,
  type BenchmarkSummaryResponse,
  type BenchmarkWorkflowType,
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
  | "benchmarks"
  | "releases"
  | "pull_requests"
  | "feature_flags"
  | "experiments"
  | "agents"
  | "events"
  | "definitions";

const WORKFLOW_LABELS: Record<BenchmarkWorkflowType, string> = {
  pr_review: "Workflow A: PR Review & Risk Audit",
  flag_setup: "Workflow B: Feature Flag Setup",
  experiment_setup: "Workflow C: Experiment Setup",
  release_decision: "Workflow D: Release Decision",
  complete_release: "Workflow E: Complete Release",
};

const EVIDENCE_META: Record<
  BenchmarkEvidenceClassification,
  { label: string; tone: string; description: string }
> = {
  VERIFIED_MEASURED: {
    label: "VERIFIED_MEASURED",
    tone: "border-emerald-500/35 bg-emerald-500/10 text-emerald-300",
    description: "Live API/execution with monotonic wall-clock timing",
  },
  ESTIMATED: {
    label: "ESTIMATED",
    tone: "border-sky-500/35 bg-sky-500/10 text-sky-300",
    description: "Documented manual engineering baseline or estimate",
  },
  SIMULATED: {
    label: "SIMULATED",
    tone: "border-amber-500/35 bg-amber-500/10 text-amber-200",
    description: "Non-live local/simulated harness execution",
  },
  INCOMPLETE: {
    label: "INCOMPLETE",
    tone: "border-slate-600/60 bg-slate-800/80 text-slate-300",
    description: "Partial or aborted run with missing stages/baseline",
  },
};

function EvidenceBadge({ classification }: { classification: BenchmarkEvidenceClassification | string }) {
  const meta =
    EVIDENCE_META[classification as BenchmarkEvidenceClassification] ?? {
      label: String(classification || "UNKNOWN"),
      tone: "border-slate-700 bg-slate-800/80 text-slate-300",
      description: "Unclassified evidence",
    };
  return (
    <span
      title={meta.description}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold tracking-tight ${meta.tone}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current opacity-85" />
      {meta.label}
    </span>
  );
}

function formatDurationSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) {
    return "Unavailable";
  }
  if (seconds >= 60) {
    const mins = Math.floor(seconds / 60);
    const rem = seconds % 60;
    return `${mins}m ${rem.toFixed(1)}s`;
  }
  return `${seconds.toFixed(2)}s`;
}

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
  const [tab, setTab] = useState<MetricsTab>("benchmarks");
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

  // Developer Productivity & Benchmark state
  const [benchWorkflowFilter, setBenchWorkflowFilter] = useState<BenchmarkWorkflowType | "all">("all");
  const [benchEnvFilter, setBenchEnvFilter] = useState<"all" | "development" | "staging">("all");
  const [benchEvidenceFilter, setBenchEvidenceFilter] = useState<
    BenchmarkEvidenceClassification | "all"
  >("all");
  const [benchSummary, setBenchSummary] = useState<BenchmarkSummaryResponse | null>(null);
  const [benchRuns, setBenchRuns] = useState<BenchmarkRunRecord[]>([]);
  const [benchLoading, setBenchLoading] = useState(false);
  const [benchError, setBenchError] = useState<string | null>(null);
  const [expandedRunId, setExpandedRunId] = useState<string | null>(null);

  // Run Benchmark trigger form state (strictly development / staging only)
  const [triggerWorkflow, setTriggerWorkflow] = useState<BenchmarkWorkflowType>("flag_setup");
  const [triggerEnv, setTriggerEnv] = useState<"development" | "staging">("development");
  const [triggerScenario, setTriggerScenario] = useState("default");
  const [triggerRequireLive, setTriggerRequireLive] = useState(false);
  const [triggerIncludeBaseline, setTriggerIncludeBaseline] = useState(true);
  const [triggerRunning, setTriggerRunning] = useState(false);
  const [triggerResult, setTriggerResult] = useState<BenchmarkRunRecord | null>(null);

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

  const loadBenchmarks = useCallback(async () => {
    setBenchLoading(true);
    setBenchError(null);
    try {
      const filters = {
        workflow_type: benchWorkflowFilter !== "all" ? benchWorkflowFilter : undefined,
        environment: benchEnvFilter !== "all" ? benchEnvFilter : undefined,
        evidence_classification:
          benchEvidenceFilter !== "all" ? benchEvidenceFilter : undefined,
      };
      const [summaryRes, runsRes] = await Promise.all([
        getBenchmarkSummary({ ...filters, limit: 200 }),
        listBenchmarkRuns({ ...filters, limit: 50, offset: 0 }),
      ]);
      setBenchSummary(summaryRes);
      setBenchRuns(runsRes.runs ?? []);
    } catch (err) {
      setBenchError(getErrorMessage(err));
    } finally {
      setBenchLoading(false);
    }
  }, [benchWorkflowFilter, benchEnvFilter, benchEvidenceFilter]);

  const handleTriggerBenchmark = useCallback(async () => {
    setTriggerRunning(true);
    setBenchError(null);
    setTriggerResult(null);
    try {
      const res = await triggerBenchmarkRun({
        workflow_type: triggerWorkflow,
        environment: triggerEnv,
        scenario: triggerScenario.trim() || "default",
        require_live: triggerRequireLive,
        include_baseline: triggerIncludeBaseline,
      });
      setTriggerResult(res.run);
      await loadBenchmarks();
    } catch (err) {
      setBenchError(getErrorMessage(err));
    } finally {
      setTriggerRunning(false);
    }
  }, [
    triggerWorkflow,
    triggerEnv,
    triggerScenario,
    triggerRequireLive,
    triggerIncludeBaseline,
    loadBenchmarks,
  ]);

  useEffect(() => {
    const t = setTimeout(() => {
      void loadAll();
    }, 0);
    return () => clearTimeout(t);
  }, [loadAll]);

  useEffect(() => {
    const t = setTimeout(() => {
      void loadBenchmarks();
    }, 0);
    return () => clearTimeout(t);
  }, [loadBenchmarks]);

  const filterOpts = (overview?.filter_options as Record<string, Array<Record<string, string>>>) ?? {};
  const definitions = (overview?.metric_definitions as Array<Record<string, unknown>>) ?? [];

  const relM = (releasesData?.metrics as Record<string, unknown>) ?? {};
  const prM = (prData?.metrics as Record<string, unknown>) ?? {};
  const flM = (flagData?.metrics as Record<string, unknown>) ?? {};
  const agM = (agentData?.metrics as Record<string, unknown>) ?? {};
  const evTotals = (eventData?.totals as Record<string, number>) ?? {};
  const experiments = (expData?.experiments as ExperimentPerformancePoint[]) ?? [];

  const exportSection: "releases" | "pull_requests" | "feature_flags" | "experiments" | "agents" | "events" =
    tab === "definitions" || tab === "benchmarks" ? "releases" : tab;

  const benchOverall = benchSummary?.overall;
  const benchBaselineComp = benchOverall?.baseline_comparison ?? null;
  const benchStageChartData = useMemo(
    () =>
      (benchSummary?.stage_comparison ?? []).map((st) => ({
        stage: st.stage_name,
        manual_seconds: st.manual_baseline_seconds ?? 0,
        automated_seconds: st.mean_duration_seconds,
      })),
    [benchSummary]
  );

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
              onClick={() => {
                void loadAll();
                void loadBenchmarks();
              }}
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
            ["benchmarks", "Productivity & Benchmarks"],
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

      {loading && !overview && tab !== "benchmarks" ? (
        <LoadingSkeleton rows={4} />
      ) : (
        <>
          {tab === "benchmarks" && (
            <div className="space-y-6">
              <ErrorBanner error={benchError} onRetry={() => void loadBenchmarks()} />

              {/* Evidence Classification Legend & Filters */}
              <SectionCard
                title="Developer Productivity & Benchmark Comparison"
                subtitle="Monotonic wall-clock timings vs documented manual engineering baselines. Evidence categories are never merged silently."
                actions={
                  <div className="flex flex-wrap items-center gap-2">
                    <select
                      aria-label="Filter benchmark workflow"
                      value={benchWorkflowFilter}
                      onChange={(e) =>
                        setBenchWorkflowFilter(e.target.value as BenchmarkWorkflowType | "all")
                      }
                      className="rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-200"
                    >
                      <option value="all">All Workflows (A–E)</option>
                      {(Object.keys(WORKFLOW_LABELS) as BenchmarkWorkflowType[]).map((wf) => (
                        <option key={wf} value={wf}>
                          {WORKFLOW_LABELS[wf]}
                        </option>
                      ))}
                    </select>
                    <select
                      aria-label="Filter benchmark environment"
                      value={benchEnvFilter}
                      onChange={(e) =>
                        setBenchEnvFilter(e.target.value as "all" | "development" | "staging")
                      }
                      className="rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-200"
                    >
                      <option value="all">Non-Prod Envs (All)</option>
                      <option value="development">development</option>
                      <option value="staging">staging</option>
                    </select>
                    <select
                      aria-label="Filter evidence classification"
                      value={benchEvidenceFilter}
                      onChange={(e) =>
                        setBenchEvidenceFilter(
                          e.target.value as BenchmarkEvidenceClassification | "all"
                        )
                      }
                      className="rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-200"
                    >
                      <option value="all">All Evidence Classes</option>
                      <option value="VERIFIED_MEASURED">VERIFIED_MEASURED</option>
                      <option value="ESTIMATED">ESTIMATED</option>
                      <option value="SIMULATED">SIMULATED</option>
                      <option value="INCOMPLETE">INCOMPLETE</option>
                    </select>
                  </div>
                }
              >
                <div className="flex flex-wrap items-center gap-2 text-[11px]">
                  <span className="font-semibold uppercase tracking-wider text-slate-400">
                    Evidence Classes:
                  </span>
                  {(Object.keys(EVIDENCE_META) as BenchmarkEvidenceClassification[]).map((cls) => (
                    <span key={cls} className="inline-flex items-center gap-1.5">
                      <EvidenceBadge classification={cls} />
                      <span className="text-slate-400">{EVIDENCE_META[cls].description}</span>
                    </span>
                  ))}
                </div>

                {benchSummary?.mixed_evidence_warning && (
                  <div className="mt-4 flex items-start gap-2.5 rounded-lg border border-amber-500/35 bg-amber-500/10 px-3.5 py-2.5 text-xs text-amber-200">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
                    <div>
                      <span className="font-semibold">Mixed Evidence Notice: </span>
                      {benchSummary.mixed_evidence_warning}
                    </div>
                  </div>
                )}
              </SectionCard>

              {/* Run Benchmark Trigger (development / staging ONLY) */}
              <SectionCard
                title="Run Non-Production Benchmark"
                subtitle="Triggers a repeatable benchmark run with monotonic timing capture. Production is strictly blocked."
                actions={
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 text-[11px] font-medium text-emerald-300">
                    <ShieldCheck className="h-3.5 w-3.5" />
                    Non-Prod Only (dev / staging)
                  </span>
                }
              >
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                  <div>
                    <label className="mb-1 block text-[11px] font-medium text-slate-400">
                      Workflow
                    </label>
                    <select
                      aria-label="Trigger benchmark workflow"
                      value={triggerWorkflow}
                      onChange={(e) => setTriggerWorkflow(e.target.value as BenchmarkWorkflowType)}
                      className="w-full rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-2 text-xs text-slate-200"
                    >
                      {(Object.keys(WORKFLOW_LABELS) as BenchmarkWorkflowType[]).map((wf) => (
                        <option key={wf} value={wf}>
                          {WORKFLOW_LABELS[wf]}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <label className="mb-1 block text-[11px] font-medium text-slate-400">
                      Environment (production blocked)
                    </label>
                    <select
                      aria-label="Trigger benchmark environment"
                      value={triggerEnv}
                      onChange={(e) =>
                        setTriggerEnv(e.target.value as "development" | "staging")
                      }
                      className="w-full rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-2 text-xs text-slate-200"
                    >
                      <option value="development">development</option>
                      <option value="staging">staging</option>
                    </select>
                  </div>

                  <div>
                    <label className="mb-1 block text-[11px] font-medium text-slate-400">
                      Scenario Label
                    </label>
                    <input
                      type="text"
                      aria-label="Benchmark scenario"
                      value={triggerScenario}
                      onChange={(e) => setTriggerScenario(e.target.value)}
                      placeholder="default"
                      className="w-full rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-2 text-xs text-slate-200"
                    />
                  </div>

                  <div className="flex flex-col justify-end gap-2 pb-1 text-xs text-slate-300">
                    <label className="inline-flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={triggerRequireLive}
                        onChange={(e) => setTriggerRequireLive(e.target.checked)}
                        className="rounded border-slate-700 bg-slate-900"
                      />
                      Require live integrations
                    </label>
                    <label className="inline-flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={triggerIncludeBaseline}
                        onChange={(e) => setTriggerIncludeBaseline(e.target.checked)}
                        className="rounded border-slate-700 bg-slate-900"
                      />
                      Include manual baseline
                    </label>
                  </div>

                  <div className="flex items-end">
                    <button
                      type="button"
                      disabled={triggerRunning}
                      onClick={() => void handleTriggerBenchmark()}
                      className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
                    >
                      <Play className="h-3.5 w-3.5" />
                      {triggerRunning ? "Running Benchmark..." : "Run Benchmark"}
                    </button>
                  </div>
                </div>

                {triggerResult && (
                  <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-emerald-500/30 bg-emerald-500/5 px-4 py-3 text-xs">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold text-emerald-300">Completed:</span>
                      <span className="font-mono text-slate-100">{triggerResult.benchmark_id}</span>
                      <EvidenceBadge classification={triggerResult.evidence_classification} />
                      <EnvironmentBadge environment={triggerResult.environment} />
                      <StatusBadge status={triggerResult.success ? "success" : "failed"} />
                    </div>
                    <div className="font-mono text-slate-200">
                      Duration: {formatDurationSeconds(triggerResult.total_duration_seconds)} · API
                      calls: {triggerResult.api_call_count}
                    </div>
                  </div>
                )}
              </SectionCard>

              {benchLoading && !benchSummary ? (
                <LoadingSkeleton rows={4} />
              ) : (
                <>
                  {/* KPI Summary Cards */}
                  <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
                    <MetricCard
                      label="Manual vs Automated"
                      value={
                        benchBaselineComp
                          ? `${formatDurationSeconds(benchBaselineComp.mean_manual_baseline_seconds)} → ${formatDurationSeconds(benchBaselineComp.mean_automated_seconds)}`
                          : benchOverall?.duration_stats?.mean !== null &&
                              benchOverall?.duration_stats?.mean !== undefined
                            ? formatDurationSeconds(benchOverall.duration_stats.mean)
                            : "Unavailable"
                      }
                      subtext={
                        benchOverall?.duration_stats?.p95 !== null &&
                        benchOverall?.duration_stats?.p95 !== undefined
                          ? `Median ${formatDurationSeconds(benchOverall.duration_stats.median)} · p95 ${formatDurationSeconds(benchOverall.duration_stats.p95)}`
                          : "No benchmark runs recorded yet"
                      }
                      icon={<Clock className="h-4 w-4" />}
                    />
                    <MetricCard
                      label="Time Saved"
                      value={
                        benchBaselineComp && benchBaselineComp.pct_saved !== null
                          ? `${formatDurationSeconds(benchBaselineComp.mean_time_saved_seconds)} (${benchBaselineComp.pct_saved}%)`
                          : "Omitted"
                      }
                      subtext={
                        benchBaselineComp
                          ? `Across ${benchBaselineComp.runs_with_baseline} run(s) with documented baseline`
                          : "No manual baseline attached (% saved not fabricated)"
                      }
                      tone={benchBaselineComp ? "emerald" : "default"}
                    />
                    <MetricCard
                      label="Success vs Failed"
                      value={`${benchOverall?.successful_runs ?? 0} / ${benchOverall?.failed_runs ?? 0}`}
                      subtext={`Completion ${((benchOverall?.completion_rate ?? 0) * 100).toFixed(1)}% · Retry rate ${((benchOverall?.retry_rate ?? 0) * 100).toFixed(1)}%`}
                      tone={
                        (benchOverall?.failed_runs ?? 0) > 0 ? "amber" : "emerald"
                      }
                    />
                    <MetricCard
                      label="Automation Coverage"
                      value={`${(benchOverall?.automation_coverage_pct ?? 0).toFixed(1)}%`}
                      subtext="Unattended execution / total duration"
                      tone="sky"
                      icon={<Gauge className="h-4 w-4" />}
                    />
                    <MetricCard
                      label="Human vs Unattended"
                      value={`${formatDurationSeconds(benchOverall?.mean_active_developer_seconds ?? 0)} / ${formatDurationSeconds(benchOverall?.mean_unattended_execution_seconds ?? 0)}`}
                      subtext={`Intervention freq ${((benchOverall?.human_intervention_frequency ?? 0) * 100).toFixed(1)}% · Approval ${formatDurationSeconds(benchOverall?.mean_approval_latency_seconds)}`}
                      tone="indigo"
                    />
                  </div>

                  {/* Unmixed Breakdown by Evidence Classification */}
                  {benchSummary &&
                    Object.keys(benchSummary.by_evidence_classification ?? {}).length > 0 && (
                      <SectionCard
                        title="Unmixed Metrics by Evidence Classification"
                        subtitle="Each evidence class is aggregated independently so VERIFIED_MEASURED runs are never blended with ESTIMATED or SIMULATED runs."
                      >
                        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                          {Object.entries(benchSummary.by_evidence_classification).map(
                            ([cls, grp]: [string, BenchmarkGroupMetrics]) => (
                              <div
                                key={cls}
                                className="rounded-lg border border-slate-800 bg-[#0d1119] p-3.5 text-xs space-y-2"
                              >
                                <div className="flex items-center justify-between gap-2">
                                  <EvidenceBadge classification={cls} />
                                  <span className="font-mono text-slate-400">
                                    {grp.total_runs} run{grp.total_runs === 1 ? "" : "s"}
                                  </span>
                                </div>
                                <div className="grid grid-cols-2 gap-2 pt-1">
                                  <div>
                                    <div className="text-[10px] text-slate-400">Mean / p95</div>
                                    <div className="font-mono font-semibold text-slate-100">
                                      {formatDurationSeconds(grp.duration_stats.mean)} /{" "}
                                      {formatDurationSeconds(grp.duration_stats.p95)}
                                    </div>
                                  </div>
                                  <div>
                                    <div className="text-[10px] text-slate-400">Success / Fail</div>
                                    <div className="font-mono font-semibold text-slate-100">
                                      {grp.successful_runs} / {grp.failed_runs}
                                    </div>
                                  </div>
                                  <div>
                                    <div className="text-[10px] text-slate-400">Automation</div>
                                    <div className="font-mono text-sky-300">
                                      {grp.automation_coverage_pct.toFixed(1)}%
                                    </div>
                                  </div>
                                  <div>
                                    <div className="text-[10px] text-slate-400">Time Saved</div>
                                    <div className="font-mono text-emerald-300">
                                      {grp.baseline_comparison?.pct_saved !== null &&
                                      grp.baseline_comparison?.pct_saved !== undefined
                                        ? `${grp.baseline_comparison.pct_saved}%`
                                        : "Omitted"}
                                    </div>
                                  </div>
                                </div>
                              </div>
                            )
                          )}
                        </div>
                      </SectionCard>
                    )}

                  {/* Stage-by-Stage Comparison Chart & Table */}
                  <div className="grid gap-6 lg:grid-cols-2">
                    <SectionCard
                      title="Manual vs Automated Stage Duration (Seconds)"
                      subtitle="Manual baseline (ESTIMATED) compared against mean automated stage duration"
                    >
                      {hydrated && benchStageChartData.length > 0 ? (
                        <div className="h-64 w-full">
                          <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={benchStageChartData}>
                              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                              <XAxis
                                dataKey="stage"
                                stroke="#94a3b8"
                                tick={{ fontSize: 10 }}
                                interval={0}
                                angle={-15}
                                textAnchor="end"
                                height={50}
                              />
                              <YAxis stroke="#94a3b8" tick={{ fontSize: 11 }} />
                              <Tooltip
                                contentStyle={{
                                  backgroundColor: "#0f172a",
                                  borderColor: "#334155",
                                  borderRadius: 8,
                                }}
                              />
                              <Legend />
                              <Bar
                                dataKey="manual_seconds"
                                name="Manual Baseline (ESTIMATED, s)"
                                fill="#64748b"
                                radius={[4, 4, 0, 0]}
                              />
                              <Bar
                                dataKey="automated_seconds"
                                name="Automated Mean (s)"
                                fill="#10b981"
                                radius={[4, 4, 0, 0]}
                              />
                            </BarChart>
                          </ResponsiveContainer>
                        </div>
                      ) : (
                        <EmptyState
                          title="No stage timings yet"
                          description="Trigger a non-production benchmark above to capture stage-by-stage comparisons."
                        />
                      )}
                    </SectionCard>

                    <SectionCard
                      title="Human Intervention vs Unattended Execution by Workflow"
                      subtitle="Active developer time vs unattended agent/API execution per workflow"
                    >
                      {Object.keys(benchSummary?.by_workflow_type ?? {}).length === 0 ? (
                        <EmptyState
                          title="No workflow benchmarks yet"
                          description="Run any workflow (A–E) in development or staging to populate workflow comparisons."
                        />
                      ) : (
                        <div className="overflow-x-auto">
                          <table className="w-full text-left text-xs">
                            <thead className="border-b border-slate-800 text-slate-400">
                              <tr>
                                <th className="py-2 pr-3">Workflow</th>
                                <th className="py-2 pr-3">Runs (OK/Fail)</th>
                                <th className="py-2 pr-3">Active Dev</th>
                                <th className="py-2 pr-3">Unattended</th>
                                <th className="py-2 pr-3">Coverage</th>
                                <th className="py-2">p95</th>
                              </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-800/70">
                              {Object.entries(benchSummary?.by_workflow_type ?? {}).map(
                                ([wf, grp]) => (
                                  <tr key={wf}>
                                    <td className="py-2 pr-3 font-medium text-slate-100">
                                      {WORKFLOW_LABELS[wf as BenchmarkWorkflowType] ?? wf}
                                    </td>
                                    <td className="py-2 pr-3 font-mono tabular-nums">
                                      <span className="text-emerald-300">
                                        {grp.successful_runs}
                                      </span>{" "}
                                      /{" "}
                                      <span className="text-rose-300">{grp.failed_runs}</span>
                                    </td>
                                    <td className="py-2 pr-3 font-mono tabular-nums text-amber-200">
                                      {formatDurationSeconds(grp.mean_active_developer_seconds)}
                                    </td>
                                    <td className="py-2 pr-3 font-mono tabular-nums text-sky-300">
                                      {formatDurationSeconds(
                                        grp.mean_unattended_execution_seconds
                                      )}
                                    </td>
                                    <td className="py-2 pr-3 font-mono tabular-nums">
                                      {grp.automation_coverage_pct.toFixed(1)}%
                                    </td>
                                    <td className="py-2 font-mono tabular-nums text-slate-300">
                                      {formatDurationSeconds(grp.duration_stats.p95)}
                                    </td>
                                  </tr>
                                )
                              )}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </SectionCard>
                  </div>

                  {/* Stage-by-Stage Comparison Table */}
                  <SectionCard
                    title="Stage-by-Stage Comparison Table"
                    subtitle="Monotonic stage durations (mean, median, p95), documented manual baselines, and API/agent/retry counts"
                  >
                    {(benchSummary?.stage_comparison ?? []).length === 0 ? (
                      <EmptyState
                        title="No stage comparison data"
                        description="Run a benchmark to inspect per-stage latency and savings."
                      />
                    ) : (
                      <div className="overflow-x-auto">
                        <table className="w-full text-left text-xs">
                          <thead className="border-b border-slate-800 text-slate-400">
                            <tr>
                              <th className="py-2 pr-3">Stage</th>
                              <th className="py-2 pr-3">Samples</th>
                              <th className="py-2 pr-3">Manual (ESTIMATED)</th>
                              <th className="py-2 pr-3">Auto Mean</th>
                              <th className="py-2 pr-3">Median / p95</th>
                              <th className="py-2 pr-3">Time Saved</th>
                              <th className="py-2 pr-3">API / Agent</th>
                              <th className="py-2">Retries / Fails</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-800/70">
                            {(benchSummary?.stage_comparison ?? []).map((st) => (
                              <tr key={st.stage_key}>
                                <td className="py-2 pr-3 font-medium text-slate-100">
                                  {st.stage_name}
                                  <div className="font-mono text-[10px] text-slate-400">
                                    {st.stage_key}
                                  </div>
                                </td>
                                <td className="py-2 pr-3 font-mono tabular-nums">
                                  {st.sample_count}
                                </td>
                                <td className="py-2 pr-3 font-mono tabular-nums text-sky-300">
                                  {formatDurationSeconds(st.manual_baseline_seconds)}
                                </td>
                                <td className="py-2 pr-3 font-mono tabular-nums text-emerald-300">
                                  {formatDurationSeconds(st.mean_duration_seconds)}
                                </td>
                                <td className="py-2 pr-3 font-mono tabular-nums text-slate-300">
                                  {formatDurationSeconds(st.median_duration_seconds)} /{" "}
                                  {formatDurationSeconds(st.p95_duration_seconds)}
                                </td>
                                <td className="py-2 pr-3 font-mono tabular-nums text-emerald-300">
                                  {formatDurationSeconds(st.time_saved_seconds)}
                                </td>
                                <td className="py-2 pr-3 font-mono tabular-nums text-slate-300">
                                  {st.api_calls} / {st.agent_calls}
                                </td>
                                <td className="py-2 font-mono tabular-nums text-slate-300">
                                  {st.retries} / {st.failures}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </SectionCard>

                  {/* Historical Benchmark Runs */}
                  <SectionCard
                    title="Historical Benchmark Runs"
                    subtitle={`Persisted UTC benchmark runs (${benchRuns.length} shown, displayed in ${timezone})`}
                  >
                    {benchRuns.length === 0 ? (
                      <EmptyState
                        title="No historical benchmark runs"
                        description="Click 'Run Benchmark' above or run backend/scripts/run_benchmark.py to record runs."
                      />
                    ) : (
                      <div className="overflow-x-auto">
                        <table className="w-full text-left text-xs">
                          <thead className="border-b border-slate-800 text-slate-400">
                            <tr>
                              <th className="py-2 pr-2"></th>
                              <th className="py-2 pr-3">Benchmark ID</th>
                              <th className="py-2 pr-3">Workflow</th>
                              <th className="py-2 pr-3">Env</th>
                              <th className="py-2 pr-3">Evidence</th>
                              <th className="py-2 pr-3">Status</th>
                              <th className="py-2 pr-3">Duration</th>
                              <th className="py-2 pr-3">Active / Unattended</th>
                              <th className="py-2 pr-3">Time Saved</th>
                              <th className="py-2">Started</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-800/70">
                            {benchRuns.map((run) => {
                              const isExpanded = expandedRunId === run.benchmark_id;
                              const impact = run.impact_metrics;
                              return (
                                <React.Fragment key={run.benchmark_id}>
                                  <tr
                                    className="cursor-pointer hover:bg-slate-800/40"
                                    onClick={() =>
                                      setExpandedRunId(isExpanded ? null : run.benchmark_id)
                                    }
                                  >
                                    <td className="py-2.5 pr-2 text-slate-400">
                                      {isExpanded ? (
                                        <ChevronDown className="h-3.5 w-3.5" />
                                      ) : (
                                        <ChevronRight className="h-3.5 w-3.5" />
                                      )}
                                    </td>
                                    <td className="py-2.5 pr-3 font-mono text-slate-100">
                                      {run.benchmark_id}
                                      <div className="text-[10px] text-slate-400">
                                        {run.scenario}
                                      </div>
                                    </td>
                                    <td className="py-2.5 pr-3 text-slate-200">
                                      {run.workflow_type}
                                    </td>
                                    <td className="py-2.5 pr-3">
                                      <EnvironmentBadge environment={run.environment} />
                                    </td>
                                    <td className="py-2.5 pr-3">
                                      <EvidenceBadge classification={run.evidence_classification} />
                                    </td>
                                    <td className="py-2.5 pr-3">
                                      <StatusBadge status={run.success ? "success" : "failed"} />
                                    </td>
                                    <td className="py-2.5 pr-3 font-mono tabular-nums text-slate-100">
                                      {formatDurationSeconds(run.total_duration_seconds)}
                                    </td>
                                    <td className="py-2.5 pr-3 font-mono tabular-nums text-slate-300">
                                      {formatDurationSeconds(run.active_developer_seconds)} /{" "}
                                      {formatDurationSeconds(run.unattended_execution_seconds)}
                                    </td>
                                    <td className="py-2.5 pr-3 font-mono tabular-nums">
                                      {impact?.pct_saved !== null &&
                                      impact?.pct_saved !== undefined ? (
                                        <span className="text-emerald-300">
                                          {formatDurationSeconds(impact.time_saved_seconds)} (
                                          {impact.pct_saved}%)
                                        </span>
                                      ) : (
                                        <span className="text-slate-400">Omitted (no baseline)</span>
                                      )}
                                    </td>
                                    <td className="py-2.5 font-mono text-slate-400">
                                      {formatInTimezone(run.started_at, timezone)}
                                    </td>
                                  </tr>
                                  {isExpanded && (
                                    <tr className="bg-[#0d1119]">
                                      <td colSpan={10} className="px-4 py-3">
                                        <div className="space-y-2 text-[11px]">
                                          <div className="flex flex-wrap gap-4 text-slate-400">
                                            <span>API calls: {run.api_call_count}</span>
                                            <span>Agent calls: {run.agent_call_count}</span>
                                            <span>Retries: {run.retry_count}</span>
                                            <span>Failures: {run.failure_count}</span>
                                            <span>
                                              Baseline source:{" "}
                                              {run.manual_baseline_source ?? "None attached"}
                                            </span>
                                          </div>
                                          {run.stages.length > 0 && (
                                            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 pt-1">
                                              {run.stages.map((st) => (
                                                <div
                                                  key={st.id}
                                                  className="rounded border border-slate-800 bg-[#111622] p-2.5"
                                                >
                                                  <div className="flex items-center justify-between gap-2 font-medium text-slate-200">
                                                    <span>
                                                      {st.stage_order}. {st.stage_name}
                                                    </span>
                                                    <EvidenceBadge
                                                      classification={st.evidence_classification}
                                                    />
                                                  </div>
                                                  <div className="mt-1 font-mono text-slate-400">
                                                    Auto: {formatDurationSeconds(st.duration_seconds)}{" "}
                                                    · Manual:{" "}
                                                    {formatDurationSeconds(
                                                      st.manual_baseline_seconds
                                                    )}
                                                  </div>
                                                </div>
                                              ))}
                                            </div>
                                          )}
                                        </div>
                                      </td>
                                    </tr>
                                  )}
                                </React.Fragment>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </SectionCard>

                  {/* Documented Manual Baselines & Rationales */}
                  {(benchSummary?.manual_baselines ?? []).length > 0 && (
                    <SectionCard
                      title="Documented Manual Engineering Baselines"
                      subtitle="Transparent per-workflow assumptions and rationales (all classified ESTIMATED)"
                    >
                      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                        {(benchSummary?.manual_baselines ?? []).map((mb) => (
                          <div
                            key={mb.workflow_type}
                            className="rounded-lg border border-slate-800 bg-[#0d1119] p-3.5 text-xs space-y-1.5"
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span className="font-semibold text-slate-100">{mb.label}</span>
                              <EvidenceBadge classification={mb.evidence_classification} />
                            </div>
                            <div className="font-mono text-sky-300">
                              Baseline: {formatDurationSeconds(mb.manual_baseline_seconds)}
                              {mb.manual_range_seconds
                                ? ` (range ${formatDurationSeconds(mb.manual_range_seconds[0])}–${formatDurationSeconds(mb.manual_range_seconds[1])})`
                                : ""}
                            </div>
                            <p className="text-slate-400 leading-relaxed">{mb.rationale}</p>
                            <div className="text-[10px] text-slate-500">
                              Source: {mb.manual_baseline_source}
                            </div>
                          </div>
                        ))}
                      </div>
                    </SectionCard>
                  )}
                </>
              )}
            </div>
          )}

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
