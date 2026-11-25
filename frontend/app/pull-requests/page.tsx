"use client";

import React, { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  ExternalLink,
  Flag,
  GitPullRequest,
  HelpCircle,
  Play,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Wrench,
} from "lucide-react";
import {
  analyzePullRequest,
  createDraftPrFromCodeChange,
  generatePrPatch,
  getErrorMessage,
  getPullRequest,
  listPullRequests,
  listRepositories,
  syncRepoPullRequests,
  validateCodeChange,
} from "@/lib/api";
import type { Approval, CodeChange, PullRequest, Repository } from "@/types/releaseguard";
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

function PullRequestsInner() {
  const searchParams = useSearchParams();
  const initialRepoId = searchParams.get("repository_id") ?? "";

  const [repos, setRepos] = useState<Repository[]>([]);
  const [prs, setPrs] = useState<PullRequest[]>([]);
  const [selectedPr, setSelectedPr] = useState<PullRequest | null>(null);

  const [repoFilter, setRepoFilter] = useState(initialRepoId);
  const [riskFilter, setRiskFilter] = useState("");
  const [customFlagKey, setCustomFlagKey] = useState("");
  const [customFlagType, setCustomFlagType] = useState<"STANDARD" | "MULTIVARIATE">(
    "STANDARD"
  );

  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [queuedApproval, setQueuedApproval] = useState<Approval | null>(null);

  const selectPrDetail = useCallback(async (prId: string) => {
    setError(null);
    try {
      const full = await getPullRequest(prId);
      setSelectedPr(full);
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }, []);

  const loadPrs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [repoList, prList] = await Promise.all([
        listRepositories({ limit: 50 }),
        listPullRequests({
          repository_id: repoFilter || undefined,
          risk_level: riskFilter || undefined,
          limit: 50,
        }),
      ]);
      setRepos(repoList.items);
      setPrs(prList.items);
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
          ? { ...updated, code_changes: prev.code_changes ?? updated.code_changes }
          : updated
      );
      setPrs((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
      setNotice(
        `Completed prompt-injection-hardened AI Risk Analysis for PR #${pr.github_pr_number}.`
      );
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

  return (
    <div className="space-y-6">
      <PageHeader
        title="PR Intelligence & Flag Wrapping"
        description="Analyze PR diffs with sandboxed untrusted input isolation, separate Confirmed vs Potential risks, validate Flagsmith patches in an isolated temp dir, and open Draft PRs only."
        actions={
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
            <div className="space-y-2 max-h-[700px] overflow-y-auto">
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
                    <span className="font-mono text-xs text-indigo-300">
                      #{pr.github_pr_number}
                    </span>
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
                description="Choose a pull request on the left to inspect risks, generate Flagsmith patches, run isolated validation, and open a Draft PR."
              />
            </SectionCard>
          ) : (
            <>
              {/* PR Overview */}
              <SectionCard
                title={`PR #${selectedPr.github_pr_number}: ${selectedPr.title}`}
                subtitle={`${selectedPr.head_branch} → ${selectedPr.base_branch} • Author @${selectedPr.author_login}`}
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
                  </div>
                }
              >
                {selectedPr.analysis_summary ? (
                  <p className="text-xs leading-relaxed text-slate-300">
                    {selectedPr.analysis_summary}
                  </p>
                ) : (
                  <p className="text-xs text-slate-400">
                    Click &ldquo;Run AI PR Analysis&rdquo; to evaluate diff risk signals under
                    Category A sandboxing.
                  </p>
                )}
                {selectedPr.analyzed_at && (
                  <div className="mt-2 font-mono text-[11px] text-slate-500">
                    Last analyzed: {formatTimestamp(selectedPr.analyzed_at)}
                  </div>
                )}
              </SectionCard>

              {/* Confirmed Risks vs Potential Risks */}
              <div className="grid gap-6 md:grid-cols-2">
                <SectionCard
                  title={`Confirmed Risks (${selectedPr.confirmed_risks?.length ?? 0})`}
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

              {/* Flag Opportunities + Custom Patch Generator */}
              <SectionCard
                title="Flag Opportunities & Patch Generator"
                subtitle="Wrap risky changes behind Flagsmith SDK checks with automated tests"
                actions={<PolicyGateBadge category="A" compact />}
              >
                <div className="space-y-4">
                  {selectedPr.flag_opportunities?.length > 0 && (
                    <div className="grid gap-3 md:grid-cols-2">
                      {selectedPr.flag_opportunities.map((opp, idx) => (
                        <div
                          key={idx}
                          className="flex flex-col justify-between rounded-lg border border-slate-800 bg-[#0d1119] p-3.5 text-xs space-y-2.5"
                        >
                          <div className="space-y-1.5">
                            <div className="flex items-center justify-between gap-2">
                              <span className="inline-flex items-center gap-1.5 font-mono font-semibold text-cyan-300">
                                <Flag className="h-3.5 w-3.5" />
                                {opp.suggested_flag_key}
                              </span>
                              <span className="rounded border border-slate-700 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                                {opp.flag_type || "STANDARD"}
                              </span>
                            </div>
                            <p className="text-slate-300">{opp.rationale}</p>
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
                  {selectedPr.code_changes.map((change) => (
                    <SectionCard
                      key={change.id}
                      title={change.title}
                      subtitle={`Flag: ${change.flag_key} • Type: ${change.change_type}`}
                      actions={
                        <div className="flex flex-wrap items-center gap-2">
                          <StatusBadge status={change.status} />
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
                        {change.draft_pr_url && (
                          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-emerald-500/35 bg-emerald-500/10 px-3.5 py-2.5 text-xs text-emerald-200">
                            <div>
                              <span className="font-semibold">
                                Draft PR #{change.draft_pr_number} live on GitHub
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
                        )}

                        <CodeDiffViewer
                          filesDiff={change.files_diff ?? []}
                          generatedTests={change.generated_tests ?? []}
                          validationStatus={change.validation_status}
                          validationReport={change.validation_report}
                        />
                      </div>
                    </SectionCard>
                  ))}
                </div>
              )}
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
