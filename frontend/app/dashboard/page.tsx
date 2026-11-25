"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Bot,
  CalendarClock,
  CheckCircle2,
  CheckSquare,
  Flag,
  FlaskConical,
  FolderGit2,
  GitPullRequest,
  HeartPulse,
  RefreshCw,
  Rocket,
  ShieldAlert,
} from "lucide-react";
import {
  getDashboardOverview,
  getErrorMessage,
  getSystemHealth,
  listApprovals,
} from "@/lib/api";
import type {
  Approval,
  DashboardOverview,
  MetricTrend,
  SystemHealth,
  TimeRangePreset,
} from "@/types/releaseguard";
import { formatTimestamp } from "@/lib/utils";
import {
  EmptyState,
  ErrorBanner,
  LoadingSkeleton,
  MetricCard,
  PageHeader,
  SectionCard,
} from "@/components/ui/StatePrimitives";
import { DashboardChartGrid } from "@/components/DashboardCharts";
import { ApprovalActionCard } from "@/components/ApprovalActionCard";
import { EnvironmentBadge, StatusBadge } from "@/components/StatusBadges";

function TrendPill({ trend }: { trend?: MetricTrend | null }) {
  if (!trend || trend.percent_change === null || trend.percent_change === undefined) {
    return null;
  }
  const up = trend.direction === "up";
  const down = trend.direction === "down";
  return (
    <span
      className={`inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums ${
        up
          ? "bg-emerald-500/15 text-emerald-300"
          : down
            ? "bg-rose-500/15 text-rose-300"
            : "bg-slate-800 text-slate-400"
      }`}
    >
      {up ? (
        <ArrowUpRight className="h-3 w-3" />
      ) : down ? (
        <ArrowDownRight className="h-3 w-3" />
      ) : null}
      {trend.percent_change > 0 ? `+${trend.percent_change}%` : `${trend.percent_change}%`}
    </span>
  );
}

function DomainSummaryCard({
  title,
  href,
  icon,
  sourceLabel,
  definition,
  items,
  trend,
}: {
  title: string;
  href: string;
  icon: React.ReactNode;
  sourceLabel: string;
  definition: string;
  items: Array<{ label: string; value: string | number; highlight?: "amber" | "rose" | "emerald" }>;
  trend?: MetricTrend | null;
}) {
  return (
    <Link
      href={href}
      className="group flex flex-col justify-between rounded-xl border border-slate-800/90 bg-[#111622] p-4 transition-colors hover:border-slate-700 hover:bg-[#151c2b]"
    >
      <div>
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
            <span className="text-indigo-400">{icon}</span>
            {title}
          </span>
          <div className="flex items-center gap-1.5">
            <TrendPill trend={trend} />
            <span className="rounded border border-slate-800 bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
              {sourceLabel}
            </span>
          </div>
        </div>
        <p className="mt-1 text-[11px] text-slate-500">{definition}</p>

        <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 border-t border-slate-800/70 pt-3 sm:grid-cols-3">
          {items.map((item) => {
            const valClass =
              item.highlight === "rose"
                ? "text-rose-300"
                : item.highlight === "amber"
                  ? "text-amber-300"
                  : item.highlight === "emerald"
                    ? "text-emerald-300"
                    : "text-slate-100";
            return (
              <div key={item.label} className="min-w-0">
                <div className="truncate text-[10px] text-slate-400">{item.label}</div>
                <div className={`text-sm font-semibold tabular-nums ${valClass}`}>
                  {item.value}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="mt-3 flex items-center justify-end gap-1 text-[11px] font-medium text-indigo-300 group-hover:text-indigo-200">
        <span>Open module</span>
        <ArrowRight className="h-3 w-3" />
      </div>
    </Link>
  );
}

export default function DashboardPage() {
  const [range, setRange] = useState<TimeRangePreset>("7d");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [environment, setEnvironment] = useState("all");

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [pendingApprovals, setPendingApprovals] = useState<Approval[]>([]);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const filters = {
        range,
        start_date: range === "custom" && startDate ? startDate : undefined,
        end_date: range === "custom" && endDate ? endDate : undefined,
        environment: environment !== "all" ? environment : undefined,
      };
      const [h, ov, appRes] = await Promise.all([
        getSystemHealth(),
        getDashboardOverview(filters),
        listApprovals({ status: "pending", limit: 5 }).catch(() => ({
          items: [],
          total: 0,
          limit: 5,
          offset: 0,
        })),
      ]);
      setHealth(h);
      setOverview(ov);
      setPendingApprovals(appRes.items);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [range, startDate, endDate, environment]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void loadData();
    }, 0);
    let debounceTimer: ReturnType<typeof setTimeout> | null = null;
    const onWorkspaceEvent = () => {
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        void loadData();
      }, 450);
    };
    window.addEventListener("rg:workspace-event", onWorkspaceEvent);
    return () => {
      clearTimeout(timer);
      if (debounceTimer) clearTimeout(debounceTimer);
      window.removeEventListener("rg:workspace-event", onWorkspaceEvent);
    };
  }, [loadData]);

  const m = overview?.metrics;
  const recentActivity = overview?.recent_activity ?? [];
  const pendingActions = overview?.pending_actions ?? [];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Release Control Plane"
        description="Real-time engineering activity, verified Flagsmith rollouts, Bayesian experiments, pending actions, and scheduled automation."
        actions={
          <div className="flex flex-wrap items-center gap-2">
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
              aria-label="Select date range"
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

            {range === "custom" && (
              <div className="flex items-center gap-1.5">
                <input
                  type="date"
                  aria-label="Custom start date"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                  className="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200"
                />
                <span className="text-xs text-slate-500">to</span>
                <input
                  type="date"
                  aria-label="Custom end date"
                  value={endDate}
                  onChange={(e) => setEndDate(e.target.value)}
                  className="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200"
                />
              </div>
            )}

            <Link
              href="/agent"
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-400"
            >
              <Bot className="h-3.5 w-3.5" />
              Open AI Engineer
            </Link>
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

      <ErrorBanner error={error} onRetry={() => void loadData()} />

      {/* Integration Status Bar */}
      {health && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="flex items-center justify-between rounded-xl border border-slate-800 bg-[#111622] px-4 py-3">
            <div>
              <div className="text-[11px] uppercase tracking-wider text-slate-400">
                GitHub Integration
              </div>
              <div className="mt-0.5 text-sm font-medium text-slate-100">
                {health.github_connected ? "Connected" : "Not Connected"}
              </div>
            </div>
            <StatusBadge status={health.github_connected ? "connected" : "disconnected"} />
          </div>

          <div className="flex items-center justify-between rounded-xl border border-slate-800 bg-[#111622] px-4 py-3">
            <div>
              <div className="text-[11px] uppercase tracking-wider text-slate-400">
                Flagsmith Control
              </div>
              <div className="mt-0.5 text-sm font-medium text-slate-100">
                {health.flagsmith_connected ? "Connected" : "Not Connected"}
              </div>
            </div>
            <StatusBadge status={health.flagsmith_connected ? "connected" : "disconnected"} />
          </div>

          <div className="flex items-center justify-between rounded-xl border border-slate-800 bg-[#111622] px-4 py-3">
            <div>
              <div className="text-[11px] uppercase tracking-wider text-slate-400">
                Gemini Orchestrator
              </div>
              <div className="mt-0.5 text-sm font-medium text-slate-100">
                {health.gemini_configured ? "Configured (AFC Off)" : "API Key Missing"}
              </div>
            </div>
            <StatusBadge status={health.gemini_configured ? "ok" : "pending"} />
          </div>

          <div className="flex items-center justify-between rounded-xl border border-slate-800 bg-[#111622] px-4 py-3">
            <div>
              <div className="text-[11px] uppercase tracking-wider text-slate-400">
                Experiment Analytics
              </div>
              <div className="mt-0.5 font-mono text-xs font-medium text-cyan-300">
                {health.analytics_mode}
              </div>
            </div>
            <Link
              href="/settings"
              className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[11px] text-slate-300 hover:text-white"
            >
              Configure
            </Link>
          </div>
        </div>
      )}

      {/* Row 1: Primary Executive KPIs */}
      {loading && !overview ? (
        <LoadingSkeleton rows={3} />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            <MetricCard
              label="Total Repositories"
              value={m?.engineering.connected_repositories ?? 0}
              subtext="Synced GitHub repositories"
              icon={<FolderGit2 className="h-4 w-4" />}
              href="/repositories"
            />
            <MetricCard
              label="PRs Analysed"
              value={`${m?.engineering.prs_analyzed ?? 0} / ${m?.engineering.synchronized_prs ?? 0}`}
              subtext={`${m?.engineering.draft_prs_created ?? 0} Draft PRs opened`}
              icon={<GitPullRequest className="h-4 w-4" />}
              href="/pull-requests"
            />
            <MetricCard
              label="Active Feature Flags"
              value={m?.feature_flags.total_flags ?? 0}
              subtext={`${m?.feature_flags.gradual_rollout_flags ?? 0} gradual rollout · Health ${m?.feature_flags.average_health_score ?? "—"}/100`}
              icon={<Flag className="h-4 w-4" />}
              tone="sky"
              href="/feature-flags"
            />
            <MetricCard
              label="Running Experiments"
              value={`${m?.experiments.running_experiments ?? 0} / ${m?.experiments.total_experiments ?? 0}`}
              subtext={`${m?.experiments.breached_guardrail_experiments ?? 0} guardrail breaches`}
              icon={<FlaskConical className="h-4 w-4" />}
              tone={(m?.experiments.breached_guardrail_experiments ?? 0) > 0 ? "rose" : "indigo"}
              href="/experiments"
            />
            <MetricCard
              label="Pending Approvals"
              value={m?.approvals.pending_approvals ?? pendingApprovals.length}
              subtext="Category B/C awaiting human sign-off"
              icon={<ShieldAlert className="h-4 w-4" />}
              tone={(m?.approvals.pending_approvals ?? 0) > 0 ? "amber" : "emerald"}
              href="/approvals"
            />
          </div>

          {/* 6 Detailed Operational Metric Cards */}
          {m && (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              <DomainSummaryCard
                title="Engineering Activity"
                href="/pull-requests"
                icon={<GitPullRequest className="h-4 w-4" />}
                sourceLabel="GitHub API"
                definition="Synced repos, PR analyses, validated Draft PRs & analysis failures"
                trend={m.engineering.trends?.synchronized_prs}
                items={[
                  { label: "Repositories", value: m.engineering.connected_repositories },
                  { label: "Synced PRs", value: m.engineering.synchronized_prs },
                  { label: "Analysed PRs", value: m.engineering.prs_analyzed, highlight: "emerald" },
                  { label: "Pending PRs", value: m.engineering.pending_pr_analyses },
                  { label: "Draft PRs", value: m.engineering.draft_prs_created },
                  {
                    label: "Failed Tasks",
                    value: m.engineering.failed_analysis_tasks,
                    highlight: m.engineering.failed_analysis_tasks > 0 ? "rose" : undefined,
                  },
                ]}
              />

              <DomainSummaryCard
                title="Release Operations"
                href="/releases"
                icon={<Rocket className="h-4 w-4" />}
                sourceLabel="rollout_history"
                definition="Verified Flagsmith rollout transitions & rollbacks (distinct from CD deploys)"
                trend={m.releases.trends?.total_releases}
                items={[
                  { label: "Total Releases", value: m.releases.total_releases },
                  { label: "In Progress", value: m.releases.releases_in_progress },
                  { label: "Verified OK", value: m.releases.successful_releases, highlight: "emerald" },
                  {
                    label: "Failed",
                    value: m.releases.failed_releases,
                    highlight: m.releases.failed_releases > 0 ? "rose" : undefined,
                  },
                  {
                    label: "Rollbacks",
                    value: m.releases.rollbacks_executed,
                    highlight: m.releases.rollbacks_executed > 0 ? "amber" : undefined,
                  },
                  {
                    label: "Success Rate",
                    value:
                      m.releases.release_success_rate !== null
                        ? `${m.releases.release_success_rate}%`
                        : "Unavailable",
                  },
                ]}
              />

              <DomainSummaryCard
                title="Feature Flags"
                href="/feature-flags"
                icon={<Flag className="h-4 w-4" />}
                sourceLabel="Flagsmith API"
                definition="Synced flags across environments with live state & 7-detector health"
                items={[
                  { label: "Total Flags", value: m.feature_flags.total_flags },
                  { label: "Enabled (100%)", value: m.feature_flags.enabled_flags, highlight: "emerald" },
                  { label: "Disabled", value: m.feature_flags.disabled_flags },
                  { label: "Gradual Split", value: m.feature_flags.gradual_rollout_flags },
                  {
                    label: "Health Issues",
                    value: m.feature_flags.flags_with_health_issues,
                    highlight: m.feature_flags.flags_with_health_issues > 0 ? "amber" : undefined,
                  },
                  { label: "Pending Changes", value: m.feature_flags.pending_flag_changes },
                ]}
              />

              <DomainSummaryCard
                title="Experimentation"
                href="/experiments"
                icon={<FlaskConical className="h-4 w-4" />}
                sourceLabel="calculated"
                definition="Bayesian Beta-Binomial conversion & payment_failed guardrail runs"
                items={[
                  { label: "Total Exps", value: m.experiments.total_experiments },
                  { label: "Running", value: m.experiments.running_experiments },
                  { label: "Completed", value: m.experiments.completed_experiments },
                  { label: "Paused", value: m.experiments.paused_experiments },
                  {
                    label: "Expand Recs",
                    value: m.experiments.positive_recommendation_experiments,
                    highlight: "emerald",
                  },
                  {
                    label: "Guardrail Breach",
                    value: m.experiments.breached_guardrail_experiments,
                    highlight: m.experiments.breached_guardrail_experiments > 0 ? "rose" : undefined,
                  },
                ]}
              />

              <DomainSummaryCard
                title="Approval Activity"
                href="/approvals"
                icon={<CheckSquare className="h-4 w-4" />}
                sourceLabel="PolicyGate"
                definition="Single-use SHA-256 hash-bound Category B & Category C governance log"
                items={[
                  {
                    label: "Pending",
                    value: m.approvals.pending_approvals,
                    highlight: m.approvals.pending_approvals > 0 ? "amber" : "emerald",
                  },
                  { label: "Approved/Exec", value: m.approvals.approved_operations, highlight: "emerald" },
                  { label: "Rejected", value: m.approvals.rejected_operations },
                  { label: "Expired", value: m.approvals.expired_approvals },
                  { label: "Awaiting Flag", value: m.releases.releases_awaiting_approval },
                ]}
              />

              <DomainSummaryCard
                title="Agent Activity"
                href="/agent"
                icon={<Bot className="h-4 w-4" />}
                sourceLabel="LangGraph"
                definition="Gemini multi-turn tool executions, duration & recorded token consumption"
                items={[
                  { label: "Total Runs", value: m.agents.total_agent_executions },
                  { label: "Succeeded", value: m.agents.successful_executions, highlight: "emerald" },
                  {
                    label: "Failed",
                    value: m.agents.failed_executions,
                    highlight: m.agents.failed_executions > 0 ? "rose" : undefined,
                  },
                  { label: "Running", value: m.agents.running_executions },
                  {
                    label: "Avg Duration",
                    value:
                      m.agents.average_execution_duration_seconds !== null
                        ? `${m.agents.average_execution_duration_seconds}s`
                        : "Unavailable",
                  },
                  { label: "Tool Calls", value: m.agents.agent_tool_calls },
                ]}
              />
            </div>
          )}

          {/* Charts A - F */}
          {overview?.charts && <DashboardChartGrid charts={overview.charts} />}

          {/* Pending Actions Panel + Recent Activity Timeline */}
          <div className="grid gap-6 xl:grid-cols-12">
            <SectionCard
              className="xl:col-span-6"
              title="Pending Actions & Operational Alerts"
              subtitle="Actionable items requiring human review or recovery (never mutates production automatically)"
              actions={
                <Link
                  href="/approvals"
                  className="text-xs font-medium text-indigo-300 hover:text-indigo-200"
                >
                  Approval Center →
                </Link>
              }
            >
              {pendingApprovals.length > 0 && (
                <div className="mb-4 space-y-3">
                  {pendingApprovals.map((app) => (
                    <ApprovalActionCard
                      key={app.id}
                      approval={app}
                      compact
                      onUpdated={() => void loadData()}
                    />
                  ))}
                </div>
              )}

              {pendingActions.length === 0 && pendingApprovals.length === 0 ? (
                <EmptyState
                  icon={<CheckCircle2 className="h-5 w-5 text-emerald-400" />}
                  title="All operational queues clear"
                  description="No pending approvals, guardrail breaches, failed operations, or integration alerts detected."
                />
              ) : (
                <div className="divide-y divide-slate-800/80">
                  {pendingActions.slice(0, 10).map((act) => {
                    const sevColor =
                      act.severity === "critical"
                        ? "border-rose-500/40 bg-rose-500/15 text-rose-200"
                        : act.severity === "high"
                          ? "border-amber-500/40 bg-amber-500/15 text-amber-200"
                          : "border-slate-700 bg-slate-800 text-slate-300";
                    return (
                      <div key={act.id} className="flex items-start justify-between gap-3 py-2.5 text-xs">
                        <div className="min-w-0 space-y-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className={`rounded border px-1.5 py-0.5 text-[10px] font-bold uppercase ${sevColor}`}>
                              {act.severity}
                            </span>
                            {act.environment_name && (
                              <EnvironmentBadge environment={act.environment_name} />
                            )}
                            <span className="font-medium text-slate-100">{act.title}</span>
                          </div>
                          <p className="text-[11px] text-slate-400">{act.description}</p>
                        </div>
                        <Link
                          href={act.href}
                          className="shrink-0 rounded-md border border-slate-700 bg-slate-900 px-2.5 py-1 text-[11px] font-medium text-indigo-300 hover:bg-slate-800"
                        >
                          {act.action_label}
                        </Link>
                      </div>
                    );
                  })}
                </div>
              )}
            </SectionCard>

            <SectionCard
              className="xl:col-span-6"
              title="Recent Operational Activity"
              subtitle="Live audit events and Flagsmith rollout history with direct resource navigation"
              actions={
                <Link
                  href="/audit-logs"
                  className="text-xs font-medium text-indigo-300 hover:text-indigo-200"
                >
                  Audit Trail →
                </Link>
              }
            >
              {recentActivity.length === 0 ? (
                <EmptyState
                  icon={<Activity className="h-5 w-5" />}
                  title="No recorded activity yet"
                  description="Synchronize repositories, run AI analyses, or toggle flags to build an immutable event trail."
                />
              ) : (
                <div className="divide-y divide-slate-800/80 max-h-[430px] overflow-y-auto pr-1">
                  {recentActivity.slice(0, 15).map((item) => (
                    <Link
                      key={item.id}
                      href={item.href}
                      className="flex items-center justify-between gap-3 py-2.5 text-xs hover:bg-slate-900/40 rounded px-1"
                    >
                      <div className="min-w-0 space-y-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium text-slate-100">{item.title}</span>
                          {item.resource_label && (
                            <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-cyan-300">
                              {item.resource_label}
                            </span>
                          )}
                          {item.environment_name && (
                            <EnvironmentBadge environment={item.environment_name} />
                          )}
                        </div>
                        <p className="truncate text-[11px] text-slate-400">
                          {item.description} · {item.actor}
                        </p>
                      </div>
                      <div className="flex flex-col items-end gap-1 shrink-0">
                        <StatusBadge status={item.status} />
                        <span className="font-mono text-[10px] text-slate-500 tabular-nums">
                          {formatTimestamp(item.timestamp)}
                        </span>
                      </div>
                    </Link>
                  ))}
                </div>
              )}
            </SectionCard>
          </div>

          {/* Row 5: Upcoming Scheduled Jobs + Data Source Transparency */}
          <div className="grid gap-6 lg:grid-cols-2">
            <SectionCard
              title="Upcoming Scheduled Automation"
              subtitle="Recurring & one-time release engineering jobs managed by PostgreSQL scheduler"
              actions={
                <Link
                  href="/schedules"
                  className="text-xs font-medium text-indigo-300 hover:text-indigo-200"
                >
                  Manage Schedules →
                </Link>
              }
            >
              {(overview?.upcoming_schedules ?? []).length === 0 ? (
                <EmptyState
                  icon={<CalendarClock className="h-5 w-5" />}
                  title="No active scheduled jobs yet"
                  description="Schedule automated repo syncs, PR analysis, flag health scans, or experiment monitoring in Schedules."
                  action={
                    <Link
                      href="/schedules"
                      className="inline-flex items-center rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400"
                    >
                      Open Schedules
                    </Link>
                  }
                />
              ) : (
                <div className="divide-y divide-slate-800/80 text-xs">
                  {(overview?.upcoming_schedules ?? []).map((sched, idx) => (
                    <Link
                      key={String(sched.id ?? idx)}
                      href="/schedules"
                      className="flex items-center justify-between gap-3 py-2.5 hover:bg-slate-900/40 rounded px-1"
                    >
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium text-slate-100">{String(sched.name ?? "Schedule")}</span>
                          {Boolean(sched.last_status) && <StatusBadge status={String(sched.last_status)} />}
                        </div>
                        <div className="mt-0.5 text-[11px] text-slate-400">
                          {String(sched.human_description ?? sched.job_type ?? "")} · {String(sched.job_type ?? "")}
                        </div>
                      </div>
                      <div className="text-right shrink-0">
                        <span className="font-mono text-[11px] text-cyan-300">
                          {sched.next_run_at ? formatTimestamp(String(sched.next_run_at)) : "Paused"}
                        </span>
                      </div>
                    </Link>
                  ))}
                </div>
              )}
            </SectionCard>

            <SectionCard
              title="Data Sources & Measurement Integrity"
              subtitle="Transparent attribution across live external APIs, persisted DB state, and simulator telemetry"
              actions={
                <Link
                  href="/metrics"
                  className="text-xs font-medium text-indigo-300 hover:text-indigo-200"
                >
                  Deep Metrics →
                </Link>
              }
            >
              <div className="space-y-2.5 text-xs text-slate-300">
                <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2">
                  <span>Interactive Storefront Telemetry</span>
                  <span className="font-mono text-emerald-300 tabular-nums">
                    {overview?.charts?.telemetry_totals?.application_telemetry_events ?? 0} events
                  </span>
                </div>
                <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2">
                  <span>NovaShop Simulator Cohorts (`sim_*`)</span>
                  <span className="font-mono text-amber-300 tabular-nums">
                    {overview?.charts?.telemetry_totals?.simulator_events ?? 0} events
                  </span>
                </div>
                <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2">
                  <span>Flagsmith Live Rollouts & Flag Health</span>
                  <span className="font-mono text-cyan-300">
                    <HeartPulse className="mr-1 inline h-3.5 w-3.5" />
                    Avg {m?.feature_flags.average_health_score ?? "—"}/100
                  </span>
                </div>
                <p className="text-[11px] text-slate-500">
                  Unavailable historical metrics (such as deployment latency without deployment webhooks or
                  prior-window deltas on new workspaces) are displayed transparently rather than fabricated.
                </p>
              </div>
            </SectionCard>
          </div>
        </>
      )}

      {health && (!health.github_connected || !health.flagsmith_connected) && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-xs text-amber-200">
          <div className="flex items-center gap-2.5">
            <AlertTriangle className="h-4 w-4 shrink-0 text-amber-400" />
            <span>
              Complete GitHub and Flagsmith setup in Settings to enable live PR analysis,
              Draft PR creation, and Feature Versioning v2 rollouts.
            </span>
          </div>
          <Link
            href="/settings"
            className="rounded-lg border border-amber-500/40 bg-amber-500/15 px-3 py-1.5 font-semibold text-amber-100 hover:bg-amber-500/25"
          >
            Go to Settings
          </Link>
        </div>
      )}
    </div>
  );
}
