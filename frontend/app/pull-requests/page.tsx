"use client";

import React, { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  Clock,
  ExternalLink,
  FileWarning,
  Flag,
  GitBranch,
  GitPullRequest,
  HelpCircle,
  Lightbulb,
  Lock,
  Play,
  RefreshCw,
  Rocket,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Wrench,
} from "lucide-react";
import {
  analyzePullRequest,
  createDraftPrFromCodeChange,
  createRelease,
  generatePrPatch,
  getErrorMessage,
  getPullRequest,
  getSystemHealth,
  listAgentExecutions,
  listApprovals,
  listPullRequests,
  listRepositories,
  syncRepoPullRequests,
  validateCodeChange,
} from "@/lib/api";
import type {
  AgentExecutionRecord,
  Approval,
  CodeChange,
  PrDiffSummary,
  PrRecommendationItem,
  PrUnknownItem,
  PullRequest,
  Repository,
  SystemHealth,
} from "@/types/releaseguard";
import { cn, formatTimestamp } from "@/lib/utils";
import {
  ApprovalQueuedBanner,
  EmptyState,
  ErrorBanner,
  LoadingSkeleton,
  PageHeader,
  SectionCard,
  SuccessBanner,
} from "@/components/ui/StatePrimitives";
import { RiskLevelBadge, StatusBadge } from "@/components/StatusBadges";
import { PolicyGateBadge } from "@/components/PolicyGateBadge";
import { CodeDiffViewer } from "@/components/CodeDiffViewer";

function parseDiffSummary(raw: PullRequest["diff_summary"]): PrDiffSummary | null {
  if (!raw || typeof raw === "string") return null;
  return raw as PrDiffSummary;
}

function normalizeRecommendation(
  item: PrRecommendationItem | string
): PrRecommendationItem {
  if (typeof item === "string") {
    return { title: item, action: item, priority: "medium" };
  }
  return item;
}

function normalizeUnknown(item: PrUnknownItem | string): PrUnknownItem {
  if (typeof item === "string") {
    return { title: "Unverified context", reason: item };
  }
  return item;
}

function PullRequestsInner() {
  const searchParams = useSearchParams();
  const initialRepoId = searchParams.get("repository_id") ?? "";

  const [repos, setRepos] = useState<Repository[]>([]);
  const [prs, setPrs] = useState<PullRequest[]>([]);
  const [selectedPr, setSelectedPr] = useState<PullRequest | null>(null);
  const [systemHealth, setSystemHealth] = useState<SystemHealth | null>(null);
  const [executions, setExecutions] = useState<AgentExecutionRecord[]>([]);
  const [pendingApprovals, setPendingApprovals] = useState<Approval[]>([]);

  const [repoFilter, setRepoFilter] = useState(initialRepoId);
  const [riskFilter, setRiskFilter] = useState("");
  const [customFlagKey, setCustomFlagKey] = useState("");
  const [customFlagType, setCustomFlagType] = useState<"STANDARD" | "MULTIVARIATE">(
    "STANDARD"
  );

  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [queuedApproval, setQueuedApproval] = useState<Approval | null>(null);

  const loadPrSideContext = useCallback(async (pr: PullRequest) => {
    try {
      const [execRes, approvalRes] = await Promise.allSettled([
        listAgentExecutions({ repository_id: pr.repository_id, limit: 30 }),
        listApprovals({ status: "pending", limit: 30 }),
      ]);
      if (execRes.status === "fulfilled") {
        const items = execRes.value.items ?? [];
        const prScoped = items.filter((e) => e.pull_request_id === pr.id);
        setExecutions(prScoped.length > 0 ? prScoped : items.slice(0, 8));
      } else {
        setExecutions([]);
      }
      if (approvalRes.status === "fulfilled") {
        const changeIds = new Set((pr.code_changes ?? []).map((c) => c.id));
        const related = (approvalRes.value.items ?? []).filter(
          (a) =>
            a.target_resource_id === pr.id ||
            changeIds.has(a.target_resource_id) ||
            a.action_type.includes("draft_pr") ||
            a.action_type.includes("flag")
        );
        setPendingApprovals(related);
      } else {
        setPendingApprovals([]);
      }
    } catch {
      // Side context is best-effort
    }
  }, []);

  const selectPrDetail = useCallback(
    async (prId: string) => {
      setError(null);
      try {
        const full = await getPullRequest(prId);
        setSelectedPr(full);
        void loadPrSideContext(full);
      } catch (err) {
        setError(getErrorMessage(err));
      }
    },
    [loadPrSideContext]
  );

  const loadPrs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [repoList, prList, healthRes] = await Promise.all([
        listRepositories({ limit: 50 }),
        listPullRequests({
          repository_id: repoFilter || undefined,
          risk_level: riskFilter || undefined,
          limit: 50,
        }),
        getSystemHealth().catch(() => null),
      ]);
      setRepos(repoList.items);
      setPrs(prList.items);
      if (healthRes) setSystemHealth(healthRes);
      if (prList.items.length > 0) {
        const currentStillExists =
          selectedPr && prList.items.some((p) => p.id === selectedPr.id);
        const targetId = currentStillExists ? selectedPr.id : prList.items[0].id;
        await selectPrDetail(targetId);
      } else {
        setSelectedPr(null);
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [repoFilter, riskFilter, selectPrDetail, selectedPr]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void loadPrs();
    }, 0);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoFilter, riskFilter]);

  const handleSyncPrs = async () => {
    const targetRepo = repoFilter || repos[0]?.id;
    if (!targetRepo) {
      setError("Connect a repository first before syncing pull requests.");
      return;
    }
    setBusyAction("sync");
    setError(null);
    setNotice(null);
    try {
      const res = await syncRepoPullRequests(targetRepo);
      setNotice(`Synced ${res.synced_count ?? res.items?.length ?? 0} pull requests.`);
      await loadPrs();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleAnalyze = async (pr: PullRequest) => {
    setBusyAction("analyze");
    setError(null);
    setNotice(null);
    try {
      const updated = await analyzePullRequest(pr.id);
      setSelectedPr((prev) =>
        prev && prev.id === updated.id
          ? {
              ...updated,
              code_changes: prev.code_changes ?? updated.code_changes,
              changed_files: prev.changed_files ?? updated.changed_files,
            }
          : updated
      );
      setPrs((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
      await selectPrDetail(updated.id);
      setNotice(
        `Completed prompt-injection-hardened AI & 7-Dim Risk Analysis for PR #${pr.github_pr_number}.`
      );
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleCreateRelease = async (pr: PullRequest) => {
    setBusyAction("create-release");
    setError(null);
    try {
      const detail = await createRelease({
        pull_request_id: pr.id,
        repository_id: pr.repository_id,
        target_environment: "development",
        ai_assist: true,
      });
      router.push(`/releases/${detail.release.id}`);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleGeneratePatch = async (
    pr: PullRequest,
    flagKey: string,
    flagType: "STANDARD" | "MULTIVARIATE" = "STANDARD",
    targetFiles?: string[]
  ) => {
    if (!flagKey.trim()) return;
    setBusyAction(`patch:${flagKey}`);
    setError(null);
    setNotice(null);
    try {
      await generatePrPatch(pr.id, {
        flag_key: flagKey.trim(),
        flag_type: flagType,
        target_files: targetFiles,
      });
      await selectPrDetail(pr.id);
      setCustomFlagKey("");
      setNotice(`Generated Flagsmith wrapping patch & tests for flag '${flagKey.trim()}'.`);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleValidateChange = async (change: CodeChange) => {
    setBusyAction(`validate:${change.id}`);
    setError(null);
    setNotice(null);
    try {
      const updated = await validateCodeChange(change.id);
      setSelectedPr((prev) =>
        prev
          ? {
              ...prev,
              code_changes: (prev.code_changes ?? []).map((c) =>
                c.id === updated.id ? updated : c
              ),
            }
          : prev
      );
      setNotice(
        `Patch validation finished with status '${updated.validation_status}'.`
      );
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleCreateDraftPr = async (change: CodeChange) => {
    setBusyAction(`draft:${change.id}`);
    setError(null);
    setNotice(null);
    setQueuedApproval(null);
    try {
      const outcome = await createDraftPrFromCodeChange(change.id, {
        base_branch: selectedPr?.base_branch,
        pr_title: `chore(flags): wrap ${change.flag_key} behind Flagsmith`,
      });
      if (outcome.execution_status === "approval_required" && outcome.approval) {
        setQueuedApproval(outcome.approval);
        if (selectedPr) void loadPrSideContext(selectedPr);
      } else if (outcome.data) {
        const raw = outcome.data as unknown as Record<string, unknown>;
        const prNum =
          typeof raw.draft_pr_number === "number"
            ? raw.draft_pr_number
            : typeof raw.pr_number === "number"
              ? raw.pr_number
              : undefined;
        if (selectedPr) {
          await selectPrDetail(selectedPr.id);
        }
        setNotice(
          `Draft PR ${prNum ? `#${prNum} ` : ""}opened on GitHub (draft: true, never auto-merged).`
        );
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const selectedRepo = useMemo(
    () => repos.find((r) => r.id === selectedPr?.repository_id) ?? null,
    [repos, selectedPr]
  );

  const diffSummary = useMemo(
    () => parseDiffSummary(selectedPr?.diff_summary),
    [selectedPr]
  );

  const recommendations = useMemo(
    () => (diffSummary?.recommendations ?? []).map(normalizeRecommendation),
    [diffSummary]
  );

  const unknowns = useMemo(
    () => (diffSummary?.unknowns ?? []).map(normalizeUnknown),
    [diffSummary]
  );

  const riskAssessment = diffSummary?.risk_assessment ?? null;
  const flagAgentRec = diffSummary?.flag_agent_recommendation ?? null;
  const dxStatus = diffSummary?.dx_status ?? null;

  // Categorize actions for explicit visual separation: Proposed vs Executed vs Approval-Required
  const actionBuckets = useMemo(() => {
    if (!selectedPr) {
      return { proposed: [] as string[], executed: [] as string[], approvalRequired: [] as string[] };
    }
    const proposed: string[] = [];
    const executed: string[] = [];
    const approvalRequired: string[] = [];

    if (selectedPr.analysis_status === "completed") {
      executed.push("AI & 7-Dim Risk Analysis (Cat A)");
    } else {
      proposed.push("Run AI & 7-Dim Risk Analysis");
    }

    if (dxStatus?.check_run) {
      executed.push(`GitHub Check-Run: ${dxStatus.check_run.conclusion ?? dxStatus.check_run.status}`);
    }
    if (dxStatus?.comment) {
      executed.push(`PR Summary Comment: ${dxStatus.comment.publish_status}`);
    }

    for (const opp of selectedPr.flag_opportunities ?? []) {
      const hasPatch = (selectedPr.code_changes ?? []).some(
        (c) => c.flag_key === opp.suggested_flag_key
      );
      if (!hasPatch) {
        proposed.push(
          opp.already_exists
            ? `Reuse existing flag '${opp.existing_flag_key || opp.suggested_flag_key}' & generate patch`
            : `Create flag '${opp.suggested_flag_key}' & generate patch`
        );
      }
    }

    for (const change of selectedPr.code_changes ?? []) {
      if (change.validation_status === "passed") {
        executed.push(`Validated patch '${change.flag_key}' in temp dir`);
      } else {
        proposed.push(`Validate patch '${change.flag_key}' in isolated temp dir`);
      }
      if (change.draft_pr_url) {
        executed.push(`Opened Draft PR #${change.draft_pr_number} (${change.flag_key})`);
      } else if (change.validation_status === "passed") {
        approvalRequired.push(`Draft PR creation for '${change.flag_key}' (Cat B)`);
      }
    }

    if (flagAgentRec?.requires_approval) {
      approvalRequired.push(
        `Flag Agent: provision '${flagAgentRec.flag_key}' in non-prod / prod`
      );
    }

    for (const ap of pendingApprovals) {
      approvalRequired.push(`${ap.title} (Cat ${ap.policy_category})`);
    }

    return { proposed, executed, approvalRequired };
  }, [selectedPr, dxStatus, flagAgentRec, pendingApprovals]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="PR Intelligence & Flag Wrapping"
        description="Analyze PR diffs with sandboxed untrusted input isolation, 7-dimensional risk scoring, Confirmed vs Potential vs Unknown findings, isolated PatchValidator checks, and Draft PRs only."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {systemHealth && (
              <div className="flex items-center gap-2 rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-1.5 text-[11px]">
                <span
                  className={cn(
                    "inline-flex items-center gap-1",
                    systemHealth.github_connected ? "text-emerald-300" : "text-amber-300"
                  )}
                >
                  <span className="h-1.5 w-1.5 rounded-full bg-current" />
                  GitHub {systemHealth.github_connected ? "Connected" : "Disconnected"}
                </span>
                <span className="text-slate-600">•</span>
                <span
                  className={cn(
                    "inline-flex items-center gap-1",
                    systemHealth.flagsmith_connected ? "text-emerald-300" : "text-amber-300"
                  )}
                >
                  <span className="h-1.5 w-1.5 rounded-full bg-current" />
                  Flagsmith {systemHealth.flagsmith_connected ? "Connected" : "Disconnected"}
                </span>
              </div>
            )}
            <button
              type="button"
              disabled={busyAction !== null}
              onClick={handleSyncPrs}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-semibold text-slate-200 hover:bg-slate-800 disabled:opacity-50"
            >
              <RefreshCw
                className={cn("h-3.5 w-3.5", busyAction === "sync" && "animate-spin")}
              />
              Sync PRs
            </button>
          </div>
        }
      />

      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <SuccessBanner message={notice} onDismiss={() => setNotice(null)} />
      <ApprovalQueuedBanner
        approval={queuedApproval}
        onDismiss={() => setQueuedApproval(null)}
      />

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <select
          value={repoFilter}
          onChange={(e) => setRepoFilter(e.target.value)}
          className="rounded-lg border border-slate-700 bg-[#111622] px-3 py-2 text-xs text-slate-200 focus:border-indigo-500 focus:outline-none"
        >
          <option value="">All Repositories</option>
          {repos.map((r) => (
            <option key={r.id} value={r.id}>
              {r.full_name}
            </option>
          ))}
        </select>

        <select
          value={riskFilter}
          onChange={(e) => setRiskFilter(e.target.value)}
          className="rounded-lg border border-slate-700 bg-[#111622] px-3 py-2 text-xs text-slate-200 focus:border-indigo-500 focus:outline-none"
        >
          <option value="">All Risk Levels</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
      </div>

      <div className="grid gap-6 lg:grid-cols-12">
        {/* PR List */}
        <SectionCard
          className="lg:col-span-4"
          title="Pull Requests"
          subtitle={`${prs.length} synced`}
        >
          {loading ? (
            <LoadingSkeleton rows={4} />
          ) : prs.length === 0 ? (
            <EmptyState
              icon={<GitPullRequest className="h-5 w-5" />}
              title="No pull requests found"
              description="Sync pull requests from a connected repository to start AI Risk Assessment."
            />
          ) : (
            <div className="space-y-2 max-h-[780px] overflow-y-auto">
              {prs.map((pr) => (
                <button
                  key={pr.id}
                  type="button"
                  onClick={() => void selectPrDetail(pr.id)}
                  className={cn(
                    "w-full rounded-xl border p-3 text-left transition-colors space-y-2",
                    selectedPr?.id === pr.id
                      ? "border-indigo-500/45 bg-indigo-500/10"
                      : "border-slate-800 bg-[#0d1119] hover:border-slate-700"
                  )}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-xs text-indigo-300">
                        #{pr.github_pr_number}
                      </span>
                      {pr.is_draft && (
                        <span className="rounded border border-slate-700 bg-slate-800/80 px-1.5 py-0.5 text-[10px] text-slate-300">
                          Draft
                        </span>
                      )}
                    </div>
                    <RiskLevelBadge level={pr.risk_level} score={pr.risk_score} />
                  </div>
                  <div className="line-clamp-2 text-xs font-semibold text-slate-100">
                    {pr.title}
                  </div>
                  <div className="flex items-center justify-between text-[11px] text-slate-400">
                    <span>@{pr.author_login}</span>
                    <StatusBadge status={pr.analysis_status} />
                  </div>
                </button>
              ))}
            </div>
          )}
        </SectionCard>

        {/* PR Workbench */}
        <div className="space-y-6 lg:col-span-8">
          {!selectedPr ? (
            <SectionCard title="PR Intelligence Workbench">
              <EmptyState
                icon={<Sparkles className="h-5 w-5" />}
                title="Select a pull request"
                description="Choose a pull request on the left to inspect risks, 7-dim breakdown, generate Flagsmith patches, run isolated validation, and open a Draft PR."
              />
            </SectionCard>
          ) : (
            <>
              {/* Repo & PR Metadata + AI Review Summary */}
              <SectionCard
                title={`PR #${selectedPr.github_pr_number}: ${selectedPr.title}`}
                subtitle={`${selectedRepo?.full_name ?? "Repository"} • ${selectedPr.head_branch} → ${selectedPr.base_branch}`}
                actions={
                  <div className="flex flex-wrap items-center gap-2">
                    <RiskLevelBadge
                      level={selectedPr.risk_level}
                      score={selectedPr.risk_score}
                    />
                    <a
                      href={selectedPr.html_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-200 hover:bg-slate-800"
                    >
                      GitHub
                      <ExternalLink className="h-3 w-3" />
                    </a>
                    <button
                      type="button"
                      disabled={busyAction !== null}
                      onClick={() => void handleAnalyze(selectedPr)}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
                    >
                      <Sparkles className="h-3.5 w-3.5" />
                      {busyAction === "analyze" ? "Analyzing..." : "Run AI PR Analysis"}
                    </button>
                    <button
                      type="button"
                      disabled={busyAction !== null}
                      onClick={() => void handleCreateRelease(selectedPr)}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-xs font-semibold text-emerald-200 hover:bg-emerald-500/20 disabled:opacity-50"
                    >
                      <Rocket className="h-3.5 w-3.5" />
                      {busyAction === "create-release" ? "Creating..." : "Create Release"}
                    </button>
                  </div>
                }
              >
                <div className="space-y-4">
                  {/* Metadata Grid */}
                  <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4 rounded-lg border border-slate-800/80 bg-[#0b0f18] p-3 text-xs">
                    <div>
                      <div className="text-[10px] uppercase tracking-wider text-slate-500">
                        Author & State
                      </div>
                      <div className="mt-1 flex items-center gap-1.5 text-slate-200">
                        <span>@{selectedPr.author_login}</span>
                        <StatusBadge status={selectedPr.state} />
                        {selectedPr.is_draft && <StatusBadge status="draft" />}
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] uppercase tracking-wider text-slate-500">
                        Head Commit SHA
                      </div>
                      <div
                        className="mt-1 flex items-center gap-1 font-mono text-cyan-300"
                        title={selectedPr.head_sha}
                      >
                        <GitBranch className="h-3.5 w-3.5 shrink-0" />
                        {selectedPr.head_sha ? selectedPr.head_sha.slice(0, 10) : "unknown"}
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] uppercase tracking-wider text-slate-500">
                        Flagsmith Project
                      </div>
                      <div className="mt-1 text-slate-200">
                        {selectedRepo?.flagsmith_project_id
                          ? `Project #${selectedRepo.flagsmith_project_id}`
                          : "Not linked"}
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] uppercase tracking-wider text-slate-500">
                        GitHub DX Publish
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5">
                        {dxStatus?.check_run ? (
                          <StatusBadge
                            status={dxStatus.check_run.conclusion ?? dxStatus.check_run.status}
                            label={`Check: ${dxStatus.check_run.conclusion ?? dxStatus.check_run.status}`}
                          />
                        ) : (
                          <span className="text-[11px] text-slate-500">No check-run</span>
                        )}
                        {dxStatus?.comment && (
                          <StatusBadge
                            status={dxStatus.comment.publish_status}
                            label={`Comment: ${dxStatus.comment.publish_status}`}
                          />
                        )}
                      </div>
                    </div>
                  </div>

                  {/* AI Review Summary */}
                  <div className="rounded-lg border border-slate-800/80 bg-[#0d1119] p-3.5">
                    <div className="mb-1.5 flex items-center justify-between">
                      <span className="text-xs font-semibold text-slate-200">
                        AI Review Summary
                      </span>
                      {selectedPr.analyzed_at && (
                        <span className="font-mono text-[11px] text-slate-500">
                          Analyzed {formatTimestamp(selectedPr.analyzed_at)}
                        </span>
                      )}
                    </div>
                    {selectedPr.analysis_summary ? (
                      <p className="text-xs leading-relaxed text-slate-300">
                        {selectedPr.analysis_summary}
                      </p>
                    ) : (
                      <p className="text-xs text-slate-400">
                        Click &ldquo;Run AI PR Analysis&rdquo; to evaluate diff risk signals
                        and compute the 7-dimensional release risk score under Category A
                        sandboxing.
                      </p>
                    )}
                  </div>
                </div>
              </SectionCard>

              {/* Explicit Action Lifecycle Separation: Proposed vs Executed vs Approval-Required */}
              <SectionCard
                title="Action Lifecycle & Policy Gates"
                subtitle="Explicit separation between Proposed, Executed, and Approval-Required actions"
              >
                <div className="grid gap-3 md:grid-cols-3">
                  {/* Proposed */}
                  <div className="rounded-lg border border-indigo-500/30 bg-indigo-500/5 p-3 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-indigo-200">
                        <Lightbulb className="h-3.5 w-3.5 text-indigo-400" />
                        Proposed
                      </span>
                      <span className="rounded-full bg-indigo-500/20 px-2 py-0.5 font-mono text-[10px] text-indigo-200">
                        {actionBuckets.proposed.length}
                      </span>
                    </div>
                    {actionBuckets.proposed.length === 0 ? (
                      <p className="text-[11px] text-slate-400">No pending proposals.</p>
                    ) : (
                      <ul className="space-y-1.5 text-[11px] text-slate-300">
                        {actionBuckets.proposed.map((item, idx) => (
                          <li key={idx} className="flex items-start gap-1.5">
                            <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-indigo-400" />
                            <span>{item}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>

                  {/* Executed */}
                  <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-emerald-200">
                        <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
                        Executed
                      </span>
                      <span className="rounded-full bg-emerald-500/20 px-2 py-0.5 font-mono text-[10px] text-emerald-200">
                        {actionBuckets.executed.length}
                      </span>
                    </div>
                    {actionBuckets.executed.length === 0 ? (
                      <p className="text-[11px] text-slate-400">No actions executed yet.</p>
                    ) : (
                      <ul className="space-y-1.5 text-[11px] text-slate-300">
                        {actionBuckets.executed.map((item, idx) => (
                          <li key={idx} className="flex items-start gap-1.5">
                            <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400" />
                            <span>{item}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>

                  {/* Approval-Required */}
                  <div className="rounded-lg border border-amber-500/35 bg-amber-500/5 p-3 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-amber-200">
                        <Lock className="h-3.5 w-3.5 text-amber-400" />
                        Approval-Required
                      </span>
                      <span className="rounded-full bg-amber-500/20 px-2 py-0.5 font-mono text-[10px] text-amber-200">
                        {actionBuckets.approvalRequired.length}
                      </span>
                    </div>
                    {actionBuckets.approvalRequired.length === 0 ? (
                      <p className="text-[11px] text-slate-400">
                        No gated actions awaiting approval.
                      </p>
                    ) : (
                      <ul className="space-y-1.5 text-[11px] text-slate-300">
                        {actionBuckets.approvalRequired.map((item, idx) => (
                          <li key={idx} className="flex items-start gap-1.5">
                            <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" />
                            <span>{item}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                    {pendingApprovals.length > 0 && (
                      <Link
                        href="/approvals"
                        className="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-300 underline hover:text-amber-200"
                      >
                        Review in Approvals Queue <ExternalLink className="h-3 w-3" />
                      </Link>
                    )}
                  </div>
                </div>
              </SectionCard>

              {/* 7-Dimensional Risk Breakdown */}
              {riskAssessment && (
                <SectionCard
                  title="7-Dimensional Risk Score Breakdown"
                  subtitle={`Assessment ${riskAssessment.assessment_key}${
                    riskAssessment.commit_sha
                      ? ` • SHA ${riskAssessment.commit_sha.slice(0, 10)}`
                      : ""
                  }`}
                  actions={
                    <RiskLevelBadge
                      level={riskAssessment.risk_classification}
                      score={riskAssessment.overall_risk_score}
                    />
                  }
                >
                  <div className="grid gap-3 md:grid-cols-2">
                    {riskAssessment.dimensions.map((dim) => {
                      const scoreVal = typeof dim.score === "number" ? dim.score : null;
                      return (
                        <div
                          key={dim.dimension_key}
                          className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 text-xs space-y-2"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-semibold text-slate-100">
                              {dim.dimension_name}
                            </span>
                            <div className="flex items-center gap-1.5">
                              {dim.severity && <RiskLevelBadge level={dim.severity} />}
                              <span className="font-mono text-xs text-cyan-300">
                                {scoreVal !== null ? `${Math.round(scoreVal)}/100` : "N/A"}
                              </span>
                            </div>
                          </div>
                          {scoreVal !== null && (
                            <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                              <div
                                className={cn(
                                  "h-full rounded-full",
                                  scoreVal >= 75
                                    ? "bg-rose-500"
                                    : scoreVal >= 50
                                      ? "bg-amber-500"
                                      : scoreVal >= 25
                                        ? "bg-sky-500"
                                        : "bg-emerald-500"
                                )}
                                style={{ width: `${Math.min(100, Math.max(0, scoreVal))}%` }}
                              />
                            </div>
                          )}
                          <div className="flex items-center justify-between text-[11px] text-slate-400">
                            <span>
                              Weight:{" "}
                              {typeof dim.weight === "number"
                                ? `${Math.round(dim.weight * 100)}%`
                                : "—"}
                            </span>
                            {dim.status && <StatusBadge status={dim.status} />}
                          </div>
                          {dim.explanations && dim.explanations.length > 0 && (
                            <ul className="space-y-0.5 text-[11px] text-slate-300">
                              {dim.explanations.slice(0, 2).map((exp, i) => (
                                <li key={i}>• {exp}</li>
                              ))}
                            </ul>
                          )}
                          {dim.limitations && dim.limitations.length > 0 && (
                            <div className="text-[11px] text-amber-300/90">
                              Limitation: {dim.limitations[0]}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </SectionCard>
              )}

              {/* Changed Files & GitHub Diff */}
              {selectedPr.changed_files && selectedPr.changed_files.length > 0 && (
                <SectionCard
                  title={`Changed Files & Diff (${selectedPr.changed_files.length})`}
                  subtitle="Live file patches retrieved from GitHub REST API"
                >
                  <div className="space-y-3 max-h-[380px] overflow-y-auto">
                    {selectedPr.changed_files.map((file, idx) => (
                      <div
                        key={`${file.filename}-${idx}`}
                        className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 text-xs space-y-2"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="font-mono font-semibold text-cyan-300">
                            {file.filename}
                          </span>
                          <div className="flex items-center gap-2 font-mono text-[11px]">
                            <StatusBadge status={file.status} />
                            <span className="text-emerald-400">+{file.additions}</span>
                            <span className="text-rose-400">-{file.deletions}</span>
                          </div>
                        </div>
                        {file.patch ? (
                          <pre className="overflow-x-auto rounded bg-[#070a0f] p-2.5 font-mono text-[11px] leading-relaxed text-slate-300">
                            {file.patch}
                          </pre>
                        ) : (
                          <div className="inline-flex items-center gap-1.5 rounded border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-[11px] text-amber-200">
                            <FileWarning className="h-3.5 w-3.5" />
                            Binary or truncated diff — patch content unavailable from GitHub
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </SectionCard>
              )}

              {/* Confirmed Risks vs Potential Risks */}
              <div className="grid gap-6 md:grid-cols-2">
                <SectionCard
                  title={`Confirmed / Critical Risks (${selectedPr.confirmed_risks?.length ?? 0})`}
                  subtitle="Directly observed in diff with file & line citations"
                >
                  {!selectedPr.confirmed_risks?.length ? (
                    <p className="text-xs text-slate-400">No confirmed risks recorded.</p>
                  ) : (
                    <div className="space-y-2.5">
                      {selectedPr.confirmed_risks.map((risk, idx) => (
                        <div
                          key={idx}
                          className="rounded-lg border border-rose-500/30 bg-rose-500/5 p-3 text-xs space-y-1.5"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="flex items-center gap-1.5 font-semibold text-rose-200">
                              <AlertTriangle className="h-3.5 w-3.5 text-rose-400" />
                              {risk.title}
                            </span>
                            <RiskLevelBadge level={risk.severity} />
                          </div>
                          <div className="font-mono text-[11px] text-cyan-300">
                            {risk.file_path}
                            {risk.line_range ? `:${risk.line_range}` : ""}
                          </div>
                          <p className="text-slate-300">{risk.evidence}</p>
                          {risk.mitigation && (
                            <p className="text-[11px] text-emerald-300">
                              Mitigation: {risk.mitigation}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </SectionCard>

                <SectionCard
                  title={`Potential Risks (${selectedPr.potential_risks?.length ?? 0})`}
                  subtitle="Inferred hypotheses requiring verification (marked unconfirmed)"
                >
                  {!selectedPr.potential_risks?.length ? (
                    <p className="text-xs text-slate-400">No potential risks recorded.</p>
                  ) : (
                    <div className="space-y-2.5">
                      {selectedPr.potential_risks.map((risk, idx) => (
                        <div
                          key={idx}
                          className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs space-y-1.5"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="flex items-center gap-1.5 font-semibold text-amber-200">
                              <HelpCircle className="h-3.5 w-3.5 text-amber-400" />
                              {risk.title}
                            </span>
                            <RiskLevelBadge level={risk.severity} />
                          </div>
                          <p className="text-slate-300">
                            <span className="text-slate-400">Assumption: </span>
                            {risk.assumption}
                          </p>
                          <p className="text-[11px] text-amber-300">
                            Verify: {risk.verification_needed}
                          </p>
                        </div>
                      ))}
                    </div>
                  )}
                </SectionCard>
              </div>

              {/* Unknowns & Actionable Recommendations */}
              {(unknowns.length > 0 || recommendations.length > 0) && (
                <div className="grid gap-6 md:grid-cols-2">
                  <SectionCard
                    title={`Unknowns & Verification Gaps (${unknowns.length})`}
                    subtitle="Areas not observable from static diff alone"
                  >
                    {unknowns.length === 0 ? (
                      <p className="text-xs text-slate-400">No unknowns recorded.</p>
                    ) : (
                      <div className="space-y-2.5">
                        {unknowns.map((unk, idx) => (
                          <div
                            key={idx}
                            className="rounded-lg border border-slate-700/80 bg-[#0d1119] p-3 text-xs space-y-1"
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span className="font-semibold text-slate-200">
                                {unk.title}
                              </span>
                              {unk.area && (
                                <span className="rounded border border-slate-700 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                                  {unk.area}
                                </span>
                              )}
                            </div>
                            <p className="text-slate-300">{unk.reason}</p>
                            {unk.impact && (
                              <p className="text-[11px] text-amber-300">{unk.impact}</p>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </SectionCard>

                  <SectionCard
                    title={`Recommendations (${recommendations.length})`}
                    subtitle="Concrete next steps before rollout"
                  >
                    {recommendations.length === 0 ? (
                      <p className="text-xs text-slate-400">No recommendations recorded.</p>
                    ) : (
                      <div className="space-y-2.5">
                        {recommendations.map((rec, idx) => (
                          <div
                            key={idx}
                            className="rounded-lg border border-indigo-500/30 bg-indigo-500/5 p-3 text-xs space-y-1"
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span className="font-semibold text-indigo-200">
                                {rec.title}
                              </span>
                              {rec.priority && <RiskLevelBadge level={rec.priority} />}
                            </div>
                            <p className="text-slate-300">{rec.action}</p>
                            {rec.target_file && (
                              <div className="font-mono text-[11px] text-cyan-300">
                                {rec.target_file}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </SectionCard>
                </div>
              )}

              {/* Flagsmith Recommendations, Duplicate Check & Patch Generator */}
              <SectionCard
                title="Flagsmith Recommendations & Patch Generator"
                subtitle="Duplicate-checked against Flagsmith & local DB before generating patches + tests"
                actions={<PolicyGateBadge category="A" compact />}
              >
                <div className="space-y-4">
                  {/* Flag Agent Handoff Recommendation */}
                  {flagAgentRec && (
                    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-cyan-500/30 bg-cyan-500/5 p-3.5 text-xs">
                      <div className="space-y-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="inline-flex items-center gap-1.5 font-semibold text-cyan-200">
                            <Bot className="h-3.5 w-3.5 text-cyan-400" />
                            Flag Agent Handoff:{" "}
                            <span className="font-mono">{flagAgentRec.flag_key}</span>
                          </span>
                          <StatusBadge
                            status={flagAgentRec.already_exists ? "validated" : "pending"}
                            label={
                              flagAgentRec.already_exists
                                ? "Exists in Flagsmith (Reuse)"
                                : "New Flag Required"
                            }
                          />
                          {flagAgentRec.requires_approval && (
                            <PolicyGateBadge category="B" compact />
                          )}
                        </div>
                        <p className="text-slate-300">{flagAgentRec.rationale}</p>
                      </div>
                      <Link
                        href="/flagsmith"
                        className="inline-flex items-center gap-1 rounded-lg border border-cyan-500/40 bg-cyan-500/15 px-3 py-1.5 text-xs font-semibold text-cyan-200 hover:bg-cyan-500/25"
                      >
                        Open Flag Manager <ExternalLink className="h-3 w-3" />
                      </Link>
                    </div>
                  )}

                  {selectedPr.flag_opportunities?.length > 0 && (
                    <div className="grid gap-3 md:grid-cols-2">
                      {selectedPr.flag_opportunities.map((opp, idx) => (
                        <div
                          key={idx}
                          className="flex flex-col justify-between rounded-lg border border-slate-800 bg-[#0d1119] p-3.5 text-xs space-y-2.5"
                        >
                          <div className="space-y-1.5">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <span className="inline-flex items-center gap-1.5 font-mono font-semibold text-cyan-300">
                                <Flag className="h-3.5 w-3.5" />
                                {opp.suggested_flag_key}
                              </span>
                              <div className="flex items-center gap-1.5">
                                {opp.already_exists ? (
                                  <span className="rounded border border-emerald-500/35 bg-emerald-500/10 px-1.5 py-0.5 font-mono text-[10px] text-emerald-300">
                                    Reuse Existing
                                  </span>
                                ) : (
                                  <span className="rounded border border-indigo-500/35 bg-indigo-500/10 px-1.5 py-0.5 font-mono text-[10px] text-indigo-300">
                                    New Flag
                                  </span>
                                )}
                                <span className="rounded border border-slate-700 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                                  {opp.flag_type || "STANDARD"}
                                </span>
                              </div>
                            </div>
                            <p className="text-slate-300">{opp.rationale}</p>
                            {opp.recommended_rollout && (
                              <div className="font-mono text-[11px] text-slate-400">
                                Rollout: {opp.recommended_rollout}
                              </div>
                            )}
                            {opp.target_files?.length > 0 && (
                              <div className="font-mono text-[11px] text-slate-400">
                                Files: {opp.target_files.join(", ")}
                              </div>
                            )}
                          </div>
                          <button
                            type="button"
                            disabled={busyAction !== null}
                            onClick={() =>
                              void handleGeneratePatch(
                                selectedPr,
                                opp.suggested_flag_key,
                                opp.flag_type === "MULTIVARIATE"
                                  ? "MULTIVARIATE"
                                  : "STANDARD",
                                opp.target_files
                              )
                            }
                            className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-indigo-500/20 border border-indigo-500/40 px-3 py-1.5 text-xs font-semibold text-indigo-200 hover:bg-indigo-500/30 disabled:opacity-50"
                          >
                            <Wrench className="h-3.5 w-3.5" />
                            {busyAction === `patch:${opp.suggested_flag_key}`
                              ? "Generating..."
                              : "Generate Flagsmith Patch"}
                          </button>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Manual patch trigger */}
                  <div className="flex flex-wrap items-center gap-2 border-t border-slate-800/80 pt-3">
                    <input
                      type="text"
                      value={customFlagKey}
                      onChange={(e) => setCustomFlagKey(e.target.value)}
                      placeholder="Custom flag key (e.g. checkout_v2)"
                      className="flex-1 min-w-[200px] rounded-lg border border-slate-700 bg-[#090c12] px-3 py-1.5 font-mono text-xs text-slate-100 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
                    />
                    <select
                      value={customFlagType}
                      onChange={(e) =>
                        setCustomFlagType(
                          e.target.value as "STANDARD" | "MULTIVARIATE"
                        )
                      }
                      className="rounded-lg border border-slate-700 bg-[#090c12] px-2.5 py-1.5 text-xs text-slate-200"
                    >
                      <option value="STANDARD">STANDARD</option>
                      <option value="MULTIVARIATE">MULTIVARIATE</option>
                    </select>
                    <button
                      type="button"
                      disabled={busyAction !== null || !customFlagKey.trim()}
                      onClick={() =>
                        void handleGeneratePatch(selectedPr, customFlagKey, customFlagType)
                      }
                      className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
                    >
                      <Wrench className="h-3.5 w-3.5" />
                      Generate Patch
                    </button>
                  </div>
                </div>
              </SectionCard>

              {/* Generated Code Changes + Isolated Validator + Draft PR */}
              {selectedPr.code_changes && selectedPr.code_changes.length > 0 && (
                <div className="space-y-4">
                  {selectedPr.code_changes.map((change) => {
                    const vr = change.validation_report;
                    const shaMatches =
                      !vr?.target_head_sha ||
                      !selectedPr.head_sha ||
                      vr.target_head_sha === selectedPr.head_sha;

                    return (
                      <SectionCard
                        key={change.id}
                        title={change.title}
                        subtitle={`Flag: ${change.flag_key} • Type: ${change.change_type}`}
                        actions={
                          <div className="flex flex-wrap items-center gap-2">
                            <StatusBadge status={change.status} />
                            <PolicyGateBadge category="B" compact />
                            <button
                              type="button"
                              disabled={busyAction !== null}
                              onClick={() => void handleValidateChange(change)}
                              className="inline-flex items-center gap-1.5 rounded-lg border border-cyan-500/40 bg-cyan-500/15 px-3 py-1.5 text-xs font-semibold text-cyan-200 hover:bg-cyan-500/25 disabled:opacity-50"
                            >
                              <ShieldCheck className="h-3.5 w-3.5" />
                              {busyAction === `validate:${change.id}`
                                ? "Validating..."
                                : "Validate in Temp Dir"}
                            </button>
                            <button
                              type="button"
                              disabled={
                                busyAction !== null ||
                                change.validation_status !== "passed" ||
                                Boolean(change.draft_pr_url)
                              }
                              title={
                                change.validation_status !== "passed"
                                  ? "Patch must pass Isolated Validator before opening a Draft PR"
                                  : "Category B: Creates branch + Draft PR (never auto-merges)"
                              }
                              onClick={() => void handleCreateDraftPr(change)}
                              className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-slate-950 hover:bg-emerald-400 disabled:opacity-45"
                            >
                              <Play className="h-3.5 w-3.5" />
                              {busyAction === `draft:${change.id}`
                                ? "Opening Draft PR..."
                                : change.draft_pr_url
                                  ? "Draft PR Opened"
                                  : "Create Draft PR (Cat B)"}
                            </button>
                          </div>
                        }
                      >
                        <div className="space-y-4">
                          {/* Draft PR Status */}
                          {change.draft_pr_url ? (
                            <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-emerald-500/35 bg-emerald-500/10 px-3.5 py-2.5 text-xs text-emerald-200">
                              <div>
                                <span className="font-semibold">
                                  Draft PR #{change.draft_pr_number} live on GitHub (draft:
                                  true, never auto-merged)
                                </span>
                                {change.draft_branch_name && (
                                  <span className="ml-2 font-mono text-emerald-300">
                                    ({change.draft_branch_name})
                                  </span>
                                )}
                              </div>
                              <a
                                href={change.draft_pr_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-1 font-semibold underline hover:text-white"
                              >
                                View Draft PR <ExternalLink className="h-3 w-3" />
                              </a>
                            </div>
                          ) : (
                            <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800 bg-[#0d1119] px-3.5 py-2 text-xs text-slate-300">
                              <span>
                                Draft PR Status:{" "}
                                <span className="font-semibold text-slate-100">
                                  Not opened yet
                                </span>{" "}
                                — requires passing PatchValidator + Category B action
                              </span>
                              <StatusBadge status={change.validation_status} />
                            </div>
                          )}

                          {/* Security & SHA Guardrail Strip */}
                          <div className="flex flex-wrap items-center gap-2 text-[11px]">
                            {vr?.target_head_sha && (
                              <span
                                className={cn(
                                  "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 font-mono",
                                  shaMatches
                                    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                                    : "border-rose-500/35 bg-rose-500/10 text-rose-300"
                                )}
                              >
                                {shaMatches ? (
                                  <ShieldCheck className="h-3 w-3" />
                                ) : (
                                  <ShieldAlert className="h-3 w-3" />
                                )}
                                Target SHA: {vr.target_head_sha.slice(0, 10)}
                                {!shaMatches && " (STALE)"}
                              </span>
                            )}
                            {typeof vr?.binary_ok === "boolean" && (
                              <span
                                className={cn(
                                  "inline-flex items-center gap-1 rounded-md border px-2 py-0.5",
                                  vr.binary_ok
                                    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                                    : "border-rose-500/35 bg-rose-500/10 text-rose-300"
                                )}
                              >
                                Binary Guard: {vr.binary_ok ? "Clean" : "Blocked"}
                              </span>
                            )}
                            {typeof vr?.sha_ok === "boolean" && (
                              <span
                                className={cn(
                                  "inline-flex items-center gap-1 rounded-md border px-2 py-0.5",
                                  vr.sha_ok
                                    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                                    : "border-rose-500/35 bg-rose-500/10 text-rose-300"
                                )}
                              >
                                SHA Check: {vr.sha_ok ? "Verified" : "Mismatch"}
                              </span>
                            )}
                          </div>

                          <CodeDiffViewer
                            filesDiff={change.files_diff ?? []}
                            generatedTests={change.generated_tests ?? []}
                            validationStatus={change.validation_status}
                            validationReport={change.validation_report}
                          />
                        </div>
                      </SectionCard>
                    );
                  })}
                </div>
              )}

              {/* Agent Execution History */}
              <SectionCard
                title={`Agent Execution History (${executions.length})`}
                subtitle="Tracked PR & repository agent runs with status and stage"
              >
                {executions.length === 0 ? (
                  <p className="text-xs text-slate-400">
                    No agent executions recorded for this pull request yet.
                  </p>
                ) : (
                  <div className="space-y-2">
                    {executions.map((ex) => (
                      <div
                        key={ex.id}
                        className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800 bg-[#0d1119] px-3.5 py-2.5 text-xs"
                      >
                        <div className="min-w-0 space-y-0.5">
                          <div className="flex items-center gap-2">
                            <span className="font-semibold text-slate-100">
                              {ex.task_name}
                            </span>
                            <span className="rounded border border-slate-700 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                              {ex.agent_type}
                            </span>
                          </div>
                          {ex.current_activity && (
                            <div className="truncate text-[11px] text-slate-400">
                              {ex.current_activity}
                            </div>
                          )}
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="inline-flex items-center gap-1 font-mono text-[11px] text-slate-400">
                            <Clock className="h-3 w-3" />
                            {formatTimestamp(ex.completed_at ?? ex.started_at ?? ex.execution_key)}
                          </span>
                          <StatusBadge status={ex.status} />
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </SectionCard>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function PullRequestsPage() {
  return (
    <Suspense fallback={<LoadingSkeleton rows={4} />}>
      <PullRequestsInner />
    </Suspense>
  );
}
