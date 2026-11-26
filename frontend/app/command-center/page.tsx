"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  ArrowRight,
  Bot,
  Lock,
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
  createAgentSession,
  getAgentTask,
  getErrorMessage,
  getExecutionOverview,
  listAgentExecutions,
  listRepositories,
  pauseAgentExecution,
  resumeAgentExecution,
  retryAgentExecution,
  sendAgentMessage,
} from "@/lib/api";
import { subscribeToAgentTask } from "@/lib/sse";
import type {
  AgentExecutionOverview,
  AgentExecutionRecord,
  AgentExecutionStatus,
  AgentTask,
  Repository,
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

const INTENT_EXAMPLES: Array<{ label: string; prompt: string }> = [
  {
    label: "Feature Request",
    prompt:
      "Add a new recommendation engine using personalized recommendations. Protect it with a feature flag, compare it against the existing recommendation engine, and gradually release it if the new version performs better.",
  },
  {
    label: "PR Review",
    prompt:
      "Review the latest open PR and identify changes that should be protected by feature flags.",
  },
  {
    label: "Experiment",
    prompt:
      "Compare the new recommendation engine against the existing implementation using Bayesian analysis.",
  },
  {
    label: "Winner Promotion",
    prompt:
      "The experiment has finished. Explain the results and prepare the winning version for human approval.",
  },
  {
    label: "Cleanup",
    prompt:
      "Find stale feature flags in this repository and prepare cleanup Draft PRs.",
  },
  {
    label: "Identity Explanation",
    prompt:
      "Explain which feature flags user_123 receives in the development environment.",
  },
];

function mapExecutionStateLabel(status: string): string {
  switch (status) {
    case "running":
    case "queued":
      return "ANALYZING / EXECUTING";
    case "waiting_for_approval":
      return "AWAITING_APPROVAL";
    case "completed":
      return "VERIFIED";
    case "paused":
    case "cancelled":
      return "BLOCKED";
    case "failed":
    case "timed_out":
      return "FAILED";
    default:
      return status.toUpperCase();
  }
}

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

  const [nlPrompt, setNlPrompt] = useState("");
  const [nlEnv, setNlEnv] = useState("development");
  const [nlDispatching, setNlDispatching] = useState(false);
  const [liveTask, setLiveTask] = useState<AgentTask | null>(null);
  const [repos, setRepos] = useState<Repository[]>([]);

  useEffect(() => {
    listRepositories()
      .then((res) => setRepos(res.items))
      .catch(() => {});
  }, []);

  const handleDispatchPrompt = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = nlPrompt.trim();
    if (!trimmed) return;
    setNlDispatching(true);
    setError(null);
    setNotice(null);
    try {
      const repoId = repos[0]?.id;
      const session = await createAgentSession({
        title: trimmed.slice(0, 80),
        context_scope: {
          environment: nlEnv,
          ...(repoId ? { repository_id: repoId } : {}),
        },
      });
      const dispatched = await sendAgentMessage(session.id, { content: trimmed });
      const initialTask = await getAgentTask(dispatched.task_id);
      setLiveTask(initialTask);
      setNotice(
        `Dispatched to ${initialTask.specialist} (state: ${mapExecutionStateLabel(initialTask.status)}).`,
      );
      setRefreshTick((n) => n + 1);
      subscribeToAgentTask(dispatched.task_id, 0, {
        onStep: () => {
          getAgentTask(dispatched.task_id)
            .then((t) => {
              setLiveTask(t);
              setRefreshTick((n) => n + 1);
            })
            .catch(() => {});
        },
        onTaskUpdate: () => {
          getAgentTask(dispatched.task_id)
            .then((t) => {
              setLiveTask(t);
              setRefreshTick((n) => n + 1);
            })
            .catch(() => {});
        },
      });
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setNlDispatching(false);
    }
  };

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
        title="AI Command Center"
        description="Inspect user instructions, supervisor decisions, specialist selection, proposed tools, policy gates, tool results, approval interrupts, and final AI responses."
        badge={
          <span className="inline-flex items-center gap-1 rounded-full border border-indigo-500/35 bg-indigo-500/10 px-2.5 py-0.5 text-[11px] font-medium text-indigo-300">
            <Lock className="h-3 w-3" />
            Hidden CoT Redacted
          </span>
        }
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

      {/* Unified Natural-Language Command Bar */}
      <SectionCard
        title="Ask ReleaseGuard AI"
        subtitle="Submit any release engineering request — ReleaseGuardSupervisor routes to pr_agent, flag_agent, or experiment_agent with PolicyEngine gating."
      >
        <form onSubmit={handleDispatchPrompt} className="space-y-3">
          <div className="flex flex-wrap gap-1.5">
            {INTENT_EXAMPLES.map((ex) => (
              <button
                key={ex.label}
                type="button"
                onClick={() => setNlPrompt(ex.prompt)}
                className="rounded-full border border-slate-700/80 bg-slate-900/90 px-2.5 py-1 text-[11px] font-medium text-slate-300 hover:border-indigo-500/50 hover:text-indigo-200"
              >
                {ex.label}
              </button>
            ))}
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              type="text"
              value={nlPrompt}
              onChange={(e) => setNlPrompt(e.target.value)}
              placeholder="e.g. Explain which feature flags user_123 receives in development, or review PR #12..."
              className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
            />
            <select
              value={nlEnv}
              onChange={(e) => setNlEnv(e.target.value)}
              className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200"
            >
              <option value="development">development</option>
              <option value="staging">staging</option>
              <option value="production">production (Cat C gated)</option>
            </select>
            <button
              type="submit"
              disabled={nlDispatching || !nlPrompt.trim()}
              className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
            >
              <Play className="h-3.5 w-3.5" />
              {nlDispatching ? "Dispatching..." : "Run Agent"}
            </button>
          </div>
        </form>

        {liveTask && (
          <div className="mt-4 rounded-xl border border-indigo-500/30 bg-indigo-500/5 p-3 space-y-2 text-xs">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="font-mono font-semibold text-indigo-300">
                  {liveTask.specialist}
                </span>
                <span className="rounded border border-slate-700 bg-slate-900 px-2 py-0.5 font-mono text-[10px] text-slate-200">
                  {mapExecutionStateLabel(liveTask.status)}
                </span>
              </div>
              <Link
                href={`/agent`}
                className="inline-flex items-center gap-1 text-indigo-300 hover:text-indigo-200"
              >
                Open Full Trace <ArrowRight className="h-3 w-3" />
              </Link>
            </div>
            <p className="text-slate-300">{liveTask.user_prompt}</p>
            {liveTask.final_response && (
              <div className="rounded-lg border border-slate-800 bg-slate-950/80 p-2.5 text-slate-200 whitespace-pre-wrap">
                {liveTask.final_response}
              </div>
            )}
            {liveTask.pending_approvals && liveTask.pending_approvals.length > 0 && (
              <div className="flex items-center justify-between rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-amber-200">
                <span>
                  Human approval required ({liveTask.pending_approvals.length} pending gate)
                </span>
                <Link href="/approvals" className="font-semibold underline">
                  Review in Approvals
                </Link>
              </div>
            )}
          </div>
        )}
      </SectionCard>

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
        title={`Active & Approval-Gated Executions (${activeExecutions.length})`}
        subtitle="Currently running, waiting for human approval, or paused agent workflows"
      >
        {loading ? (
          <LoadingSkeleton rows={3} />
        ) : activeExecutions.length === 0 ? (
          <EmptyState
            icon={<Bot className="h-5 w-5" />}
            title="No active agent executions"
            description="Start an AI-powered release from the Dashboard or trigger PR analysis from a Release to observe live execution."
          />
        ) : (
          <div className="grid gap-3 lg:grid-cols-2">
            {activeExecutions.map((exe) => (
              <div
                key={exe.id}
                className={`rounded-xl border p-4 space-y-3 ${
                  exe.status === "waiting_for_approval"
                    ? "border-amber-500/45 bg-amber-500/5"
                    : "border-slate-800 bg-[#0d1119]"
                }`}
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
                      Specialist: <span className="text-slate-200">{exe.agent_name}</span> ({exe.agent_type}) · stage:{" "}
                      {exe.current_stage || "—"}
                    </div>
                  </div>
                  <StatusBadge status={exe.status} />
                </div>

                {exe.task_description && (
                  <p className="line-clamp-2 rounded-lg border border-slate-800/80 bg-[#090c12] px-2.5 py-1.5 text-[11px] text-slate-300">
                    <span className="font-semibold text-slate-400">Instruction: </span>
                    {exe.task_description}
                  </p>
                )}

                {exe.current_activity && (
                  <p className="text-xs text-slate-300">{exe.current_activity}</p>
                )}

                <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-800/80 pt-2.5 text-[11px] text-slate-400">
                  <span className="font-mono">
                    {exe.total_steps
                      ? `${exe.completed_steps}/${exe.total_steps} steps`
                      : `stage: ${exe.current_stage || "active"}`}
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
                      Inspect Trace
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
        subtitle="Filterable audit trail across all supervised agent runs"
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
            <option value="">All Specialists</option>
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
                  <th className="py-2 pr-3">Specialist</th>
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
