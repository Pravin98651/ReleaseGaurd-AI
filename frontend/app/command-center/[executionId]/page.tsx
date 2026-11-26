"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  Activity,
  ArrowLeft,
  Bot,
  Clock,
  Lock,
  MessageSquare,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
  Terminal,
  Wrench,
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

function summarizeJson(obj: Record<string, unknown> | undefined | null): string {
  if (!obj || Object.keys(obj).length === 0) return "—";
  const entries = Object.entries(obj)
    .slice(0, 4)
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`);
  return entries.join(" · ");
}

export default function ExecutionDetailPage() {
  const params = useParams<{ executionId: string }>();
  const executionId = params?.executionId ?? "";

  const [detail, setDetail] = useState<AgentExecutionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [tab, setTab] = useState<
    "overview" | "timeline" | "tools" | "decisions" | "approvals" | "checkpoints"
  >("overview");
  const [traceFilter, setTraceFilter] = useState("");
  const [refreshTick, setRefreshTick] = useState(0);
  const [sseStatus, setSseStatus] = useState<"connecting" | "connected" | "error">("connecting");

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
    const onEvt = () => {
      setSseStatus("connected");
      setRefreshTick((n) => n + 1);
    };
    es.onopen = () => setSseStatus("connected");
    es.onerror = () => setSseStatus("error");
    es.addEventListener("execution.updated", onEvt);
    es.addEventListener("execution.decision", onEvt);
    return () => {
      es.close();
    };
  }, [executionId]);

  const activeAgentTaskId = detail?.execution.agent_task_id;
  useEffect(() => {
    if (!activeAgentTaskId || typeof EventSource === "undefined") return;
    const taskEs = new EventSource(`${API_BASE_URL}/api/v1/agent/tasks/${activeAgentTaskId}/stream`, {
      withCredentials: true,
    });
    const onTaskEvt = () => {
      setSseStatus("connected");
      setRefreshTick((n) => n + 1);
    };
    taskEs.onopen = () => setSseStatus("connected");
    taskEs.onerror = () => setSseStatus("error");
    const events = [
      "task.started",
      "agent.thinking",
      "agent.tool_proposed",
      "policy.evaluated",
      "approval.required",
      "tool.executed",
      "agent.completed",
      "task.failed",
      "task.cancelled",
    ];
    events.forEach((ev) => taskEs.addEventListener(ev, onTaskEvt));
    return () => {
      events.forEach((ev) => taskEs.removeEventListener(ev, onTaskEvt));
      taskEs.close();
    };
  }, [activeAgentTaskId]);

  const exeStatus = detail?.execution.status;
  useEffect(() => {
    if (sseStatus !== "error" || !executionId) return;
    if (exeStatus && ["completed", "failed", "cancelled", "timed_out"].includes(exeStatus)) {
      return;
    }
    const timer = setInterval(() => {
      setRefreshTick((n) => n + 1);
    }, 10000);
    return () => clearInterval(timer);
  }, [sseStatus, executionId, exeStatus]);

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
          <ArrowLeft className="h-3.5 w-3.5" /> Back to AI Command Center
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

  const userInstruction =
    (typeof exe.execution_metadata?.user_instruction === "string" &&
      exe.execution_metadata.user_instruction) ||
    (typeof exe.execution_metadata?.prompt === "string" && exe.execution_metadata.prompt) ||
    exe.task_description ||
    exe.task_name;

  const supervisorDecisions = decisions.filter(
    (d) =>
      d.decision_type.toLowerCase().includes("supervisor") ||
      d.decision_type.toLowerCase().includes("route") ||
      d.decision_type.toLowerCase().includes("plan") ||
      d.decision_source === "ai_recommendation",
  );

  const policyTraces = traces.filter(
    (t) =>
      t.event_type === "policy.evaluated" ||
      t.category.toLowerCase().includes("policy") ||
      t.event_type.includes("policy"),
  );

  const deterministicPolicyDecisions = decisions.filter(
    (d) => d.decision_source === "deterministic_rule",
  );

  const proposedToolTraces = traces.filter(
    (t) => t.event_type === "agent.tool_proposed" || t.event_type.includes("tool_proposed"),
  );

  const finalAiResponse =
    (typeof exe.execution_metadata?.final_response === "string" &&
      exe.execution_metadata.final_response) ||
    (typeof exe.execution_metadata?.final_answer === "string" &&
      exe.execution_metadata.final_answer) ||
    decisions.find((d) => d.decision_source === "ai_recommendation")?.recommendation ||
    decisions.find((d) => d.decision_source === "ai_recommendation")?.decision_summary ||
    null;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <Link href="/command-center" className="inline-flex items-center gap-1 hover:text-slate-200">
          <ArrowLeft className="h-3.5 w-3.5" />
          AI Command Center
        </Link>
        <span>/</span>
        <span className="font-mono text-indigo-300">{exe.execution_key}</span>
      </div>

      <PageHeader
        title={`${exe.execution_key}: ${exe.task_name}`}
        description={`Specialist: ${exe.agent_name} (${exe.agent_type})`}
        badge={
          <span className="inline-flex items-center gap-1 rounded-full border border-indigo-500/35 bg-indigo-500/10 px-2.5 py-0.5 text-[11px] font-medium text-indigo-300">
            <Lock className="h-3 w-3" />
            Hidden CoT Redacted
          </span>
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium ${
                sseStatus === "connected"
                  ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                  : sseStatus === "error"
                    ? "border-amber-500/35 bg-amber-500/10 text-amber-300"
                    : "border-slate-700 bg-slate-900 text-slate-400"
              }`}
            >
              {sseStatus === "connected"
                ? "Live SSE"
                : sseStatus === "error"
                  ? "SSE Disconnected (Polling)"
                  : "Connecting SSE..."}
            </span>
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

      {/* Inspector Tabs */}
      <div className="flex flex-wrap gap-2 border-b border-slate-800 pb-2 text-xs">
        {(
          [
            ["overview", "AI Command Trace"],
            ["timeline", `Live Timeline (${traces.length})`],
            ["tools", `Tool Calls (${tool_calls.length})`],
            ["decisions", `Decisions & Evidence (${decisions.length})`],
            ["approvals", `Approval Interrupts (${approvals.length})`],
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

      {tab === "overview" && (
        <div className="space-y-4 text-xs">
          {/* 1. User Instruction & 3. Specialist Selected */}
          <div className="grid gap-4 lg:grid-cols-3">
            <SectionCard title="1. User Instruction" className="lg:col-span-2">
              <p className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 text-slate-200">
                {userInstruction}
              </p>
            </SectionCard>

            <SectionCard title="3. Specialist Selected">
              <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 space-y-1.5">
                <div className="flex items-center gap-2 font-semibold text-slate-100">
                  <Bot className="h-4 w-4 text-indigo-400" />
                  <span>{exe.agent_name}</span>
                </div>
                <div className="font-mono text-[11px] text-indigo-300">{exe.agent_type}</div>
                <div className="text-[11px] text-slate-400">Mode: {exe.execution_mode}</div>
              </div>
            </SectionCard>
          </div>

          {/* 2. Supervisor Decisions */}
          <SectionCard
            title={`2. Supervisor Decisions (${supervisorDecisions.length})`}
            subtitle="Routing, planning, and structured recommendations (no hidden chain-of-thought)"
          >
            {supervisorDecisions.length === 0 ? (
              <p className="text-slate-400">
                {steps.length > 0
                  ? `Routed to ${exe.agent_name} across ${steps.length} execution step(s).`
                  : "No supervisor routing decisions recorded yet."}
              </p>
            ) : (
              <div className="space-y-2">
                {supervisorDecisions.map((dec) => (
                  <div
                    key={dec.id}
                    className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 space-y-1"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-semibold text-slate-100">{dec.decision_type}</span>
                      <StatusBadge status={dec.decision_status} />
                    </div>
                    <p className="text-slate-300">{dec.decision_summary}</p>
                  </div>
                ))}
              </div>
            )}
          </SectionCard>

          {/* 4. Proposed Tools & 5. Policy Decisions */}
          <div className="grid gap-4 lg:grid-cols-2">
            <SectionCard
              title={`4. Proposed Tools (${proposedToolTraces.length || tool_calls.length})`}
              subtitle="Tools proposed by the selected specialist before execution"
            >
              {proposedToolTraces.length > 0 ? (
                <div className="space-y-2">
                  {proposedToolTraces.map((tr) => (
                    <div
                      key={tr.id}
                      className="flex items-center justify-between rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2"
                    >
                      <div className="flex items-center gap-2">
                        <Wrench className="h-3.5 w-3.5 text-sky-400" />
                        <span className="text-slate-200">{tr.message}</span>
                      </div>
                      <span className="font-mono text-[10px] text-slate-500">
                        {formatTimestamp(tr.created_at)}
                      </span>
                    </div>
                  ))}
                </div>
              ) : tool_calls.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                  {tool_calls.map((tc) => (
                    <span
                      key={tc.id}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-[#0d1119] px-2.5 py-1 font-mono text-[11px] text-slate-200"
                    >
                      <Wrench className="h-3 w-3 text-sky-400" />
                      {tc.tool_name} ({tc.tool_category})
                    </span>
                  ))}
                </div>
              ) : (
                <p className="text-slate-400">No tools proposed yet.</p>
              )}
            </SectionCard>

            <SectionCard
              title={`5. Policy Decisions (${policyTraces.length + deterministicPolicyDecisions.length + approvals.length})`}
              subtitle="Deterministic Category A/B/C/DENY policy evaluations"
            >
              {policyTraces.length === 0 &&
              deterministicPolicyDecisions.length === 0 &&
              approvals.length === 0 ? (
                <p className="text-slate-400">No policy gate evaluations recorded yet.</p>
              ) : (
                <div className="space-y-2">
                  {policyTraces.map((tr) => (
                    <div
                      key={tr.id}
                      className="rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2 text-slate-200"
                    >
                      <ShieldCheck className="mr-1.5 inline h-3.5 w-3.5 text-emerald-400" />
                      {tr.message}
                    </div>
                  ))}
                  {deterministicPolicyDecisions.map((dec) => (
                    <div
                      key={dec.id}
                      className="rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2 text-slate-200"
                    >
                      <span className="font-semibold">{dec.decision_type}:</span>{" "}
                      {dec.decision_summary}
                    </div>
                  ))}
                  {approvals.map((app) => (
                    <div
                      key={app.id}
                      className="flex items-center justify-between rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2"
                    >
                      <span className="text-slate-200">
                        Category {app.policy_category}: {app.title}
                      </span>
                      <StatusBadge status={app.status} />
                    </div>
                  ))}
                </div>
              )}
            </SectionCard>
          </div>

          {/* 6. Actual Tool Results */}
          <SectionCard
            title={`6. Actual Tool Results (${tool_calls.length})`}
            subtitle="Verified inputs, outputs, and execution latency from executed tools"
          >
            {tool_calls.length === 0 ? (
              <p className="text-slate-400">No tool executions recorded yet.</p>
            ) : (
              <div className="space-y-2.5">
                {tool_calls.map((tc) => (
                  <div
                    key={tc.id}
                    className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 space-y-1.5"
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
                    <div className="grid gap-2 sm:grid-cols-2 text-[11px]">
                      <div className="rounded bg-[#090c12] px-2.5 py-1.5 text-slate-300">
                        <span className="font-semibold text-slate-400">Input: </span>
                        {summarizeJson(tc.input_summary)}
                      </div>
                      <div className="rounded bg-[#090c12] px-2.5 py-1.5 text-slate-300">
                        <span className="font-semibold text-slate-400">Result: </span>
                        {summarizeJson(tc.output_summary)}
                      </div>
                    </div>
                    {tc.error_message && (
                      <div className="rounded border border-rose-500/30 bg-rose-500/10 px-2.5 py-1.5 text-rose-200">
                        {tc.error_code ? `${tc.error_code}: ` : ""}
                        {tc.error_message}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </SectionCard>

          {/* 7. Approval Interrupts */}
          <SectionCard
            title={`7. Approval Interrupts (${approvals.length})`}
            subtitle="Human-in-the-loop authorization gates pausing execution until approved or rejected"
          >
            {exe.status === "waiting_for_approval" && (
              <div className="mb-3 flex items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-amber-200">
                <ShieldAlert className="h-4 w-4 shrink-0" />
                <span>
                  Execution is paused at a human approval interrupt. Approve or reject below to resume.
                </span>
              </div>
            )}
            {approvals.length === 0 ? (
              <p className="text-slate-400">No approval interrupts triggered for this execution.</p>
            ) : (
              <div className="space-y-3">
                {approvals.map((app) => (
                  <ApprovalActionCard
                    key={app.id}
                    approval={app}
                    onUpdated={() => setRefreshTick((n) => n + 1)}
                  />
                ))}
              </div>
            )}
          </SectionCard>

          {/* 8. Final AI Response */}
          <SectionCard
            title="8. Final AI Response"
            subtitle="Structured final output (hidden chain-of-thought is never exposed)"
          >
            {finalAiResponse ? (
              <div className="rounded-lg border border-indigo-500/30 bg-indigo-500/5 p-3.5 text-slate-200">
                <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold text-indigo-300">
                  <MessageSquare className="h-3.5 w-3.5" />
                  Final Response
                </div>
                <p className="leading-relaxed">{finalAiResponse}</p>
              </div>
            ) : (
              <p className="text-slate-400">
                {isTerminal
                  ? "Execution finished without a separate final narrative."
                  : "Execution in progress — final AI response will appear upon completion."}
              </p>
            )}
          </SectionCard>
        </div>
      )}

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
            {filteredTraces.length === 0 ? (
              <p className="text-xs text-slate-400">
                {traces.length === 0
                  ? "No execution trace events recorded yet."
                  : "No trace events match your filter."}
              </p>
            ) : (
              filteredTraces.map((tr) => (
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
              ))
            )}
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
                  <div className="grid gap-2 sm:grid-cols-2 text-[11px]">
                    <div className="rounded bg-[#090c12] p-2 text-slate-300">
                      <div className="mb-1 font-semibold text-slate-400">Input Summary</div>
                      <pre className="overflow-x-auto text-[10px]">
                        {JSON.stringify(tc.input_summary, null, 2)}
                      </pre>
                    </div>
                    <div className="rounded bg-[#090c12] p-2 text-slate-300">
                      <div className="mb-1 font-semibold text-slate-400">Output Summary</div>
                      <pre className="overflow-x-auto text-[10px]">
                        {JSON.stringify(tc.output_summary, null, 2)}
                      </pre>
                    </div>
                  </div>
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
            <SectionCard title="Approval Interrupts">
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
          <div className="mb-3 grid gap-2 sm:grid-cols-4 text-xs">
            <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-2.5">
              <span className="text-slate-400">Correlation ID:</span>{" "}
              <span className="font-mono text-slate-200">{exe.correlation_id}</span>
            </div>
            <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-2.5">
              <span className="text-slate-400">Version:</span>{" "}
              <span className="font-mono text-slate-200">v{exe.version}</span>
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
