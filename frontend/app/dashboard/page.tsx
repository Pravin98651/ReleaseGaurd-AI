"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  Bot,
  CheckCircle2,
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
  getDashboardSummary,
  getErrorMessage,
  getFlagHealth,
  getSystemHealth,
  listApprovals,
  listExperiments,
  listRolloutHistory,
} from "@/lib/api";
import type {
  Approval,
  DashboardSummary,
  Experiment,
  RolloutHistoryEntry,
  SystemHealth,
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
import { ApprovalActionCard } from "@/components/ApprovalActionCard";
import {
  EnvironmentBadge,
  RecommendationBadge,
  StatusBadge,
} from "@/components/StatusBadges";
import { PolicyGateBadge } from "@/components/PolicyGateBadge";

export default function DashboardPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [pendingApprovals, setPendingApprovals] = useState<Approval[]>([]);
  const [rollouts, setRollouts] = useState<RolloutHistoryEntry[]>([]);
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [avgHealthScore, setAvgHealthScore] = useState<number | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [h, dash, appRes, rollRes, healthRes, expRes] = await Promise.all([
        getSystemHealth(),
        getDashboardSummary(),
        listApprovals({ status: "pending", limit: 5 }).catch(() => ({
          items: [],
          total: 0,
          limit: 5,
          offset: 0,
        })),
        listRolloutHistory({ limit: 10 }).catch(() => ({
          items: [],
          total: 0,
          limit: 10,
          offset: 0,
        })),
        getFlagHealth().catch(() => null),
        listExperiments().catch(() => ({ items: [], total: 0, limit: 20, offset: 0 })),
      ]);

      setHealth(h);
      setSummary(dash);
      setPendingApprovals(appRes.items);
      setRollouts(
        rollRes.items.length > 0 ? rollRes.items : (dash.recent_rollouts ?? [])
      );
      setExperiments(expRes.items);
      if (typeof dash.average_health_score === "number") {
        setAvgHealthScore(dash.average_health_score);
      } else if (healthRes && typeof healthRes.average_health_score === "number") {
        setAvgHealthScore(healthRes.average_health_score);
      } else if (healthRes && healthRes.items.length > 0) {
        const avg = Math.round(
          healthRes.items.reduce((acc, f) => acc + f.health_score, 0) /
            healthRes.items.length
        );
        setAvgHealthScore(avg);
      } else {
        setAvgHealthScore(null);
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      void loadData();
    }, 0);
    return () => clearTimeout(timer);
  }, [loadData]);

  const counts = summary?.counts;
  const recentAudits = summary?.recent_audits ?? [];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Release Control Plane"
        description="Live overview of PR risk posture, Flagsmith environments, Bayesian experiments, and Policy Gate approvals."
        actions={
          <>
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
          </>
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
            <StatusBadge
              status={health.flagsmith_connected ? "connected" : "disconnected"}
            />
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

      {/* Primary KPI Grid */}
      {loading && !summary ? (
        <LoadingSkeleton rows={2} />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
          <MetricCard
            label="Repositories"
            value={counts?.repositories ?? 0}
            subtext="Synced GitHub targets"
            icon={<FolderGit2 className="h-4 w-4" />}
            href="/repositories"
          />
          <MetricCard
            label="Open / Risky PRs"
            value={`${counts?.open_prs ?? 0} / ${counts?.high_risk_prs ?? 0}`}
            subtext="High & critical risk PRs"
            icon={<GitPullRequest className="h-4 w-4" />}
            tone={(counts?.high_risk_prs ?? 0) > 0 ? "amber" : "default"}
            href="/pull-requests"
          />
          <MetricCard
            label="Synced Flags"
            value={counts?.active_flags ?? 0}
            subtext="Flagsmith projected flags"
            icon={<Flag className="h-4 w-4" />}
            tone="sky"
            href="/feature-flags"
          />
          <MetricCard
            label="Active Experiments"
            value={counts?.active_experiments ?? 0}
            subtext="Running Bayesian evals"
            icon={<FlaskConical className="h-4 w-4" />}
            tone="indigo"
            href="/experiments"
          />
          <MetricCard
            label="Pending Approvals"
            value={counts?.pending_approvals ?? pendingApprovals.length}
            subtext="Category B/C gated actions"
            icon={<ShieldAlert className="h-4 w-4" />}
            tone={(counts?.pending_approvals ?? 0) > 0 ? "amber" : "emerald"}
            href="/approvals"
          />
          <MetricCard
            label="Flag Health"
            value={avgHealthScore !== null ? `${avgHealthScore}/100` : "—"}
            subtext={`${counts?.unhealthy_flags ?? 0} flags with deductions`}
            icon={<HeartPulse className="h-4 w-4" />}
            tone={
              avgHealthScore === null
                ? "default"
                : avgHealthScore >= 80
                  ? "emerald"
                  : avgHealthScore >= 55
                    ? "amber"
                    : "rose"
            }
            href="/flag-health"
          />
        </div>
      )}

      {/* Pending Approvals + Active Experiments */}
      <div className="grid gap-6 xl:grid-cols-12">
        <SectionCard
          className="xl:col-span-7"
          title="Pending Human Approvals"
          subtitle="Single-use, SHA-256 hash-bound Category B/C actions awaiting authorization"
          actions={
            <Link
              href="/approvals"
              className="text-xs font-medium text-indigo-300 hover:text-indigo-200"
            >
              View Approval Center →
            </Link>
          }
        >
          {loading && pendingApprovals.length === 0 ? (
            <LoadingSkeleton rows={2} />
          ) : pendingApprovals.length === 0 ? (
            <EmptyState
              icon={<CheckCircle2 className="h-5 w-5 text-emerald-400" />}
              title="No pending approvals"
              description="All Category B and Category C policy gates are clear. Production rollouts or deletions will queue here."
            />
          ) : (
            <div className="space-y-3">
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
        </SectionCard>

        <SectionCard
          className="xl:col-span-5"
          title="Active & Recent Experiments"
          subtitle="Bayesian Beta-Binomial conversion & guardrail tracking"
          actions={
            <Link
              href="/experiments"
              className="text-xs font-medium text-indigo-300 hover:text-indigo-200"
            >
              Experiments →
            </Link>
          }
        >
          {experiments.length === 0 ? (
            <EmptyState
              icon={<FlaskConical className="h-5 w-5" />}
              title="No experiments yet"
              description="Plan a natural-language experiment or send demo checkout traffic to compute Bayesian posteriors."
              action={
                <Link
                  href="/experiments"
                  className="inline-flex items-center rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400"
                >
                  Plan Experiment
                </Link>
              }
            />
          ) : (
            <div className="space-y-3">
              {experiments.slice(0, 5).map((exp) => (
                <Link
                  key={exp.id}
                  href={`/experiments?id=${encodeURIComponent(exp.id)}`}
                  className="block rounded-lg border border-slate-800 bg-[#0d1119] p-3 hover:border-slate-700"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium text-slate-100">
                        {exp.name}
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                        <EnvironmentBadge environment={exp.environment_name} />
                        <span className="font-mono text-cyan-300">{exp.analytics_mode}</span>
                      </div>
                    </div>
                    <StatusBadge status={exp.status} />
                  </div>
                  <div className="mt-2.5 flex items-center justify-between gap-2">
                    <RecommendationBadge recommendation={exp.latest_recommendation} />
                    {exp.latest_bayesian_result && (
                      <span className="font-mono text-[11px] text-slate-300 tabular-nums">
                        P(T&gt;C):{" "}
                        {(
                          exp.latest_bayesian_result.comparison.prob_treatment_beats_control *
                          100
                        ).toFixed(1)}
                        %
                      </span>
                    )}
                  </div>
                </Link>
              ))}
            </div>
          )}
        </SectionCard>
      </div>

      {/* Recent Rollouts & Audit Trail */}
      <div className="grid gap-6 lg:grid-cols-2">
        <SectionCard
          title="Recent Rollouts & Flag Mutations"
          subtitle="Immutable rollout_history with post-mutation live Flagsmith verification"
          actions={
            <Link
              href="/releases"
              className="text-xs font-medium text-indigo-300 hover:text-indigo-200"
            >
              Releases →
            </Link>
          }
        >
          {rollouts.length === 0 ? (
            <EmptyState
              icon={<Rocket className="h-5 w-5" />}
              title="No rollout history recorded"
              description="Toggle or step a feature flag across development, staging, or production to log verified rollouts."
            />
          ) : (
            <div className="divide-y divide-slate-800/80">
              {rollouts.slice(0, 8).map((item) => (
                <div
                  key={item.id}
                  className="flex items-center justify-between gap-3 py-2.5 text-xs"
                >
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono font-medium text-slate-100">
                        {item.flag_key || item.feature_flag_id.slice(0, 8)}
                      </span>
                      <EnvironmentBadge environment={item.environment_name} />
                      <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                        {item.action}
                      </span>
                    </div>
                    {item.reason && (
                      <p className="truncate text-[11px] text-slate-400">{item.reason}</p>
                    )}
                  </div>
                  <div className="flex flex-col items-end gap-1 shrink-0">
                    <StatusBadge
                      status={item.verified ? "verified" : "failed"}
                      label={item.verified ? "Verified" : "Unverified"}
                    />
                    <span className="font-mono text-[10px] text-slate-500 tabular-nums">
                      {formatTimestamp(item.created_at)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </SectionCard>

        <SectionCard
          title="Recent Governance Audit Logs"
          subtitle="Append-only trail of Category A/B/C/DENY policy decisions"
          actions={
            <Link
              href="/audit-logs"
              className="text-xs font-medium text-indigo-300 hover:text-indigo-200"
            >
              Full Audit Trail →
            </Link>
          }
        >
          {recentAudits.length === 0 ? (
            <EmptyState
              icon={<Activity className="h-5 w-5" />}
              title="No audit events yet"
              description="Every AI tool proposal, policy check, and mutation is immutably logged here."
            />
          ) : (
            <div className="divide-y divide-slate-800/80">
              {recentAudits.slice(0, 8).map((log) => (
                <div
                  key={log.id}
                  className="flex items-center justify-between gap-3 py-2.5 text-xs"
                >
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <PolicyGateBadge category={log.policy_category} compact />
                      <span className="font-mono font-medium text-slate-200">
                        {log.event_action}
                      </span>
                    </div>
                    <div className="text-[11px] text-slate-400">
                      {log.actor_type} · {log.resource_type}
                      {log.environment_name ? ` · ${log.environment_name}` : ""}
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-1 shrink-0">
                    <StatusBadge status={log.status} />
                    <span className="font-mono text-[10px] text-slate-500 tabular-nums">
                      {formatTimestamp(log.created_at)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      </div>

      {/* Warning notice if neither GitHub nor Flagsmith connected */}
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
