"use client";

import React, { useMemo, useState, useSyncExternalStore } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { DashboardCharts, ExperimentPerformancePoint } from "@/types/releaseguard";
import { EmptyState, SectionCard } from "@/components/ui/StatePrimitives";
import { RecommendationBadge, StatusBadge } from "@/components/StatusBadges";
import { BarChart3, FlaskConical, HeartPulse, Bot, Activity, Flag } from "lucide-react";

const emptySubscribe = () => () => {};
function useIsHydrated(): boolean {
  return useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false
  );
}

const FLAG_COLORS = ["#64748b", "#34d399", "#38bdf8", "#f59e0b"];
const HEALTH_COLORS = ["#34d399", "#f59e0b", "#f43f5e", "#e11d48"];

function ChartFrame({
  height = 240,
  children,
}: {
  height?: number;
  children: React.ReactElement;
}) {
  const hydrated = useIsHydrated();
  if (!hydrated) {
    return (
      <div
        className="w-full animate-pulse rounded-lg border border-slate-800/60 bg-slate-900/40"
        style={{ height }}
      />
    );
  }
  return (
    <div className="w-full" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        {children}
      </ResponsiveContainer>
    </div>
  );
}

export function DashboardChartGrid({ charts }: { charts: DashboardCharts }) {
  const [selectedExpId, setSelectedExpId] = useState<string>("");

  const activeExperiment: ExperimentPerformancePoint | null = useMemo(() => {
    if (charts.experiment_performance.length === 0) return null;
    if (selectedExpId) {
      const match = charts.experiment_performance.find(
        (e) => e.experiment_id === selectedExpId
      );
      if (match) return match;
    }
    return charts.experiment_performance[0];
  }, [charts.experiment_performance, selectedExpId]);

  const expBarData = useMemo(() => {
    if (!activeExperiment) return [];
    return [
      {
        arm: "Control",
        conversionPct: Number((activeExperiment.control_rate * 100).toFixed(2)),
        errorPct: Number((activeExperiment.control_error_rate * 100).toFixed(2)),
        exposures: activeExperiment.control_exposures,
        conversions: activeExperiment.control_conversions,
      },
      {
        arm: "Treatment",
        conversionPct: Number((activeExperiment.treatment_rate * 100).toFixed(2)),
        errorPct: Number((activeExperiment.treatment_error_rate * 100).toFixed(2)),
        exposures: activeExperiment.treatment_exposures,
        conversions: activeExperiment.treatment_conversions,
      },
    ];
  }, [activeExperiment]);

  const hasReleaseActivity = charts.release_activity.some(
    (p) => p.releases_created > 0 || p.releases_completed > 0 || p.releases_failed > 0 || p.rollbacks > 0
  );
  const hasAgentActivity = charts.agent_activity.some(
    (p) => p.successful > 0 || p.failed > 0 || p.running > 0 || p.awaiting_approval > 0
  );
  const hasTelemetryActivity = (charts.telemetry_totals?.total_events ?? 0) > 0;
  const hasFlagDist = charts.flag_distribution.some((d) => d.count > 0);
  const hasReleaseHealth = charts.release_health.some((d) => d.count > 0);

  return (
    <div className="space-y-6">
      {/* Row 2: Chart A (Release Activity) + Chart B (Experiment Performance) */}
      <div className="grid gap-6 xl:grid-cols-12">
        <SectionCard
          className="xl:col-span-6"
          title="Chart A — Release Activity Over Time"
          subtitle="Flagsmith verified rollouts, failures, and rollbacks inside selected window (Source: rollout_history)"
        >
          {!hasReleaseActivity ? (
            <EmptyState
              icon={<BarChart3 className="h-5 w-5" />}
              title="No rollout transitions in selected window"
              description="Step or toggle a feature flag across development, staging, or production to plot verified release activity."
            />
          ) : (
            <ChartFrame>
              <BarChart data={charts.release_activity} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="label" stroke="#94a3b8" tick={{ fontSize: 11 }} />
                <YAxis stroke="#94a3b8" allowDecimals={false} tick={{ fontSize: 11 }} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "#0f172a",
                    borderColor: "#334155",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="releases_created" name="Created" fill="#6366f1" radius={[3, 3, 0, 0]} />
                <Bar dataKey="releases_completed" name="Verified OK" fill="#34d399" radius={[3, 3, 0, 0]} />
                <Bar dataKey="releases_failed" name="Failed" fill="#f59e0b" radius={[3, 3, 0, 0]} />
                <Bar dataKey="rollbacks" name="Rollbacks" fill="#f43f5e" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ChartFrame>
          )}
        </SectionCard>

        <SectionCard
          className="xl:col-span-6"
          title="Chart B — Experiment Conversion Performance"
          subtitle="Bayesian Beta-Binomial conversion & payment error comparison (Source: calculated)"
          actions={
            charts.experiment_performance.length > 0 ? (
              <select
                aria-label="Select experiment for chart comparison"
                value={activeExperiment?.experiment_id ?? ""}
                onChange={(e) => setSelectedExpId(e.target.value)}
                className="max-w-[220px] rounded-md border border-slate-700 bg-slate-900 px-2.5 py-1 text-xs text-slate-100"
              >
                {charts.experiment_performance.map((exp) => (
                  <option key={exp.experiment_id} value={exp.experiment_id}>
                    {exp.name} ({exp.environment_name})
                  </option>
                ))}
              </select>
            ) : null
          }
        >
          {!activeExperiment ? (
            <EmptyState
              icon={<FlaskConical className="h-5 w-5" />}
              title="No experiments recorded"
              description="Plan and activate an experiment to inspect Control vs Treatment Bayesian posterior lift."
            />
          ) : (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2 text-xs">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge status={activeExperiment.status} />
                  <RecommendationBadge recommendation={activeExperiment.recommendation} />
                  {activeExperiment.simulator_event_count > 0 && (
                    <span className="rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-200">
                      Includes {activeExperiment.simulator_event_count} simulator events
                    </span>
                  )}
                </div>
                <div className="font-mono text-[11px] text-slate-300 tabular-nums">
                  Abs Lift: {(activeExperiment.absolute_improvement * 100).toFixed(2)}% | P(T&gt;C):{" "}
                  {(activeExperiment.prob_treatment_beats_control * 100).toFixed(1)}%
                </div>
              </div>

              <ChartFrame height={195}>
                <BarChart data={expBarData} margin={{ top: 6, right: 12, left: -10, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis dataKey="arm" stroke="#94a3b8" tick={{ fontSize: 11 }} />
                  <YAxis stroke="#94a3b8" tickFormatter={(v) => `${v}%`} tick={{ fontSize: 11 }} />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "#0f172a",
                      borderColor: "#334155",
                      borderRadius: 8,
                      fontSize: 12,
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Bar dataKey="conversionPct" name="Conversion Rate (%)" fill="#38bdf8" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="errorPct" name="Payment Error Rate (%)" fill="#f43f5e" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ChartFrame>

              <div className="grid grid-cols-2 gap-2 text-[11px] text-slate-400">
                <div className="rounded border border-slate-800/80 bg-[#0d1119] px-2.5 py-1.5 font-mono tabular-nums">
                  Control: {activeExperiment.control_conversions}/{activeExperiment.control_exposures}{" "}
                  {activeExperiment.control_ci_95.length === 2
                    ? `(95% CrI ${(activeExperiment.control_ci_95[0] * 100).toFixed(1)}–${(activeExperiment.control_ci_95[1] * 100).toFixed(1)}%)`
                    : ""}
                </div>
                <div className="rounded border border-slate-800/80 bg-[#0d1119] px-2.5 py-1.5 font-mono tabular-nums">
                  Treatment: {activeExperiment.treatment_conversions}/{activeExperiment.treatment_exposures}{" "}
                  {activeExperiment.treatment_ci_95.length === 2
                    ? `(95% CrI ${(activeExperiment.treatment_ci_95[0] * 100).toFixed(1)}–${(activeExperiment.treatment_ci_95[1] * 100).toFixed(1)}%)`
                    : ""}
                </div>
              </div>
            </div>
          )}
        </SectionCard>
      </div>

      {/* Row 3: Chart C (Flag Distribution) + Chart D (Agent Execution Activity) */}
      <div className="grid gap-6 xl:grid-cols-12">
        <SectionCard
          className="xl:col-span-5"
          title="Chart C — Feature Flag Rollout Distribution"
          subtitle="Live Flagsmith environment states & health posture (Source: Flagsmith API)"
        >
          {!hasFlagDist ? (
            <EmptyState
              icon={<Flag className="h-5 w-5" />}
              title="No feature flags synced"
              description="Sync flags from Flagsmith in Feature Flags to inspect distribution across environments."
            />
          ) : (
            <div className="grid gap-3 sm:grid-cols-5 items-center">
              <div className="sm:col-span-3">
                <ChartFrame height={220}>
                  <PieChart>
                    <Pie
                      data={charts.flag_distribution}
                      dataKey="count"
                      nameKey="name"
                      cx="50%"
                      cy="50%"
                      innerRadius={48}
                      outerRadius={78}
                      paddingAngle={2}
                    >
                      {charts.flag_distribution.map((entry, idx) => (
                        <Cell key={entry.key} fill={FLAG_COLORS[idx % FLAG_COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{
                        backgroundColor: "#0f172a",
                        borderColor: "#334155",
                        borderRadius: 8,
                        fontSize: 12,
                      }}
                    />
                  </PieChart>
                </ChartFrame>
              </div>
              <div className="sm:col-span-2 space-y-2 text-xs">
                {charts.flag_distribution.map((slice, idx) => (
                  <div
                    key={slice.key}
                    className="flex items-center justify-between rounded border border-slate-800/80 bg-[#0d1119] px-2.5 py-1.5"
                  >
                    <span className="flex items-center gap-2 text-slate-300">
                      <span
                        className="h-2.5 w-2.5 rounded-full"
                        style={{ backgroundColor: FLAG_COLORS[idx % FLAG_COLORS.length] }}
                      />
                      {slice.name}
                    </span>
                    <span className="font-mono font-semibold text-slate-100 tabular-nums">
                      {slice.count}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </SectionCard>

        <SectionCard
          className="xl:col-span-7"
          title="Chart D — Agent Execution Activity & Latency"
          subtitle="LangGraph task completions, failures, active runs, and avg duration (Source: agent_tasks)"
        >
          {!hasAgentActivity ? (
            <EmptyState
              icon={<Bot className="h-5 w-5" />}
              title="No AI agent runs in selected window"
              description="Run a task in AI Agent or trigger PR analysis to view Gemini execution history."
            />
          ) : (
            <ChartFrame height={220}>
              <ComposedChart data={charts.agent_activity} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="label" stroke="#94a3b8" tick={{ fontSize: 11 }} />
                <YAxis yAxisId="left" stroke="#94a3b8" allowDecimals={false} tick={{ fontSize: 11 }} />
                <YAxis
                  yAxisId="right"
                  orientation="right"
                  stroke="#38bdf8"
                  tickFormatter={(v) => `${v}s`}
                  tick={{ fontSize: 11 }}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "#0f172a",
                    borderColor: "#334155",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar yAxisId="left" dataKey="successful" name="Successful" fill="#34d399" radius={[3, 3, 0, 0]} />
                <Bar yAxisId="left" dataKey="failed" name="Failed" fill="#f43f5e" radius={[3, 3, 0, 0]} />
                <Bar yAxisId="left" dataKey="running" name="Running" fill="#6366f1" radius={[3, 3, 0, 0]} />
                <Line
                  yAxisId="right"
                  type="monotone"
                  dataKey="avg_duration_seconds"
                  name="Avg Duration (s)"
                  stroke="#38bdf8"
                  strokeWidth={2}
                  connectNulls
                />
              </ComposedChart>
            </ChartFrame>
          )}
        </SectionCard>
      </div>

      {/* Row 4: Chart E (Telemetry Activity) + Chart F (Release Health) */}
      <div className="grid gap-6 xl:grid-cols-12">
        <SectionCard
          className="xl:col-span-7"
          title="Chart E — Event & Telemetry Ingestion Activity"
          subtitle="Checkout starts, completions, payment failures & abandonments (Source: experiment_events)"
          actions={
            <div className="flex flex-wrap gap-1.5 text-[10px]">
              <span className="rounded border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-emerald-300">
                App Telemetry: {charts.telemetry_totals?.application_telemetry_events ?? 0}
              </span>
              <span className="rounded border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-amber-200">
                Simulator Events: {charts.telemetry_totals?.simulator_events ?? 0}
              </span>
            </div>
          }
        >
          {!hasTelemetryActivity ? (
            <EmptyState
              icon={<Activity className="h-5 w-5" />}
              title="No telemetry ingested in selected window"
              description="Use NovaShop storefront or run simulator cohorts to stream checkout events to ReleaseGuard AI."
            />
          ) : (
            <ChartFrame height={230}>
              <AreaChart data={charts.telemetry_activity} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="label" stroke="#94a3b8" tick={{ fontSize: 11 }} />
                <YAxis stroke="#94a3b8" allowDecimals={false} tick={{ fontSize: 11 }} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "#0f172a",
                    borderColor: "#334155",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Area type="monotone" dataKey="checkout_started" name="Started" stroke="#6366f1" fill="#6366f1" fillOpacity={0.18} />
                <Area type="monotone" dataKey="checkout_completed" name="Completed" stroke="#34d399" fill="#34d399" fillOpacity={0.22} />
                <Area type="monotone" dataKey="payment_failed" name="Payment Failed" stroke="#f43f5e" fill="#f43f5e" fillOpacity={0.25} />
                <Area type="monotone" dataKey="checkout_abandoned" name="Abandoned" stroke="#f59e0b" fill="#f59e0b" fillOpacity={0.14} />
              </AreaChart>
            </ChartFrame>
          )}
        </SectionCard>

        <SectionCard
          className="xl:col-span-5"
          title="Chart F — Release Health & Guardrail Posture"
          subtitle="Healthy releases vs attention, guardrail breaches & rollback advice"
        >
          {!hasReleaseHealth ? (
            <EmptyState
              icon={<HeartPulse className="h-5 w-5" />}
              title="No evaluated release health signals yet"
              description="Run experiment recommendations or release verification steps to populate release health status."
            />
          ) : (
            <ChartFrame height={230}>
              <BarChart
                data={charts.release_health}
                layout="vertical"
                margin={{ top: 8, right: 20, left: 20, bottom: 0 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis type="number" stroke="#94a3b8" allowDecimals={false} tick={{ fontSize: 11 }} />
                <YAxis type="category" dataKey="name" width={140} stroke="#cbd5e1" tick={{ fontSize: 11 }} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "#0f172a",
                    borderColor: "#334155",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                />
                <Bar dataKey="count" name="Count" radius={[0, 4, 4, 0]}>
                  {charts.release_health.map((entry, idx) => (
                    <Cell key={entry.key} fill={HEALTH_COLORS[idx % HEALTH_COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ChartFrame>
          )}
        </SectionCard>
      </div>
    </div>
  );
}
