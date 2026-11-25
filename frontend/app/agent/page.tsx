"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Bot,
  CheckCircle2,
  Cpu,
  Plus,
  Send,
  Shield,
  Sparkles,
  Square,
  Terminal,
  Wrench,
  XCircle,
} from "lucide-react";
import {
  cancelAgentTask,
  createAgentSession,
  getAgentSession,
  getAgentTask,
  getErrorMessage,
  listAgentSessions,
  listRepositories,
  sendAgentMessage,
} from "@/lib/api";
import { subscribeToAgentTask } from "@/lib/sse";
import type {
  AgentSession,
  AgentSpecialist,
  AgentStepEvent,
  AgentTask,
  Approval,
  Repository,
} from "@/types/releaseguard";
import { cn, formatTimestamp, truncateHash } from "@/lib/utils";
import {
  EmptyState,
  ErrorBanner,
  LoadingSkeleton,
  PageHeader,
  SectionCard,
} from "@/components/ui/StatePrimitives";
import { PolicyGateBadge } from "@/components/PolicyGateBadge";
import { StatusBadge } from "@/components/StatusBadges";
import { ApprovalActionCard } from "@/components/ApprovalActionCard";

const QUICK_TEMPLATES: Array<{
  label: string;
  specialist: AgentSpecialist;
  prompt: string;
}> = [
  {
    label: "Analyze Open PR Risk",
    specialist: "pr_agent",
    prompt:
      "Inspect the latest open pull request on our connected repository, separate confirmed vs potential risks, and propose a Flagsmith-guarded Draft PR if risky checkout logic is unflagged.",
  },
  {
    label: "Stage Checkout v2 (25%)",
    specialist: "flag_agent",
    prompt:
      "Roll out checkout_v2 to 25% in staging and verify the live Flagsmith environment state.",
  },
  {
    label: "Bayesian Experiment Eval",
    specialist: "experiment_agent",
    prompt:
      "Evaluate our active checkout_v2 experiment using the Bayesian Beta-Binomial engine, check payment_failed guardrails, and recommend the next rollout stage.",
  },
  {
    label: "Investigate Error Spike",
    specialist: "experiment_agent",
    prompt:
      "Investigate recent payment_failed events in development/staging over the last 60 minutes, check variation confounding, and list temporal correlations.",
  },
];

function StepEventCard({ step }: { step: AgentStepEvent }) {
  const evt = step.event_type;
  const p = step.payload ?? {};

  const icon = (() => {
    if (evt === "task.started") return <Cpu className="h-3.5 w-3.5 text-indigo-400" />;
    if (evt === "agent.thinking") return <Sparkles className="h-3.5 w-3.5 text-cyan-400" />;
    if (evt === "agent.tool_proposed") return <Wrench className="h-3.5 w-3.5 text-sky-400" />;
    if (evt === "policy.evaluated") return <Shield className="h-3.5 w-3.5 text-amber-400" />;
    if (evt === "approval.required")
      return <Shield className="h-3.5 w-3.5 text-rose-400" />;
    if (evt === "tool.executed") return <Terminal className="h-3.5 w-3.5 text-emerald-400" />;
    if (evt === "agent.completed")
      return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />;
    if (evt === "task.failed") return <XCircle className="h-3.5 w-3.5 text-rose-400" />;
    return <Bot className="h-3.5 w-3.5 text-slate-400" />;
  })();

  const borderTone =
    evt === "approval.required"
      ? "border-amber-500/40 bg-amber-500/5"
      : evt === "task.failed"
        ? "border-rose-500/40 bg-rose-500/5"
        : evt === "agent.completed"
          ? "border-emerald-500/30 bg-emerald-500/5"
          : "border-slate-800/90 bg-[#0d1119]";

  return (
    <div className={cn("rounded-lg border p-3 text-xs space-y-1.5", borderTone)}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[10px] text-slate-500 tabular-nums">
            #{step.seq}
          </span>
          {icon}
          <span className="font-mono font-semibold text-slate-100">{evt}</span>
          {typeof p.specialist === "string" && (
            <span className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] text-indigo-300">
              {p.specialist}
            </span>
          )}
          {typeof p.tool_name === "string" && (
            <span className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] text-cyan-300">
              {p.tool_name}
            </span>
          )}
          {typeof p.policy_category === "string" && (
            <PolicyGateBadge category={p.policy_category} compact />
          )}
          {typeof p.decision === "string" && <StatusBadge status={p.decision} />}
          {typeof p.verified === "boolean" && (
            <StatusBadge
              status={p.verified ? "verified" : "failed"}
              label={p.verified ? "Live Verified" : "Mismatch"}
            />
          )}
        </div>
        {step.timestamp && (
          <span className="font-mono text-[10px] text-slate-500 tabular-nums">
            {formatTimestamp(step.timestamp)}
          </span>
        )}
      </div>

      {typeof p.message === "string" && (
        <p className="text-slate-300 leading-relaxed">{p.message}</p>
      )}
      {typeof p.summary === "string" && (
        <p className="text-slate-300 leading-relaxed">{p.summary}</p>
      )}
      {typeof p.title === "string" && (
        <p className="font-medium text-amber-200">{p.title}</p>
      )}
      {typeof p.error === "string" && <p className="text-rose-300">{p.error}</p>}

      {typeof p.action_hash === "string" && (
        <div className="font-mono text-[11px] text-slate-400">
          action_hash: {truncateHash(p.action_hash, 16)}
        </div>
      )}

      {p.args_redacted != null && (
        <details className="rounded border border-slate-800 bg-[#090c12]">
          <summary className="cursor-pointer px-2.5 py-1 text-[11px] text-slate-400">
            Redacted Tool Arguments
          </summary>
          <pre className="max-h-32 overflow-auto border-t border-slate-800 p-2 font-mono text-[11px] text-slate-300">
            {JSON.stringify(p.args_redacted, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}

export default function AgentWorkspacePage() {
  const [sessions, setSessions] = useState<AgentSession[]>([]);
  const [repos, setRepos] = useState<Repository[]>([]);
  const [activeSession, setActiveSession] = useState<AgentSession | null>(null);
  const [activeTask, setActiveTask] = useState<AgentTask | null>(null);
  const [steps, setSteps] = useState<AgentStepEvent[]>([]);
  const [streamMode, setStreamMode] = useState<"idle" | "sse" | "polling" | "closed">("idle");

  const [prompt, setPrompt] = useState("");
  const [specialistHint, setSpecialistHint] = useState<AgentSpecialist>("supervisor");
  const [newRepoId, setNewRepoId] = useState("");
  const [newEnv, setNewEnv] = useState("development");
  const [newTitle, setNewTitle] = useState("");

  const [loadingSessions, setLoadingSessions] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const unsubRef = useRef<(() => void) | null>(null);

  const attachTaskStream = useCallback((task: AgentTask) => {
    if (unsubRef.current) {
      unsubRef.current();
      unsubRef.current = null;
    }

    const existingSteps = Array.isArray(task.steps_log) ? task.steps_log : [];
    setSteps(existingSteps);
    setActiveTask(task);

    if (
      task.status === "completed" ||
      task.status === "failed" ||
      task.status === "cancelled"
    ) {
      setStreamMode("closed");
      return;
    }

    const maxSeq = existingSteps.reduce((m, s) => Math.max(m, s.seq), 0);
    unsubRef.current = subscribeToAgentTask(task.id, maxSeq, {
      onModeChange: (mode) => setStreamMode(mode),
      onStep: (step) => {
        setSteps((prev) => {
          if (prev.some((s) => s.seq === step.seq)) return prev;
          return [...prev, step].sort((a, b) => a.seq - b.seq);
        });
      },
      onTaskUpdate: async (partial) => {
        setActiveTask((prev) => (prev ? { ...prev, ...partial } : prev));
        if (partial.status === "awaiting_approval" || partial.status === "completed") {
          try {
            const refreshed = await getAgentTask(task.id);
            setActiveTask(refreshed);
            if (Array.isArray(refreshed.steps_log) && refreshed.steps_log.length > 0) {
              setSteps(refreshed.steps_log);
            }
          } catch {
            // Keep existing optimistic state
          }
        }
      },
      onError: (msg) => setError(msg),
    });
  }, []);

  const selectSession = useCallback(
    async (sessionId: string) => {
      setError(null);
      try {
        const detail = await getAgentSession(sessionId);
        setActiveSession(detail);
        const tasks = detail.tasks ?? [];
        if (tasks.length > 0) {
          const latest = tasks[tasks.length - 1];
          const fullTask = await getAgentTask(latest.id).catch(() => latest);
          attachTaskStream(fullTask);
        } else {
          setActiveTask(null);
          setSteps([]);
          setStreamMode("idle");
        }
      } catch (err) {
        setError(getErrorMessage(err));
      }
    },
    [attachTaskStream]
  );

  const loadSessions = useCallback(async () => {
    setLoadingSessions(true);
    setError(null);
    try {
      const [sessList, repoList] = await Promise.all([
        listAgentSessions(),
        listRepositories({ limit: 50 }).catch(() => ({
          items: [],
          total: 0,
          limit: 50,
          offset: 0,
        })),
      ]);
      setSessions(sessList);
      setRepos(repoList.items);
      if (sessList.length > 0) {
        await selectSession(sessList[0].id);
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoadingSessions(false);
    }
  }, [selectSession]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void loadSessions();
    }, 0);
    return () => {
      clearTimeout(timer);
      if (unsubRef.current) {
        unsubRef.current();
      }
    };
  }, [loadSessions]);

  const handleCreateSession = async () => {
    setError(null);
    try {
      const created = await createAgentSession({
        title: newTitle.trim() || "Release Engineering Session",
        context_scope: {
          repository_id: newRepoId || undefined,
          environment: newEnv || undefined,
        },
      });
      setNewTitle("");
      setSessions((prev) => [created, ...prev]);
      await selectSession(created.id);
    } catch (err) {
      setError(getErrorMessage(err));
    }
  };

  const handleSendMessage = async (e?: React.FormEvent) => {
    e?.preventDefault();
    const content = prompt.trim();
    if (!content) return;

    setSending(true);
    setError(null);
    try {
      let targetSession = activeSession;
      if (!targetSession) {
        targetSession = await createAgentSession({
          title: content.slice(0, 60),
          context_scope: {
            repository_id: newRepoId || undefined,
            environment: newEnv || undefined,
          },
        });
        setSessions((prev) => [targetSession!, ...prev]);
        setActiveSession(targetSession);
      }

      const dispatch = await sendAgentMessage(targetSession.id, {
        content,
        specialist_hint: specialistHint,
      });
      setPrompt("");
      const taskObj = await getAgentTask(dispatch.task_id).catch(
        (): AgentTask => ({
          id: dispatch.task_id,
          session_id: targetSession.id,
          specialist: specialistHint,
          user_prompt: content,
          status: "queued",
          steps_log: [],
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        })
      );
      attachTaskStream(taskObj);
      const updatedSession = await getAgentSession(targetSession.id).catch(() => null);
      if (updatedSession) setActiveSession(updatedSession);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSending(false);
    }
  };

  const handleCancelTask = async () => {
    if (!activeTask) return;
    setError(null);
    try {
      const cancelled = await cancelAgentTask(activeTask.id);
      setActiveTask(cancelled);
      setStreamMode("closed");
    } catch (err) {
      setError(getErrorMessage(err));
    }
  };

  const handleApprovalUpdated = async (updated: Approval) => {
    if (!activeTask) return;
    setActiveTask((prev) =>
      prev
        ? {
            ...prev,
            pending_approvals: (prev.pending_approvals ?? []).map((a) =>
              a.id === updated.id ? updated : a
            ),
          }
        : prev
    );
    const refreshed = await getAgentTask(activeTask.id).catch(() => null);
    if (refreshed) {
      attachTaskStream(refreshed);
    }
  };

  const pendingApprovals = (activeTask?.pending_approvals ?? []).filter(
    (a) => a.status === "pending"
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="LangGraph AI Release Engineer"
        description="Supervisor coordinating pr_agent, flag_agent, and experiment_agent with manual Gemini function calling (AFC disabled) & Category B/C interrupts."
        badge={
          <span className="rounded-full border border-cyan-500/30 bg-cyan-500/10 px-2.5 py-0.5 font-mono text-[11px] text-cyan-300">
            Stream: {streamMode}
          </span>
        }
      />

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <div className="grid gap-6 lg:grid-cols-12">
        {/* Session list & context scope */}
        <div className="space-y-4 lg:col-span-4 xl:col-span-3">
          <SectionCard title="New Session Scope">
            <div className="space-y-2.5">
              <input
                type="text"
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                placeholder="Session title (optional)"
                className="w-full rounded-lg border border-slate-700 bg-[#090c12] px-3 py-1.5 text-xs text-slate-100 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
              />
              <select
                value={newRepoId}
                onChange={(e) => setNewRepoId(e.target.value)}
                className="w-full rounded-lg border border-slate-700 bg-[#090c12] px-3 py-1.5 text-xs text-slate-200 focus:border-indigo-500 focus:outline-none"
              >
                <option value="">All connected repositories</option>
                {repos.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.full_name}
                  </option>
                ))}
              </select>
              <select
                value={newEnv}
                onChange={(e) => setNewEnv(e.target.value)}
                className="w-full rounded-lg border border-slate-700 bg-[#090c12] px-3 py-1.5 text-xs text-slate-200 focus:border-indigo-500 focus:outline-none"
              >
                <option value="development">Scope: development</option>
                <option value="staging">Scope: staging</option>
                <option value="production">Scope: production (Category C)</option>
              </select>
              <button
                type="button"
                onClick={handleCreateSession}
                className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-400"
              >
                <Plus className="h-3.5 w-3.5" />
                Start New Session
              </button>
            </div>
          </SectionCard>

          <SectionCard title="Sessions" subtitle={`${sessions.length} threads`}>
            {loadingSessions ? (
              <LoadingSkeleton rows={3} />
            ) : sessions.length === 0 ? (
              <p className="text-xs text-slate-400">
                No agent sessions yet. Send a prompt to create one automatically.
              </p>
            ) : (
              <div className="space-y-1.5 max-h-[420px] overflow-y-auto">
                {sessions.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => void selectSession(s.id)}
                    className={cn(
                      "w-full rounded-lg border px-3 py-2.5 text-left transition-colors",
                      activeSession?.id === s.id
                        ? "border-indigo-500/40 bg-indigo-500/15"
                        : "border-slate-800/80 bg-[#0d1119] hover:border-slate-700"
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-xs font-semibold text-slate-100">
                        {s.title}
                      </span>
                      <StatusBadge status={s.status} />
                    </div>
                    <div className="mt-1 flex items-center justify-between text-[10px] text-slate-400">
                      <span className="font-mono">{s.active_specialist}</span>
                      <span className="tabular-nums">{formatTimestamp(s.updated_at)}</span>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </SectionCard>
        </div>

        {/* Main Workspace: Quick templates, Command Composer, Live Task Timeline & Inline Approval */}
        <div className="space-y-4 lg:col-span-8 xl:col-span-9">
          <SectionCard
            title={activeSession ? activeSession.title : "AI Release Engineer Console"}
            subtitle={
              activeSession
                ? `Thread ${activeSession.thread_id} • Specialist: ${activeSession.active_specialist}`
                : "Select a template or write an operational instruction"
            }
            actions={
              activeTask &&
              (activeTask.status === "running" ||
                activeTask.status === "queued" ||
                activeTask.status === "awaiting_approval") ? (
                <button
                  type="button"
                  onClick={handleCancelTask}
                  className="inline-flex items-center gap-1 rounded-lg border border-rose-500/40 bg-rose-500/10 px-2.5 py-1.5 text-xs font-medium text-rose-200 hover:bg-rose-500/20"
                >
                  <Square className="h-3 w-3" />
                  Cancel Task
                </button>
              ) : null
            }
          >
            {/* Quick templates */}
            <div className="mb-3 flex flex-wrap gap-2">
              {QUICK_TEMPLATES.map((tpl) => (
                <button
                  key={tpl.label}
                  type="button"
                  onClick={() => {
                    setPrompt(tpl.prompt);
                    setSpecialistHint(tpl.specialist);
                  }}
                  className="rounded-lg border border-slate-800 bg-[#0d1119] px-2.5 py-1.5 text-xs text-slate-300 hover:border-indigo-500/40 hover:text-indigo-200"
                >
                  {tpl.label}
                </button>
              ))}
            </div>

            <form onSubmit={handleSendMessage} className="space-y-3">
              <textarea
                rows={3}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Ask the supervisor or a specialist to analyze a PR, wrap risky code behind Flagsmith, stage a rollout, or run a Bayesian evaluation..."
                className="w-full rounded-xl border border-slate-700 bg-[#090c12] p-3 text-sm text-slate-100 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
              />
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2 text-xs">
                  <span className="text-slate-400">Specialist Router:</span>
                  <select
                    value={specialistHint}
                    onChange={(e) =>
                      setSpecialistHint(e.target.value as AgentSpecialist)
                    }
                    className="rounded-lg border border-slate-700 bg-[#090c12] px-2.5 py-1.5 font-mono text-xs text-slate-200 focus:border-indigo-500 focus:outline-none"
                  >
                    <option value="supervisor">supervisor (auto-route)</option>
                    <option value="pr_agent">pr_agent (Module 1)</option>
                    <option value="flag_agent">flag_agent (Module 2)</option>
                    <option value="experiment_agent">experiment_agent (Module 3)</option>
                  </select>
                </div>

                <button
                  type="submit"
                  disabled={sending || !prompt.trim()}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
                >
                  <Send className="h-3.5 w-3.5" />
                  {sending ? "Dispatching..." : "Dispatch Task"}
                </button>
              </div>
            </form>
          </SectionCard>

          {/* Inline Approval Gate when paused */}
          {pendingApprovals.length > 0 && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-amber-300">
                <Shield className="h-4 w-4" />
                <span>LangGraph Interrupted — Single-Use Policy Gate Required</span>
              </div>
              {pendingApprovals.map((app) => (
                <ApprovalActionCard
                  key={app.id}
                  approval={app}
                  highlighted
                  onUpdated={(u) => void handleApprovalUpdated(u)}
                />
              ))}
            </div>
          )}

          {/* Structured Task Execution Timeline */}
          <SectionCard
            title="Structured Task Execution Timeline"
            subtitle={
              activeTask
                ? `Task ${activeTask.id} • Specialist: ${activeTask.specialist}`
                : "Events stream in real time via SSE with ?after_seq= polling fallback"
            }
            actions={
              activeTask ? (
                <div className="flex items-center gap-2">
                  {activeTask.token_usage?.total_tokens ? (
                    <span className="font-mono text-[11px] text-slate-400 tabular-nums">
                      {activeTask.token_usage.total_tokens} tokens
                    </span>
                  ) : null}
                  <StatusBadge status={activeTask.status} />
                </div>
              ) : undefined
            }
          >
            {!activeTask ? (
              <EmptyState
                icon={<Terminal className="h-5 w-5" />}
                title="No active task timeline"
                description="Dispatch a workflow instruction above to trace every Gemini tool proposal, Policy Gate evaluation, and verified execution."
              />
            ) : (
              <div className="space-y-3">
                <div className="rounded-lg border border-slate-800 bg-[#0b0f17] px-3.5 py-2.5 text-xs">
                  <span className="font-semibold text-slate-400">Operator Prompt: </span>
                  <span className="text-slate-100">{activeTask.user_prompt}</span>
                </div>

                {steps.length === 0 ? (
                  <LoadingSkeleton rows={2} />
                ) : (
                  <div className="space-y-2">
                    {steps.map((step) => (
                      <StepEventCard key={step.seq} step={step} />
                    ))}
                  </div>
                )}

                {activeTask.final_response && (
                  <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-4 space-y-1.5">
                    <div className="text-xs font-semibold uppercase tracking-wider text-emerald-300">
                      Final Specialist Summary
                    </div>
                    <div className="whitespace-pre-wrap text-sm leading-relaxed text-slate-100">
                      {activeTask.final_response}
                    </div>
                  </div>
                )}

                {activeTask.error_message && (
                  <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-200">
                    <span className="font-semibold">Task Failure: </span>
                    {activeTask.error_message}
                  </div>
                )}
              </div>
            )}
          </SectionCard>

          {/* Session Message History */}
          {activeSession && activeSession.message_history?.length > 0 && (
            <SectionCard
              title="Session Conversation Log"
              subtitle="Sanitized turn history stored in agent_sessions"
            >
              <div className="space-y-2.5 max-h-80 overflow-y-auto">
                {activeSession.message_history.map((msg, idx) => (
                  <div
                    key={idx}
                    className={cn(
                      "rounded-lg border p-3 text-xs",
                      msg.role === "user"
                        ? "border-indigo-500/30 bg-indigo-500/10 text-slate-100"
                        : "border-slate-800 bg-[#0d1119] text-slate-200"
                    )}
                  >
                    <div className="mb-1 flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-400">
                      <span>{msg.role}</span>
                      {msg.timestamp && <span>{formatTimestamp(msg.timestamp)}</span>}
                    </div>
                    <div className="whitespace-pre-wrap leading-relaxed">{msg.content}</div>
                  </div>
                ))}
              </div>
            </SectionCard>
          )}
        </div>
      </div>
    </div>
  );
}
