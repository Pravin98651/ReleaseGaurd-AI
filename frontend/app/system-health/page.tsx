"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  Bot,
  CalendarClock,
  CheckCircle2,
  Database,
  Plug,
  RefreshCw,
  Server,
  ShoppingBag,
} from "lucide-react";

import { LoadingSkeleton, MetricCard, PageHeader, SectionCard } from "@/components/ui/StatePrimitives";
import { StatusBadge } from "@/components/StatusBadges";
import { getDeepSystemHealth, getSystemSchedulerStatus, getSystemWorkersStatus } from "@/lib/api";
import type { DeepSystemHealth } from "@/types/releaseguard";

export default function SystemHealthPage() {
  const [health, setHealth] = useState<DeepSystemHealth | null>(null);
  const [workers, setWorkers] = useState<{
    agent_tasks: Array<Record<string, unknown>>;
    job_executions: Array<Record<string, unknown>>;
  } | null>(null);
  const [sched, setSched] = useState<{
    worker: Record<string, unknown>;
    counts: Record<string, number>;
    upcoming: Array<Record<string, unknown>>;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [h, w, s] = await Promise.all([
        getDeepSystemHealth(),
        getSystemWorkersStatus(),
        getSystemSchedulerStatus(),
      ]);
      setHealth(h);
      setWorkers(w);
      setSched(s);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load system diagnostics");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => {
      void loadData();
    }, 0);
    return () => clearTimeout(t);
  }, [loadData]);

  return (
    <div className="space-y-6">
        <PageHeader
          title="System & Integration Health"
          description="Real-time operational diagnostics probing PostgreSQL latency, API error/latency counters, scheduler ticks, agent workers, external integrations, and NovaShop demo services."
          actions={
            <button
              type="button"
              onClick={() => void loadData()}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-medium text-slate-200 hover:bg-slate-800"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
              Run Probes
            </button>
          }
        />

        {error && (
          <div className="flex items-center gap-2 rounded-xl border border-rose-500/30 bg-rose-950/30 px-4 py-3 text-xs text-rose-200">
            <AlertTriangle className="h-4 w-4 text-rose-400" />
            <span>{error}</span>
          </div>
        )}

        {loading && !health ? (
          <LoadingSkeleton rows={6} />
        ) : health ? (
          <>
            {/* Overall Banner */}
            <div
              className={`flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-3 text-xs ${
                health.status === "healthy"
                  ? "border-emerald-500/30 bg-emerald-950/20 text-emerald-100"
                  : health.status === "degraded"
                    ? "border-amber-500/30 bg-amber-950/20 text-amber-100"
                    : "border-rose-500/30 bg-rose-950/20 text-rose-100"
              }`}
            >
              <div className="flex items-center gap-2">
                {health.status === "healthy" ? (
                  <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                ) : (
                  <AlertTriangle className="h-4 w-4 text-amber-400" />
                )}
                <span className="font-semibold uppercase">Overall Status: {health.status}</span>
              </div>
              <span className="font-mono text-[11px] opacity-80">
                Checked: {new Date(health.checked_at).toLocaleString()}
              </span>
            </div>

            {/* Subsystem Summary Cards */}
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
              <MetricCard
                label="Database Probe"
                value={health.database.status === "ok" ? `${health.database.latency_ms} ms` : "ERROR"}
                subtext={health.database.error || "SELECT 1 health probe"}
                tone={health.database.status === "ok" ? "emerald" : "rose"}
                icon={<Database className="h-4 w-4" />}
              />
              <MetricCard
                label="Backend Avg Latency"
                value={
                  health.backend_api.avg_latency_ms !== null &&
                  health.backend_api.avg_latency_ms !== undefined
                    ? `${health.backend_api.avg_latency_ms} ms`
                    : "—"
                }
                subtext={`${health.backend_api.requests_total} reqs · ${health.backend_api.errors_5xx} 5xx`}
                tone={health.backend_api.errors_5xx > 0 ? "amber" : "default"}
                icon={<Server className="h-4 w-4" />}
              />
              <MetricCard
                label="Scheduler Worker"
                value={health.scheduler.running ? "Running" : "Stopped"}
                subtext={`${health.scheduler.ticks_completed} ticks · ${health.scheduler.attention_schedules} failing`}
                tone={health.scheduler.attention_schedules > 0 ? "amber" : "emerald"}
                icon={<CalendarClock className="h-4 w-4" />}
              />
              <MetricCard
                label="Active Workers"
                value={health.workers.active_agent_tasks + health.workers.running_job_executions}
                subtext={`${health.workers.failed_agent_tasks} agent fails · ${health.workers.failed_job_executions} job fails`}
                icon={<Bot className="h-4 w-4" />}
              />
              <MetricCard
                label="Demo Backend"
                value={String(health.demo_app.backend.status).toUpperCase()}
                subtext={
                  health.demo_app.backend.latency_ms
                    ? `${health.demo_app.backend.latency_ms} ms`
                    : health.demo_app.backend.error || "NovaShop storefront API"
                }
                tone={health.demo_app.backend.status === "ok" ? "emerald" : "amber"}
                icon={<ShoppingBag className="h-4 w-4" />}
              />
            </div>

            {/* External Integrations + Scheduler */}
            <div className="grid gap-6 lg:grid-cols-2">
              <SectionCard
                title="External Integrations"
                subtitle="Configured status, verification timestamps & analytics mode (secrets redacted)"
                actions={
                  <Link href="/settings" className="text-xs text-indigo-300 hover:text-indigo-200">
                    Manage in Settings →
                  </Link>
                }
              >
                <div className="divide-y divide-slate-800/80 text-xs">
                  {(["github", "flagsmith", "gemini"] as const).map((prov) => {
                    const info = health.integrations[prov];
                    return (
                      <div key={prov} className="flex items-center justify-between py-3">
                        <div className="space-y-0.5">
                          <div className="flex items-center gap-2 font-semibold capitalize text-slate-100">
                            <Plug className="h-3.5 w-3.5 text-slate-400" />
                            {prov}
                            <StatusBadge status={String(info.status ?? "unknown")} />
                          </div>
                          <div className="text-[11px] text-slate-400">
                            {Boolean(info.account) && <span>Account: {String(info.account)} · </span>}
                            {Boolean(info.model) && <span>Model: {String(info.model)} · </span>}
                            {Boolean(info.analytics_mode) && (
                              <span>Analytics: {String(info.analytics_mode)} · </span>
                            )}
                            {Boolean(info.last_verified_at) && (
                              <span>Verified: {new Date(String(info.last_verified_at)).toLocaleString()}</span>
                            )}
                          </div>
                        </div>
                        <span className="font-mono text-[11px] text-slate-400">
                          {info.configured ? "Configured" : "Not Configured"}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </SectionCard>

              <SectionCard
                title="Scheduler & Upcoming Queue"
                subtitle="Background job loop telemetry & next scheduled triggers"
                actions={
                  <Link href="/schedules" className="text-xs text-indigo-300 hover:text-indigo-200">
                    Open Schedules →
                  </Link>
                }
              >
                {sched && (
                  <div className="space-y-3 text-xs">
                    <div className="grid grid-cols-4 gap-2 rounded-lg border border-slate-800 bg-slate-950/60 p-2.5 text-center">
                      <div>
                        <div className="text-[10px] text-slate-400">Total</div>
                        <div className="font-semibold">{sched.counts.total ?? 0}</div>
                      </div>
                      <div>
                        <div className="text-[10px] text-slate-400">Enabled</div>
                        <div className="font-semibold text-emerald-300">{sched.counts.enabled ?? 0}</div>
                      </div>
                      <div>
                        <div className="text-[10px] text-slate-400">Paused</div>
                        <div className="font-semibold text-slate-300">{sched.counts.paused ?? 0}</div>
                      </div>
                      <div>
                        <div className="text-[10px] text-slate-400">Failing</div>
                        <div className="font-semibold text-rose-300">{sched.counts.failing ?? 0}</div>
                      </div>
                    </div>
                    {sched.upcoming.length === 0 ? (
                      <p className="text-slate-400">No active schedules queued.</p>
                    ) : (
                      <div className="divide-y divide-slate-800/80">
                        {sched.upcoming.slice(0, 6).map((u) => (
                          <div key={String(u.id)} className="flex items-center justify-between py-2">
                            <div>
                              <span className="font-medium text-slate-100">{String(u.name)}</span>
                              <span className="ml-2 font-mono text-[10px] text-slate-400">
                                {String(u.job_type)}
                              </span>
                            </div>
                            <span className="font-mono text-[11px] text-cyan-300">
                              {u.next_run_at ? new Date(String(u.next_run_at)).toLocaleString() : "—"}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </SectionCard>
            </div>

            {/* Recent Worker & Job Execution Logs */}
            {workers && (
              <div className="grid gap-6 lg:grid-cols-2">
                <SectionCard title="Recent Agent Executions" subtitle="Latest 15 LangGraph agent runs">
                  {workers.agent_tasks.length === 0 ? (
                    <p className="text-xs text-slate-400">No agent tasks recorded.</p>
                  ) : (
                    <div className="divide-y divide-slate-800/80 text-xs">
                      {workers.agent_tasks.slice(0, 10).map((t) => (
                        <div key={String(t.id)} className="flex items-center justify-between py-2">
                          <div>
                            <span className="font-medium text-slate-200">{String(t.specialist)}</span>
                            <span className="ml-2 font-mono text-[10px] text-slate-500">
                              {String(t.gemini_model ?? "")}
                            </span>
                            {Boolean(t.error_message) && (
                              <div className="text-[11px] text-rose-300">{String(t.error_message)}</div>
                            )}
                          </div>
                          <StatusBadge status={String(t.status)} />
                        </div>
                      ))}
                    </div>
                  )}
                </SectionCard>

                <SectionCard title="Recent Scheduled Job Runs" subtitle="Latest 15 scheduler job executions">
                  {workers.job_executions.length === 0 ? (
                    <p className="text-xs text-slate-400">No job executions recorded.</p>
                  ) : (
                    <div className="divide-y divide-slate-800/80 text-xs">
                      {workers.job_executions.slice(0, 10).map((j) => (
                        <div key={String(j.id)} className="flex items-center justify-between py-2">
                          <div>
                            <span className="font-medium text-slate-200">{String(j.schedule_name)}</span>
                            <span className="ml-2 font-mono text-[10px] text-slate-500">
                              {String(j.trigger_type)}
                            </span>
                            {Boolean(j.error_details) && (
                              <div className="text-[11px] text-rose-300">{String(j.error_details)}</div>
                            )}
                          </div>
                          <StatusBadge status={String(j.status)} />
                        </div>
                      ))}
                    </div>
                  )}
                </SectionCard>
              </div>
            )}

            {/* Demo Frontend + API Error Footer */}
            <SectionCard title="NovaShop Demo Storefront Telemetry Status" actions={<Activity className="h-4 w-4 text-slate-400" />}>
              <div className="grid gap-4 sm:grid-cols-2 text-xs">
                <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3 flex items-center justify-between">
                  <div>
                    <div className="font-semibold text-slate-100">Demo Backend API (8001)</div>
                    <div className="text-[11px] text-slate-400">{health.demo_app.backend.url || "Port 8001"}</div>
                  </div>
                  <StatusBadge status={health.demo_app.backend.status} />
                </div>
                <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3 flex items-center justify-between">
                  <div>
                    <div className="font-semibold text-slate-100">Demo Frontend App (3001)</div>
                    <div className="text-[11px] text-slate-400">{health.demo_app.frontend.url || "Port 3001"}</div>
                  </div>
                  <StatusBadge status={health.demo_app.frontend.status} />
                </div>
              </div>
            </SectionCard>
          </>
        ) : null}
      </div>
  );
}
