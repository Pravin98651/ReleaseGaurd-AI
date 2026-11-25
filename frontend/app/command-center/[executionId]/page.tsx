"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  Activity,
  ArrowLeft,
  Clock,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Terminal,
  XCircle,
} from "lucide-react";
import {
  API_BASE_URL,
  cancelAgentExecution,
  getAgentExecutionDetail,
  getErrorMessage,
  pauseAgentExecution,
  resumeAgentExecution,
  retryAgentExecution,
} from "@/lib/api";
import type { AgentExecutionDetail } from "@/types/releaseguard";
import { formatTimestamp } from "@/lib/utils";
import {
  ErrorBanner,
  LoadingSkeleton,
  PageHeader,
  SectionCard,
  SuccessBanner,
} from "@/components/ui/StatePrimitives";
import { StatusBadge } from "@/components/StatusBadges";
import { ApprovalActionCard } from "@/components/ApprovalActionCard";

const SOURCE_LABELS: Record<string, string> = {
  ai_recommendation: "AI Recommendation",
  deterministic_rule: "Deterministic Rule",
  verified_fact: "Verified Fact",
  human_decision: "Human Decision",
};

export default function ExecutionDetailPage() {
  const params = useParams<{ executionId: string }>();
  const executionId = params?.executionId ?? "";

  const [detail, setDetail] = useState<AgentExecutionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [tab, setTab] = useState<"timeline" | "tools" | "decisions" | "approvals" | "checkpoints">(
    "timeline",
  );
  const [traceFilter, setTraceFilter] = useState("");
  const [refreshTick, setRefreshTick] = useState(0);

  useEffect(() => {
    if (!executionId) return;
    let active = true;
    getAgentExecutionDetail(executionId)
      .then((res) => {
        if (!active) return;
        setDetail(res);
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
  }, [executionId, refreshTick]);

  useEffect(() => {
    if (!executionId || typeof EventSource === "undefined") return;
    const es = new EventSource(`${API_BASE_URL}/api/v1/agent-executions/${executionId}/stream`, {
      withCredentials: true,
    });
    const onEvt = () => setRefreshTick((n) => n + 1);
    es.addEventListener("execution.updated", onEvt);
    es.addEventListener("execution.decision", onEvt);
    return () => {
      es.close();
    };
  }, [executionId]);

  const runControl = async (key: string, fn: () => Promise<unknown>, msg: string) => {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      await fn();
      setNotice(msg);
      setRefreshTick((n) => n + 1);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  if (loading) {
    return <LoadingSkeleton rows={6} />;
  }

  if (!detail) {
    return (
      <div className="space-y-4">
        <ErrorBanner error={error || "Execution not found."} onDismiss={() => setError(null)} />
        <Link
          href="/command-center"
          className="inline-flex items-center gap-1.5 text-xs text-indigo-300 hover:underline"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Back to Command Center
        </Link>
      </div>
    );
  }

  const { execution: exe, steps, tool_calls, decisions, traces, checkpoints, approvals } = detail;
  const isTerminal = ["completed", "failed", "cancelled", "timed_out"].includes(exe.status);
  const retryable = ["failed", "timed_out", "interrupted"].includes(exe.status);
  const filteredTraces = traceFilter.trim()
    ? traces.filter((t) =>
        `${t.event_type} ${t.category} ${t.message}`
          .toLowerCase()
          .includes(traceFilter.trim().toLowerCase()),
      )
    : traces;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <Link href="/command-center" className="inline-flex items-center gap-1 hover:text-slate-200">
          <ArrowLeft className="h-3.5 w-3.5" />
          Command Center
        </Link>
        <span>/</span>
        <span className="font-mono text-indigo-300">{exe.execution_key}</span>
      </div>

      <PageHeader
        title={`${exe.execution_key}: ${exe.task_name}`}
        description={`${exe.agent_name} (${exe.agent_type}) · correlation: ${exe.correlation_id} · v${exe.version}`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {exe.release_id && (
              <Link
                href={`/releases/${exe.release_id}`}
                className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-500/40 bg-indigo-500/10 px-3 py-1.5 text-xs text-indigo-200 hover:bg-indigo-500/20"
              >
                Open Release
              </Link>
            )}
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

      {/* Current Stage, Heartbeat & Controls */}
      <SectionCard
        title="Current Task & Execution Controls"
        subtitle={exe.current_activity || "Live execution state"}
        actions={<StatusBadge status={exe.status} />}
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-xs">
          <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3">
            <div className="text-slate-400">Current Stage</div>
            <div className="mt-1 font-mono font-semibold text-slate-100">
              {exe.current_stage || "—"}
            </div>
          </div>
          <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3">
            <div className="text-slate-400">Progress</div>
            <div className="mt-1 font-mono font-semibold text-slate-100">
              {exe.total_steps
                ? `${exe.completed_steps} / ${exe.total_steps} steps`
                : `${exe.completed_steps} steps completed`}
            </div>
          </div>
          <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3">
            <div className="text-slate-400">Duration</div>
            <div className="mt-1 font-mono font-semibold text-slate-100">
              {exe.duration_ms !== null && exe.duration_ms !== undefined
                ? `${(exe.duration_ms / 1000).toFixed(2)}s`
                : "In progress"}
            </div>
          </div>
          <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3">
            <div className="text-slate-400">Last Heartbeat</div>
            <div className="mt-1 font-mono text-slate-200">
              {formatTimestamp(exe.last_heartbeat_at)}
            </div>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          {!isTerminal && exe.status !== "paused" && (
            <button
              type="button"
              disabled={busy !== null}
              onClick={() =>
                void runControl(
                  "pause",
                  () => pauseAgentExecution(exe.id, { expected_version: exe.version }),
                  "Pause requested at safe checkpoint.",
                )
              }
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800"
            >
              <Pause className="h-3.5 w-3.5" />
              Pause
            </button>
          )}
          {exe.status === "paused" && (
            <button
              type="button"
              disabled={busy !== null}
              onClick={() =>
                void runControl(
                  "resume",
                  () => resumeAgentExecution(exe.id, { expected_version: exe.version }),
                  "Execution resumed from checkpoint.",
                )
              }
              className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-xs font-semibold text-emerald-200 hover:bg-emerald-500/20"
            >
              <Play className="h-3.5 w-3.5" />
              Resume
            </button>
          )}
          {retryable && (
            <button
              type="button"
              disabled={busy !== null}
              onClick={() =>
                void runControl(
                  "retry",
                  () => retryAgentExecution(exe.id, { expected_version: exe.version }),
                  "Execution retry initiated.",
                )
              }
              className="inline-flex items-center gap-1.5 rounded-lg border border-sky-500/40 bg-sky-500/10 px-3 py-1.5 text-xs font-semibold text-sky-200 hover:bg-sky-500/20"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              Retry
            </button>
          )}
          {!isTerminal && (
            <button
              type="button"
              disabled={busy !== null}
              onClick={() =>
                void runControl(
                  "cancel",
                  () => cancelAgentExecution(exe.id, { expected_version: exe.version }),
                  "Execution cancelled.",
                )
              }
              className="inline-flex items-center gap-1.5 rounded-lg border border-rose-500/35 bg-rose-500/10 px-3 py-1.5 text-xs font-medium text-rose-200 hover:bg-rose-500/20"
            >
              <XCircle className="h-3.5 w-3.5" />
              Cancel
            </button>
          )}
        </div>
      </SectionCard>

      {/* Planned / Completed Steps */}
      {steps.length > 0 && (
        <SectionCard title="Execution Stages">
          <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
            {steps.map((st) => (
              <div
                key={st.id}
                className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 text-xs"
              >
                <div className="mb-1 flex items-center justify-between">
                  <span className="font-mono text-[10px] text-slate-500">#{st.sequence}</span>
                  <StatusBadge status={st.status} />
                </div>
                <div className="font-semibold text-slate-100">{st.step_name}</div>
                {st.duration_ms !== null && st.duration_ms !== undefined && (
                  <div className="mt-1 font-mono text-[10px] text-slate-400">
                    {st.duration_ms}ms
                  </div>
                )}
              </div>
            ))}
          </div>
        </SectionCard>
      )}

      {/* Inspector Tabs */}
      <div className="flex flex-wrap gap-2 border-b border-slate-800 pb-2 text-xs">
        {(
          [
            ["timeline", `Live Timeline (${traces.length})`],
            ["tools", `Tool Calls (${tool_calls.length})`],
            ["decisions", `Decisions & Evidence (${decisions.length})`],
            ["approvals", `Human Decisions (${approvals.length})`],
            ["checkpoints", `Checkpoints (${checkpoints.length})`],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`rounded-lg px-3 py-1.5 font-medium ${
              tab === key
                ? "border border-indigo-500/40 bg-indigo-500/20 text-indigo-200"
                : "text-slate-400 hover:bg-slate-900 hover:text-slate-200"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "timeline" && (
        <SectionCard
          title="Execution Timeline"
          actions={
            <input
              type="text"
              value={traceFilter}
              onChange={(e) => setTraceFilter(e.target.value)}
              placeholder="Filter timeline..."
              className="rounded-lg border border-slate-700 bg-[#0d1119] px-2.5 py-1 text-xs text-slate-100 placeholder:text-slate-500"
            />
          }
        >
          <div className="space-y-2">
            {filteredTraces.map((tr) => (
              <div
                key={tr.id}
                className="flex flex-wrap items-start justify-between gap-2 rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2 text-xs"
              >
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[10px] text-slate-500">
                      #{tr.sequence_number}
                    </span>
                    <span className="font-semibold text-slate-100">{tr.message}</span>
                  </div>
                  <div className="mt-0.5 font-mono text-[10px] text-slate-500">
                    {tr.event_type} · {tr.category}
                  </div>
                </div>
                <span className="inline-flex items-center gap-1 font-mono text-[10px] text-slate-400">
                  <Clock className="h-3 w-3" />
                  {formatTimestamp(tr.created_at)}
                </span>
              </div>
            ))}
          </div>
        </SectionCard>
      )}

      {tab === "tools" && (
        <SectionCard title="Tool Call Inspector">
          {tool_calls.length === 0 ? (
            <p className="text-xs text-slate-400">No tool calls recorded yet.</p>
          ) : (
            <div className="space-y-2.5">
              {tool_calls.map((tc) => (
                <div
                  key={tc.id}
                  className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 text-xs space-y-2"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <Terminal className="h-3.5 w-3.5 text-sky-400" />
                      <span className="font-mono font-semibold text-slate-100">
                        {tc.tool_name}
                      </span>
                      <span className="rounded border border-slate-700 px-1.5 py-0.5 text-[10px] text-slate-400">
                        {tc.tool_category}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      {tc.duration_ms !== null && tc.duration_ms !== undefined && (
                        <span className="font-mono text-[11px] text-slate-400">
                          {tc.duration_ms}ms
                        </span>
                      )}
                      <StatusBadge status={tc.status} />
                    </div>
                  </div>
                  {tc.error_message && (
                    <div className="rounded border border-rose-500/30 bg-rose-500/10 px-2.5 py-1.5 text-rose-200">
                      {tc.error_code ? `${tc.error_code}: ` : ""}
                      {tc.error_message}
                    </div>
                  )}
                  <details className="text-[11px] text-slate-400">
                    <summary className="cursor-pointer hover:text-slate-200">
                      Redacted Input / Output
                    </summary>
                    <pre className="mt-2 overflow-x-auto rounded bg-[#090c12] p-2 text-[10px] text-slate-300">
                      {JSON.stringify(
                        { input: tc.input_summary, output: tc.output_summary },
                        null,
                        2,
                      )}
                    </pre>
                  </details>
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      )}

      {tab === "decisions" && (
        <SectionCard title="Decisions & Supporting Evidence">
          {decisions.length === 0 ? (
            <p className="text-xs text-slate-400">No decisions recorded yet.</p>
          ) : (
            <div className="space-y-3">
              {decisions.map((dec) => (
                <div
                  key={dec.id}
                  className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 text-xs space-y-2"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <ShieldCheck className="h-3.5 w-3.5 text-indigo-400" />
                      <span className="font-semibold text-slate-100">{dec.decision_type}</span>
                      <span className="rounded-full border border-indigo-500/35 bg-indigo-500/10 px-2 py-0.5 text-[10px] font-medium text-indigo-200">
                        {SOURCE_LABELS[dec.decision_source] ?? dec.decision_source}
                      </span>
                    </div>
                    <StatusBadge status={dec.decision_status} />
                  </div>
                  <p className="text-slate-200">{dec.decision_summary}</p>
                  {dec.recommendation && (
                    <p className="text-slate-400">
                      <strong className="text-slate-300">Recommendation:</strong>{" "}
                      {dec.recommendation}
                    </p>
                  )}
                  {dec.supporting_evidence.length > 0 && (
                    <ul className="list-disc pl-5 text-[11px] text-slate-400">
                      {dec.supporting_evidence.map((ev, idx) => (
                        <li key={idx}>{ev}</li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      )}

      {tab === "approvals" && (
        <div className="space-y-3">
          {approvals.length === 0 ? (
            <SectionCard title="Human Decisions">
              <p className="text-xs text-slate-400">No approval gates linked to this execution.</p>
            </SectionCard>
          ) : (
            approvals.map((app) => (
              <ApprovalActionCard
                key={app.id}
                approval={app}
                onUpdated={() => setRefreshTick((n) => n + 1)}
              />
            ))
          )}
        </div>
      )}

      {tab === "checkpoints" && (
        <SectionCard title="Recoverable Checkpoints & Metadata">
          <div className="mb-3 grid gap-2 sm:grid-cols-3 text-xs">
            <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-2.5">
              <span className="text-slate-400">Correlation ID:</span>{" "}
              <span className="font-mono text-slate-200">{exe.correlation_id}</span>
            </div>
            <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-2.5">
              <span className="text-slate-400">Retries:</span>{" "}
              <span className="font-mono text-slate-200">
                {exe.retry_count} / {exe.max_retries}
              </span>
            </div>
            <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-2.5">
              <span className="text-slate-400">Started:</span>{" "}
              <span className="font-mono text-slate-200">{formatTimestamp(exe.started_at)}</span>
            </div>
          </div>
          {checkpoints.length === 0 ? (
            <p className="text-xs text-slate-400">No checkpoints saved yet.</p>
          ) : (
            <div className="space-y-2">
              {checkpoints.map((cp) => (
                <div
                  key={cp.id}
                  className="flex items-center justify-between rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2 text-xs"
                >
                  <div>
                    <Activity className="mr-1.5 inline h-3.5 w-3.5 text-emerald-400" />
                    <span className="font-mono font-semibold text-slate-100">
                      Checkpoint #{cp.checkpoint_number} ({cp.checkpoint_type})
                    </span>
                    <span className="ml-2 text-slate-400">
                      state: {cp.resumable_state} · {cp.completed_step_ids.length} steps completed
                    </span>
                  </div>
                  <span className="font-mono text-[10px] text-slate-500">
                    {formatTimestamp(cp.created_at)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      )}
    </div>
  );
}
