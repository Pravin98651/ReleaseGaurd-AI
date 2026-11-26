"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
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
  ExternalLink,
  FileText,
  Flag,
  FlaskConical,
  FolderGit2,
  GitPullRequest,
  HeartPulse,
  Play,
  RefreshCw,
  Rocket,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Square,
  Terminal,
  Wrench,
} from "lucide-react";
import {
  cancelAgentTask,
  createAgentSession,
  createRelease,
  DEMO_APP_URL,
  getAgentTask,
  getDashboardOverview,
  getErrorMessage,
  getSystemHealth,
  listApprovals,
  listPullRequests,
  listRepositories,
  sendAgentMessage,
} from "@/lib/api";
import { subscribeToAgentTask } from "@/lib/sse";
import type {
  AgentStepEvent,
  AgentTask,
  Approval,
  DashboardOverview,
  MetricTrend,
  PullRequest,
  ReleaseDetail,
  Repository,
  SystemHealth,
  TimeRangePreset,
} from "@/types/releaseguard";
import { formatTimestamp } from "@/lib/utils";
import {
  EmptyState,
  ErrorBanner,
  LoadingSkeleton,
  MetricCard,
  Modal,
  PageHeader,
  SectionCard,
} from "@/components/ui/StatePrimitives";
import { DashboardChartGrid } from "@/components/DashboardCharts";
import { ApprovalActionCard } from "@/components/ApprovalActionCard";
import { PolicyGateBadge } from "@/components/PolicyGateBadge";
import { EnvironmentBadge, StatusBadge } from "@/components/StatusBadges";

const NOVASHOP_DEMO_OBJECTIVE =
  "Analyze the selected PR for checkout risk, verify Flagsmith flag coverage in development only, stage a 25% canary rollout with payment_failed guardrail monitoring, and prepare the release readiness report.";

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

function LiveStepRow({ step }: { step: AgentStepEvent }) {
  const p = step.payload ?? {};
  const labelMap: Record<string, { label: string; tone: string }> = {
    "task.started": { label: "Started", tone: "text-indigo-300 border-indigo-500/30 bg-indigo-500/10" },
    "agent.thinking": { label: "Routing", tone: "text-sky-300 border-sky-500/30 bg-sky-500/10" },
    "agent.tool_proposed": { label: "Tool Proposed", tone: "text-cyan-300 border-cyan-500/30 bg-cyan-500/10" },
    "policy.evaluated": { label: "Policy Gate", tone: "text-amber-300 border-amber-500/30 bg-amber-500/10" },
    "approval.required": { label: "Approval Required", tone: "text-amber-200 border-amber-500/40 bg-amber-500/15" },
    "tool.executed": { label: "Tool Executed", tone: "text-emerald-300 border-emerald-500/30 bg-emerald-500/10" },
    "agent.completed": { label: "Completed", tone: "text-emerald-200 border-emerald-500/40 bg-emerald-500/15" },
    "task.failed": { label: "Failed", tone: "text-rose-200 border-rose-500/40 bg-rose-500/15" },
  };
  const badge = labelMap[step.event_type] ?? {
    label: step.event_type,
    tone: "text-slate-300 border-slate-700 bg-slate-800",
  };

  return (
    <div className="flex items-start justify-between gap-3 rounded-lg border border-slate-800/80 bg-[#0d1119] px-3 py-2 text-xs">
      <div className="min-w-0 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[10px] text-slate-500">#{step.seq}</span>
          <span className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold ${badge.tone}`}>
            {badge.label}
          </span>
          {typeof p.specialist === "string" && (
            <span className="font-mono text-[11px] text-indigo-300">{p.specialist}</span>
          )}
          {typeof p.tool_name === "string" && (
            <span className="inline-flex items-center gap-1 font-mono text-[11px] text-cyan-300">
              <Wrench className="h-3 w-3" />
              {p.tool_name}
            </span>
          )}
          {typeof p.category === "string" && (
            <PolicyGateBadge category={p.category} />
          )}
        </div>
        {typeof p.summary === "string" && (
          <p className="text-[11px] text-slate-300">{p.summary}</p>
        )}
        {typeof p.reason === "string" && (
          <p className="text-[11px] text-slate-400">{p.reason}</p>
        )}
        {typeof p.title === "string" && (
          <p className="text-[11px] font-medium text-amber-200">{p.title}</p>
        )}
        {typeof p.error === "string" && (
          <p className="text-[11px] text-rose-300">{p.error}</p>
        )}
      </div>
      <span className="shrink-0 font-mono text-[10px] text-slate-500 tabular-nums">
        {formatTimestamp(step.timestamp)}
      </span>
    </div>
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

  // Central Release Workspace state
  const [repos, setRepos] = useState<Repository[]>([]);
  const [selectedRepoId, setSelectedRepoId] = useState("");
  const [pullRequests, setPullRequests] = useState<PullRequest[]>([]);
  const [loadingPrs, setLoadingPrs] = useState(false);
  const [selectedPrId, setSelectedPrId] = useState("");
  const [targetEnv, setTargetEnv] = useState<"development" | "staging" | "production">("development");
  const [objective, setObjective] = useState("");
  const [launching, setLaunching] = useState(false);
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [activeRelease, setActiveRelease] = useState<ReleaseDetail | null>(null);
  const [activeTask, setActiveTask] = useState<AgentTask | null>(null);
  const [liveSteps, setLiveSteps] = useState<AgentStepEvent[]>([]);
  const [streamMode, setStreamMode] = useState<"idle" | "sse" | "polling" | "closed">("idle");

  // Confirmation dialog state for sensitive actions
  const [confirmAction, setConfirmAction] = useState<{
    title: string;
    subtitle: string;
    description: string;
    confirmLabel: string;
    tone: "amber" | "rose" | "indigo";
    onConfirm: () => Promise<void> | void;
  } | null>(null);

  const unsubRef = useRef<(() => void) | null>(null);

  const attachTaskStream = useCallback((task: AgentTask) => {
    if (unsubRef.current) {
      unsubRef.current();
      unsubRef.current = null;
    }

    const existingSteps = Array.isArray(task.steps_log) ? task.steps_log : [];
    setLiveSteps(existingSteps);
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
        setLiveSteps((prev) => {
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
              setLiveSteps(refreshed.steps_log);
            }
          } catch {
            // Keep existing state if refresh fails
          }
        }
      },
      onError: (msg) => setWorkspaceError(msg),
    });
  }, []);

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
      const [healthRes, overviewRes, approvalsRes, reposRes] = await Promise.allSettled([
        getSystemHealth(),
        getDashboardOverview(filters),
        listApprovals({ status: "pending", limit: 5 }),
        listRepositories({ limit: 50 }),
      ]);

      const fetchErrors: string[] = [];

      if (healthRes.status === "fulfilled") {
        setHealth(healthRes.value);
      } else {
        fetchErrors.push(`System health: ${getErrorMessage(healthRes.reason)}`);
      }

      if (overviewRes.status === "fulfilled") {
        setOverview(overviewRes.value);
      } else {
        fetchErrors.push(`Dashboard overview: ${getErrorMessage(overviewRes.reason)}`);
      }

      if (approvalsRes.status === "fulfilled") {
        setPendingApprovals(approvalsRes.value.items);
      } else {
        setPendingApprovals([]);
        fetchErrors.push(`Pending approvals: ${getErrorMessage(approvalsRes.reason)}`);
      }

      if (reposRes.status === "fulfilled") {
        const items = reposRes.value.items;
        setRepos(items);
        setSelectedRepoId((prev) => {
          if (prev && items.some((r) => r.id === prev)) return prev;
          return items[0]?.id ?? "";
        });
      } else {
        setRepos([]);
        setSelectedRepoId("");
        fetchErrors.push(`Repositories: ${getErrorMessage(reposRes.reason)}`);
      }

      if (fetchErrors.length > 0) {
        setError(fetchErrors.join(" · "));
      }
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
      if (unsubRef.current) {
        unsubRef.current();
      }
    };
  }, [loadData]);

  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      if (cancelled) return;
      if (!selectedRepoId) {
        setPullRequests([]);
        setSelectedPrId("");
        setLoadingPrs(false);
        return;
      }
      setLoadingPrs(true);
      void listPullRequests({ repository_id: selectedRepoId, limit: 50 })
        .then((res) => {
          if (cancelled) return;
          setPullRequests(res.items);
          const openPr = res.items.find((p) => p.state === "open") ?? res.items[0];
          setSelectedPrId(openPr?.id ?? "");
        })
        .catch((err) => {
          if (!cancelled) {
            setPullRequests([]);
            setSelectedPrId("");
            setWorkspaceError(`Failed to load pull requests: ${getErrorMessage(err)}`);
          }
        })
        .finally(() => {
          if (!cancelled) setLoadingPrs(false);
        });
    }, 0);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [selectedRepoId]);

  const executeWorkflowLaunch = async () => {
    const trimmedObjective = objective.trim();
    if (!trimmedObjective) {
      setWorkspaceError("Enter a natural-language release objective before starting.");
      return;
    }
    setLaunching(true);
    setWorkspaceError(null);
    try {
      const selectedRepo = repos.find((r) => r.id === selectedRepoId) ?? null;
      const selectedPr = pullRequests.find((p) => p.id === selectedPrId) ?? null;

      // Best-effort create a tracked release record so the user gets a direct Final Release Report link
      if (selectedRepo && selectedPr) {
        try {
          const release = await createRelease({
            title: `AI Release: ${selectedPr.title}`.slice(0, 120),
            description: trimmedObjective,
            target_environment: targetEnv,
            repository_id: selectedRepo.id,
            pull_request_id: selectedPr.id,
            target_rollout_percentage: 25,
            ai_assist: true,
          });
          setActiveRelease(release);
        } catch {
          // Proceed with AI session even if release record creation is skipped or duplicated
        }
      }

      const session = await createAgentSession({
        title: selectedPr
          ? `PR #${selectedPr.github_pr_number}: ${selectedPr.title}`.slice(0, 80)
          : trimmedObjective.slice(0, 80),
        context_scope: {
          repository_id: selectedRepoId || undefined,
          pull_request_id: selectedPrId || undefined,
          environment: targetEnv,
        },
      });

      const promptWithContext = selectedPr
        ? `[Repository: ${selectedRepo?.full_name ?? selectedRepoId} | PR #${selectedPr.github_pr_number}: ${selectedPr.title} | Env: ${targetEnv}] ${trimmedObjective}`
        : `[Env: ${targetEnv}] ${trimmedObjective}`;

      const dispatch = await sendAgentMessage(session.id, {
        content: promptWithContext,
        specialist_hint: "supervisor",
      });

      const taskObj = await getAgentTask(dispatch.task_id).catch(
        (): AgentTask => ({
          id: dispatch.task_id,
          session_id: session.id,
          specialist: "supervisor",
          user_prompt: promptWithContext,
          status: "queued",
          steps_log: [],
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        })
      );
      attachTaskStream(taskObj);
    } catch (err) {
      setWorkspaceError(getErrorMessage(err));
    } finally {
      setLaunching(false);
    }
  };

  const handleStartWorkflow = (e: React.FormEvent) => {
    e.preventDefault();
    if (!objective.trim()) {
      setWorkspaceError("Enter a natural-language release objective before starting.");
      return;
    }
    if (targetEnv !== "development") {
      setConfirmAction({
        title: `Confirm ${targetEnv.toUpperCase()} AI Release Workflow`,
        subtitle: "Sensitive environment target",
        description: `You are about to launch an AI-powered release workflow targeting "${targetEnv}". Category B/C mutations will still require explicit human approval, and no PRs will be auto-merged. Continue?`,
        confirmLabel: `Start in ${targetEnv}`,
        tone: targetEnv === "production" ? "rose" : "amber",
        onConfirm: async () => {
          setConfirmAction(null);
          await executeWorkflowLaunch();
        },
      });
      return;
    }
    void executeWorkflowLaunch();
  };

  const handleCancelActiveTask = () => {
    if (!activeTask) return;
    setConfirmAction({
      title: "Cancel Active AI Release Task",
      subtitle: `Task ${activeTask.id}`,
      description:
        "Cancelling stops the LangGraph supervisor stream immediately. Any already-executed Category A reads remain in the audit log.",
      confirmLabel: "Cancel Task",
      tone: "rose",
      onConfirm: async () => {
        setConfirmAction(null);
        try {
          const cancelled = await cancelAgentTask(activeTask.id);
          setActiveTask(cancelled);
          setStreamMode("closed");
        } catch (err) {
          setWorkspaceError(getErrorMessage(err));
        }
      },
    });
  };

  const applyNovaShopDemoScenario = () => {
    const apply = () => {
      const novaRepo =
        repos.find((r) => r.full_name.toLowerCase().includes("novashop")) ?? repos[0];
      if (novaRepo) {
        setSelectedRepoId(novaRepo.id);
      }
      setTargetEnv("development");
      setObjective(NOVASHOP_DEMO_OBJECTIVE);
      setWorkspaceError(null);
    };

    if (objective.trim() && objective.trim() !== NOVASHOP_DEMO_OBJECTIVE) {
      setConfirmAction({
        title: "Load NovaShop Demo Scenario",
        subtitle: "Development environment only · Synthetic data",
        description:
          "This will replace your current release objective with the NovaShop development-only demo scenario and lock target environment to development. Production is never mutated.",
        confirmLabel: "Load Demo Scenario",
        tone: "indigo",
        onConfirm: () => {
          setConfirmAction(null);
          apply();
        },
      });
      return;
    }
    apply();
  };

  const handleTaskApprovalUpdated = async (updated: Approval) => {
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
    void loadData();
  };

  const m = overview?.metrics;
  const recentActivity = overview?.recent_activity ?? [];
  const pendingActions = overview?.pending_actions ?? [];
  const taskPendingApprovals = (activeTask?.pending_approvals ?? []).filter(
    (a) => a.status === "pending"
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Release Control Plane"
        description="Central Release Workspace for AI-powered release orchestration, live SSE execution, NovaShop Demo Mode, and operational telemetry."
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

      {/* Hero Row: Central Release Workspace + NovaShop Demo Mode */}
      <div className="grid gap-6 xl:grid-cols-12">
        <SectionCard
          className="xl:col-span-8"
          title="Start an AI-powered release"
          subtitle="Central Release Workspace — select a repository and PR, define your objective, and observe live LangGraph SSE execution."
          actions={
            <div className="flex items-center gap-2">
              <span className="rounded-full border border-cyan-500/30 bg-cyan-500/10 px-2.5 py-0.5 font-mono text-[11px] text-cyan-300">
                Stream: {streamMode}
              </span>
              <Link
                href={activeRelease ? `/releases/${activeRelease.release.id}` : "/releases"}
                className="inline-flex items-center gap-1 rounded-lg border border-indigo-500/40 bg-indigo-500/15 px-2.5 py-1 text-xs font-semibold text-indigo-200 hover:bg-indigo-500/25"
              >
                <FileText className="h-3.5 w-3.5" />
                Final Release Report
              </Link>
            </div>
          }
        >
          <form onSubmit={handleStartWorkflow} className="space-y-4">
            <ErrorBanner error={workspaceError} onDismiss={() => setWorkspaceError(null)} />

            <div className="grid gap-3 sm:grid-cols-3">
              <div>
                <label
                  htmlFor="workspace-repo-select"
                  className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-slate-400"
                >
                  Repository
                </label>
                <select
                  id="workspace-repo-select"
                  value={selectedRepoId}
                  onChange={(e) => setSelectedRepoId(e.target.value)}
                  className="w-full rounded-lg border border-slate-700 bg-[#0d1119] px-3 py-2 text-xs text-slate-100 focus:border-indigo-500 focus:outline-none"
                >
                  {repos.length === 0 ? (
                    <option value="">No connected repositories</option>
                  ) : (
                    repos.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.full_name}
                      </option>
                    ))
                  )}
                </select>
              </div>

              <div>
                <label
                  htmlFor="workspace-pr-select"
                  className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-slate-400"
                >
                  Pull Request
                </label>
                <select
                  id="workspace-pr-select"
                  value={selectedPrId}
                  onChange={(e) => setSelectedPrId(e.target.value)}
                  disabled={loadingPrs || pullRequests.length === 0}
                  className="w-full rounded-lg border border-slate-700 bg-[#0d1119] px-3 py-2 text-xs text-slate-100 focus:border-indigo-500 focus:outline-none disabled:opacity-60"
                >
                  {loadingPrs ? (
                    <option value="">Loading PRs...</option>
                  ) : pullRequests.length === 0 ? (
                    <option value="">No synced PRs for repo</option>
                  ) : (
                    pullRequests.map((pr) => (
                      <option key={pr.id} value={pr.id}>
                        #{pr.github_pr_number} · {pr.title} ({pr.state})
                      </option>
                    ))
                  )}
                </select>
              </div>

              <div>
                <label
                  htmlFor="workspace-env-select"
                  className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-slate-400"
                >
                  Target Environment
                </label>
                <select
                  id="workspace-env-select"
                  value={targetEnv}
                  onChange={(e) =>
                    setTargetEnv(e.target.value as "development" | "staging" | "production")
                  }
                  className="w-full rounded-lg border border-slate-700 bg-[#0d1119] px-3 py-2 text-xs text-slate-100 focus:border-indigo-500 focus:outline-none"
                >
                  <option value="development">development (Safe Sandbox)</option>
                  <option value="staging">staging (Confirmation Required)</option>
                  <option value="production">production (Confirmation Required)</option>
                </select>
              </div>
            </div>

            <div>
              <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
                <label
                  htmlFor="workspace-objective"
                  className="text-[11px] font-semibold uppercase tracking-wider text-slate-400"
                >
                  Natural-Language Release Objective
                </label>
                <button
                  type="button"
                  onClick={() => setObjective(NOVASHOP_DEMO_OBJECTIVE)}
                  className="text-[11px] font-medium text-indigo-300 hover:text-indigo-200"
                >
                  Use NovaShop checkout template
                </button>
              </div>
              <textarea
                id="workspace-objective"
                rows={3}
                value={objective}
                onChange={(e) => setObjective(e.target.value)}
                placeholder="Describe what the AI release supervisor should analyze, flag, experiment, or stage..."
                className="w-full rounded-lg border border-slate-700 bg-[#0d1119] px-3 py-2 text-xs leading-relaxed text-slate-100 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
              />
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                <ShieldCheck className="h-3.5 w-3.5 text-emerald-400" />
                <span>PolicyGate enforced · Category B/C mutations require human sign-off</span>
              </div>
              <div className="flex items-center gap-2">
                {activeTask &&
                  (activeTask.status === "running" ||
                    activeTask.status === "queued" ||
                    activeTask.status === "awaiting_approval") && (
                    <button
                      type="button"
                      onClick={handleCancelActiveTask}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs font-semibold text-rose-200 hover:bg-rose-500/20"
                    >
                      <Square className="h-3.5 w-3.5" />
                      Cancel Task
                    </button>
                  )}
                <button
                  type="submit"
                  disabled={launching}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
                >
                  <Play className="h-3.5 w-3.5" />
                  {launching ? "Starting Release..." : "Start AI-Powered Release"}
                </button>
              </div>
            </div>
          </form>

          {/* Live SSE Execution & Inline Approvals */}
          {activeTask && (
            <div className="mt-5 space-y-3 border-t border-slate-800/80 pt-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <Terminal className="h-4 w-4 text-indigo-400" />
                  <span className="text-xs font-semibold text-slate-100">
                    Live Execution Trace
                  </span>
                  <StatusBadge status={activeTask.status} />
                  <span className="font-mono text-[11px] text-slate-400">
                    Task {activeTask.id.slice(0, 8)}
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Link
                    href="/command-center"
                    className="rounded-md border border-slate-700 bg-slate-900 px-2.5 py-1 text-[11px] font-medium text-indigo-300 hover:bg-slate-800"
                  >
                    Open AI Command Center →
                  </Link>
                  <Link
                    href={activeRelease ? `/releases/${activeRelease.release.id}` : "/releases"}
                    className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2.5 py-1 text-[11px] font-semibold text-emerald-200 hover:bg-emerald-500/20"
                  >
                    Inspect Final Release Report →
                  </Link>
                </div>
              </div>

              {taskPendingApprovals.length > 0 && (
                <div className="space-y-2.5 rounded-xl border border-amber-500/40 bg-amber-500/5 p-3">
                  <div className="text-xs font-semibold text-amber-200">
                    Gated Action Awaiting Your Approval
                  </div>
                  {taskPendingApprovals.map((app) => (
                    <ApprovalActionCard
                      key={app.id}
                      approval={app}
                      compact
                      highlighted
                      onUpdated={handleTaskApprovalUpdated}
                    />
                  ))}
                </div>
              )}

              {liveSteps.length === 0 ? (
                <div className="rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-4 text-center text-xs text-slate-400">
                  Waiting for backend SSE events (no progress fabricated)...
                </div>
              ) : (
                <div className="max-h-64 space-y-2 overflow-y-auto pr-1">
                  {liveSteps.map((step) => (
                    <LiveStepRow key={`${step.seq}-${step.event_type}`} step={step} />
                  ))}
                </div>
              )}

              {activeTask.final_response && (
                <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-xs text-emerald-100">
                  <div className="mb-1 font-semibold text-emerald-200">
                    Supervisor Final Response
                  </div>
                  <p className="whitespace-pre-wrap leading-relaxed">
                    {activeTask.final_response}
                  </p>
                </div>
              )}
            </div>
          )}
        </SectionCard>

        {/* Dedicated NovaShop Demo Mode Panel */}
        <SectionCard
          className="xl:col-span-4"
          title="Demo Mode · NovaShop"
          subtitle="Safe interactive sandbox for the 5-Minute Demo Path"
          actions={
            <span className="inline-flex items-center gap-1 rounded-full border border-amber-500/40 bg-amber-500/15 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-amber-200">
              <Sparkles className="h-3 w-3" />
              Synthetic Data
            </span>
          }
        >
          <div className="space-y-4 text-xs">
            <div className="flex flex-wrap gap-1.5">
              <span className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 font-mono text-[10px] font-semibold text-emerald-300">
                ENV: development only
              </span>
              <span className="rounded-md border border-rose-500/30 bg-rose-500/10 px-2 py-0.5 font-mono text-[10px] font-semibold text-rose-200">
                NEVER MUTATES PRODUCTION
              </span>
            </div>

            <p className="leading-relaxed text-slate-300">
              NovaShop is a sandboxed e-commerce demo storefront wired to Flagsmith and
              ReleaseGuard telemetry. All simulator cohorts (<code className="font-mono text-amber-300">sim_*</code>)
              are clearly badged as synthetic and isolated from production environments.
            </p>

            <div className="space-y-2 rounded-lg border border-slate-800 bg-[#0d1119] p-3">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                Telemetry Attribution
              </div>
              <div className="flex items-center justify-between">
                <span className="text-slate-300">Storefront App Telemetry</span>
                <span className="font-mono font-semibold text-emerald-300 tabular-nums">
                  {overview?.charts?.telemetry_totals?.application_telemetry_events ?? 0} events
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-slate-300">
                  Synthetic Simulator (<code className="font-mono text-amber-300">sim_*</code>)
                </span>
                <span className="font-mono font-semibold text-amber-300 tabular-nums">
                  {overview?.charts?.telemetry_totals?.simulator_events ?? 0} events
                </span>
              </div>
            </div>

            <div className="space-y-2">
              <button
                type="button"
                onClick={applyNovaShopDemoScenario}
                className="flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-500 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-400"
              >
                <Sparkles className="h-3.5 w-3.5" />
                Load NovaShop Demo Scenario
              </button>

              <a
                href={DEMO_APP_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="flex w-full items-center justify-between rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-medium text-slate-200 hover:bg-slate-800"
              >
                <span>Open NovaShop Storefront</span>
                <ExternalLink className="h-3.5 w-3.5 text-slate-400" />
              </a>

              <div className="grid grid-cols-2 gap-2 pt-1">
                <Link
                  href="/flagsmith"
                  className="flex items-center justify-center gap-1.5 rounded-lg border border-slate-800 bg-[#0d1119] px-2.5 py-2 text-[11px] font-medium text-slate-300 hover:border-slate-700 hover:text-slate-100"
                >
                  <Flag className="h-3.5 w-3.5 text-indigo-400" />
                  Flagsmith Hub
                </Link>
                <Link
                  href="/experiments"
                  className="flex items-center justify-center gap-1.5 rounded-lg border border-slate-800 bg-[#0d1119] px-2.5 py-2 text-[11px] font-medium text-slate-300 hover:border-slate-700 hover:text-slate-100"
                >
                  <FlaskConical className="h-3.5 w-3.5 text-cyan-400" />
                  Experiments
                </Link>
              </div>
            </div>
          </div>
        </SectionCard>
      </div>

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

      {/* Executive KPIs, Domain Cards, Charts & Schedules */}
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
                href="/command-center"
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

          {overview?.charts && <DashboardChartGrid charts={overview.charts} />}

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

      {/* Confirmation Dialog for Sensitive Actions */}
      <Modal
        open={Boolean(confirmAction)}
        onClose={() => setConfirmAction(null)}
        title={confirmAction?.title ?? "Confirm Action"}
        subtitle={confirmAction?.subtitle}
      >
        {confirmAction && (
          <div className="space-y-4">
            <p className="text-xs leading-relaxed text-slate-300">
              {confirmAction.description}
            </p>
            <div className="flex justify-end gap-2 border-t border-slate-800 pt-3">
              <button
                type="button"
                onClick={() => setConfirmAction(null)}
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void confirmAction.onConfirm()}
                className={`rounded-lg px-3.5 py-1.5 text-xs font-semibold ${
                  confirmAction.tone === "rose"
                    ? "bg-rose-500 text-white hover:bg-rose-400"
                    : confirmAction.tone === "amber"
                      ? "bg-amber-500 text-slate-950 hover:bg-amber-400"
                      : "bg-indigo-500 text-white hover:bg-indigo-400"
                }`}
              >
                {confirmAction.confirmLabel}
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
