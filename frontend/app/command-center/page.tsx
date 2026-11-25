"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  ArrowRight,
  Bot,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldAlert,
  Wifi,
  WifiOff,
  XCircle,
} from "lucide-react";
import {
  API_BASE_URL,
  cancelAgentExecution,
  getErrorMessage,
  getExecutionOverview,
  listAgentExecutions,
  pauseAgentExecution,
  resumeAgentExecution,
  retryAgentExecution,
} from "@/lib/api";
import type {
  AgentExecutionOverview,
  AgentExecutionRecord,
  AgentExecutionStatus,
} from "@/types/releaseguard";
import { formatTimestamp } from "@/lib/utils";
import {
  EmptyState,
  ErrorBanner,
  LoadingSkeleton,
  MetricCard,
  PageHeader,
  SectionCard,
  SuccessBanner,
} from "@/components/ui/StatePrimitives";
import { StatusBadge } from "@/components/StatusBadges";

const TERMINAL = new Set(["completed", "failed", "cancelled", "timed_out"]);

export default function CommandCenterPage() {
  const [overview, setOverview] = useState<AgentExecutionOverview | null>(null);
  const [executions, setExecutions] = useState<AgentExecutionRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [sseConnected, setSseConnected] = useState(false);

  const [search, setSearch] = useState("");
  const [agentType, setAgentType] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [failedOnly, setFailedOnly] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);

  useEffect(() => {
    let active = true;
    Promise.all([
      getExecutionOverview(),
      listAgentExecutions({
        q: search.trim() || undefined,
        agent_type: agentType || undefined,
        status: (statusFilter || undefined) as AgentExecutionStatus | undefined,
        failed_only: failedOnly || undefined,
        limit: 50,
      }),
    ])
      .then(([ov, listRes]) => {
        if (!active) return;
        setOverview(ov);
        setExecutions(listRes.items);
        setTotal(listRes.total);
        setError(null);
      })
      .catch((err) => {
        if (!active) return;
        setError(getErrorMessage(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [search, agentType, statusFilter, failedOnly, refreshTick]);

  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    const es = new EventSource(`${API_BASE_URL}/api/v1/agent-executions/stream`, {
      withCredentials: true,
    });
    es.onopen = () => setSseConnected(true);
    es.onerror = () => setSseConnected(false);
    const onEvt = () => setRefreshTick((n) => n + 1);
    es.addEventListener("execution.created", onEvt);
    es.addEventListener("execution.updated", onEvt);
    es.addEventListener("execution.decision", onEvt);
    return () => {
      es.close();
    };
  }, []);

  const runControl = async (
    exeId: string,
    fn: () => Promise<unknown>,
    msg: string,
  ) => {
    setBusyId(exeId);
    setError(null);
    setNotice(null);
    try {
      await fn();
      setNotice(msg);
      setRefreshTick((n) => n + 1);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  const activeExecutions = executions.filter((e) => !TERMINAL.has(e.status));

  return (
    <div className="space-y-6">
      <PageHeader
        title="Autonomous Release Command Center"
        description="Real-time observability, tool-call inspection, evidence-backed decisions, and human-supervised execution controls."
        actions={
          <div className="flex items-center gap-2">
            <span
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium ${
                sseConnected
                  ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                  : "border-slate-700 bg-slate-900 text-slate-400"
              }`}
            >
              {sseConnected ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
              {sseConnected ? "Live SSE" : "Polling"}
            </span>
            <button
              type="button"
              onClick={() => setRefreshTick((n) => n + 1)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-800"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Refresh
            </button>
          </div>
        }
      />

      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <SuccessBanner message={notice} onDismiss={() => setNotice(null)} />

      {/* Operational KPIs */}
      {overview && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <MetricCard
            label="Active Executions"
            value={overview.active_executions}
            subtext={`${overview.queued_executions} queued · ${overview.paused_executions} paused`}
            icon={<Activity className="h-4 w-4" />}
          />
          <MetricCard
            label="Waiting for Approval"
            value={overview.waiting_for_approval}
            subtext="Human authorization gates"
            tone={overview.waiting_for_approval > 0 ? "amber" : "default"}
            icon={<ShieldAlert className="h-4 w-4" />}
          />
          <MetricCard
            label="Failed / Interrupted"
            value={overview.failed_executions}
            subtext={
              overview.tool_failure_rate !== null && overview.tool_failure_rate !== undefined
                ? `Tool failure rate: ${(overview.tool_failure_rate * 100).toFixed(1)}%`
                : "No tool calls recorded yet"
            }
            tone={overview.failed_executions > 0 ? "rose" : "default"}
          />
          <MetricCard
            label="Completed Executions"
            value={overview.completed_executions}
            subtext={
              overview.average_duration_ms !== null && overview.average_duration_ms !== undefined
                ? `Avg duration: ${(overview.average_duration_ms / 1000).toFixed(1)}s`
                : `${overview.total_executions} total`
            }
            tone="emerald"
            icon={<Bot className="h-4 w-4" />}
          />
        </div>
      )}

      {/* Active Executions */}
      <SectionCard
        title={`Active & Paused Executions (${activeExecutions.length})`}
        subtitle="Currently running, waiting for approval, or paused agent workflows"
      >
        {loading ? (
          <LoadingSkeleton rows={3} />
        ) : activeExecutions.length === 0 ? (
          <EmptyState
            icon={<Bot className="h-5 w-5" />}
            title="No active agent executions"
            description="Start an AI Agent session or trigger PR analysis from a Release to observe live execution."
          />
        ) : (
          <div className="grid gap-3 lg:grid-cols-2">
            {activeExecutions.map((exe) => (
              <div
                key={exe.id}
                className="rounded-xl border border-slate-800 bg-[#0d1119] p-4 space-y-3"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <Link
                      href={`/command-center/${exe.id}`}
                      className="text-xs font-semibold text-slate-100 hover:text-indigo-300"
                    >
                      <span className="mr-1.5 font-mono text-indigo-300">{exe.execution_key}</span>
                      {exe.task_name}
                    </Link>
                    <div className="mt-0.5 text-[11px] text-slate-400">
                      {exe.agent_name} · stage: {exe.current_stage || "—"}
                    </div>
                  </div>
                  <StatusBadge status={exe.status} />
                </div>

                {exe.current_activity && (
                  <p className="text-xs text-slate-300">{exe.current_activity}</p>
                )}

                <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-800/80 pt-2.5 text-[11px] text-slate-400">
                  <span className="font-mono">
                    {exe.total_steps
                      ? `${exe.completed_steps}/${exe.total_steps} steps`
                      : `stage: ${exe.current_stage || "active"}`}
                    {" · "}v{exe.version}
                  </span>
                  <div className="flex flex-wrap items-center gap-1.5">
                    {exe.status === "paused" ? (
                      <button
                        type="button"
                        disabled={busyId === exe.id}
                        onClick={() =>
                          void runControl(
                            exe.id,
                            () => resumeAgentExecution(exe.id, { expected_version: exe.version }),
                            `Resumed ${exe.execution_key}`,
                          )
                        }
                        className="inline-flex items-center gap-1 rounded border border-emerald-500/40 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-200 hover:bg-emerald-500/20"
                      >
                        <Play className="h-3 w-3" />
                        Resume
                      </button>
                    ) : (
                      <button
                        type="button"
                        disabled={busyId === exe.id}
                        onClick={() =>
                          void runControl(
                            exe.id,
                            () => pauseAgentExecution(exe.id, { expected_version: exe.version }),
                            `Pause requested on ${exe.execution_key}`,
                          )
                        }
                        className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[11px] text-slate-200 hover:bg-slate-800"
                      >
                        <Pause className="h-3 w-3" />
                        Pause
                      </button>
                    )}
                    <button
                      type="button"
                      disabled={busyId === exe.id}
                      onClick={() =>
                        void runControl(
                          exe.id,
                          () => cancelAgentExecution(exe.id, { expected_version: exe.version }),
                          `Cancelled ${exe.execution_key}`,
                        )
                      }
                      className="inline-flex items-center gap-1 rounded border border-rose-500/35 bg-rose-500/10 px-2 py-1 text-[11px] text-rose-200 hover:bg-rose-500/20"
                    >
                      <XCircle className="h-3 w-3" />
                      Cancel
                    </button>
                    <Link
                      href={`/command-center/${exe.id}`}
                      className="inline-flex items-center gap-1 rounded border border-indigo-500/40 bg-indigo-500/10 px-2 py-1 text-[11px] text-indigo-200 hover:bg-indigo-500/20"
                    >
                      Inspect
                      <ArrowRight className="h-3 w-3" />
                    </Link>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </SectionCard>

      {/* Searchable Execution History */}
      <SectionCard
        title={`Execution History (${total})`}
        subtitle="Filterable audit trail across all agent runs"
      >
        <div className="mb-4 grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-500" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search key, task, agent..."
              className="w-full rounded-lg border border-slate-700 bg-[#0d1119] py-1.5 pl-8 pr-3 text-xs text-slate-100 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
            />
          </div>
          <select
            value={agentType}
            onChange={(e) => setAgentType(e.target.value)}
            aria-label="Filter by agent type"
            className="rounded-lg border border-slate-700 bg-[#0d1119] px-2.5 py-1.5 text-xs text-slate-100"
          >
            <option value="">All Agent Types</option>
            <option value="pr_agent">pr_agent</option>
            <option value="flag_agent">flag_agent</option>
            <option value="experiment_agent">experiment_agent</option>
            <option value="supervisor">supervisor</option>
          </select>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            aria-label="Filter by status"
            className="rounded-lg border border-slate-700 bg-[#0d1119] px-2.5 py-1.5 text-xs text-slate-100"
          >
            <option value="">All Statuses</option>
            <option value="running">Running</option>
            <option value="waiting_for_approval">Waiting for Approval</option>
            <option value="paused">Paused</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="interrupted">Interrupted</option>
            <option value="cancelled">Cancelled</option>
          </select>
          <label className="flex items-center gap-2 rounded-lg border border-slate-700 bg-[#0d1119] px-3 py-1.5 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={failedOnly}
              onChange={(e) => setFailedOnly(e.target.checked)}
            />
            Failed / Interrupted only
          </label>
        </div>

        {loading ? (
          <LoadingSkeleton rows={4} />
        ) : executions.length === 0 ? (
          <EmptyState
            icon={<Activity className="h-5 w-5" />}
            title="No matching executions"
            description="Adjust your filters or trigger a new agent workflow."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-slate-800 text-[11px] uppercase tracking-wider text-slate-400">
                  <th className="py-2 pr-3">Execution</th>
                  <th className="py-2 pr-3">Agent</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3">Stage</th>
                  <th className="py-2 pr-3">Duration</th>
                  <th className="py-2 pr-3">Updated</th>
                  <th className="py-2 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/80">
                {executions.map((exe) => {
                  const retryable = ["failed", "timed_out", "interrupted"].includes(exe.status);
                  return (
                    <tr key={exe.id} className="hover:bg-slate-900/40">
                      <td className="py-2.5 pr-3">
                        <Link
                          href={`/command-center/${exe.id}`}
                          className="font-semibold text-slate-100 hover:text-indigo-300"
                        >
                          <span className="mr-1.5 font-mono text-[11px] text-indigo-300">
                            {exe.execution_key}
                          </span>
                          {exe.task_name}
                        </Link>
                      </td>
                      <td className="py-2.5 pr-3 text-slate-300">{exe.agent_name}</td>
                      <td className="py-2.5 pr-3">
                        <StatusBadge status={exe.status} />
                      </td>
                      <td className="py-2.5 pr-3 font-mono text-[11px] text-slate-400">
                        {exe.current_stage || "—"}
                      </td>
                      <td className="py-2.5 pr-3 font-mono text-[11px] text-slate-300 tabular-nums">
                        {exe.duration_ms !== null && exe.duration_ms !== undefined
                          ? `${(exe.duration_ms / 1000).toFixed(2)}s`
                          : "—"}
                      </td>
                      <td className="py-2.5 pr-3 text-[11px] text-slate-400 tabular-nums">
                        {formatTimestamp(exe.updated_at)}
                      </td>
                      <td className="py-2.5 text-right">
                        <div className="inline-flex items-center gap-1.5">
                          {retryable && (
                            <button
                              type="button"
                              disabled={busyId === exe.id}
                              onClick={() =>
                                void runControl(
                                  exe.id,
                                  () =>
                                    retryAgentExecution(exe.id, { expected_version: exe.version }),
                                  `Retried ${exe.execution_key}`,
                                )
                              }
                              className="inline-flex items-center gap-1 rounded border border-sky-500/40 bg-sky-500/10 px-2 py-1 text-[11px] text-sky-200 hover:bg-sky-500/20"
                            >
                              <RotateCcw className="h-3 w-3" />
                              Retry
                            </button>
                          )}
                          <Link
                            href={`/command-center/${exe.id}`}
                            className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[11px] text-slate-200 hover:bg-slate-800"
                          >
                            Open
                            <ArrowRight className="h-3 w-3" />
                          </Link>
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
  );
}
