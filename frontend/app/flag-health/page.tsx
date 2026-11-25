"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  ExternalLink,
  HeartPulse,
  RefreshCw,
  Trash2,
  Wrench,
} from "lucide-react";
import {
  generateCleanupPr,
  getErrorMessage,
  getFlagHealth,
  listRepositories,
  requestFlagDelete,
  scanFlagHealth,
} from "@/lib/api";
import type {
  Approval,
  CodeChange,
  FeatureFlag,
  FlagHealthResponse,
  HealthIssueType,
  Repository,
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
import { PolicyGateBadge } from "@/components/PolicyGateBadge";
import { StatusBadge } from "@/components/StatusBadges";
import {
  HealthScoreBreakdown,
  HealthScoreGauge,
} from "@/components/HealthScoreBreakdown";
import { CodeDiffViewer } from "@/components/CodeDiffViewer";
import { ApprovalActionCard } from "@/components/ApprovalActionCard";

const DETECTORS: Array<{ value: "" | HealthIssueType; label: string }> = [
  { value: "", label: "All Detectors" },
  { value: "orphaned", label: "Orphaned (-30)" },
  { value: "env_drift", label: "Env Drift (-20)" },
  { value: "stale", label: "Stale (-15)" },
  { value: "always_on", label: "Always On (-15)" },
  { value: "always_off", label: "Always Off (-15)" },
  { value: "unused", label: "Unused (-15)" },
  { value: "duplicate", label: "Duplicate (-10)" },
];

export default function FlagHealthPage() {
  const [healthData, setHealthData] = useState<FlagHealthResponse | null>(null);
  const [repos, setRepos] = useState<Repository[]>([]);
  const [issueFilter, setIssueFilter] = useState<"" | HealthIssueType>("");
  const [selectedFlag, setSelectedFlag] = useState<FeatureFlag | null>(null);

  // Cleanup PR config
  const [cleanupRepoId, setCleanupRepoId] = useState("");
  const [keepBranch, setKeepBranch] = useState<"enabled" | "disabled">("enabled");
  const [openDraftPr, setOpenDraftPr] = useState(false);
  const [cleanupChange, setCleanupChange] = useState<CodeChange | null>(null);

  // Delete/Archive Category C request
  const [deleteAction, setDeleteAction] = useState<"archive" | "delete">("archive");
  const [deleteReason, setDeleteReason] = useState("");

  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [queuedApproval, setQueuedApproval] = useState<Approval | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [hRes, repoRes] = await Promise.all([
        getFlagHealth({ issue_type: issueFilter || undefined }),
        listRepositories({ limit: 50 }).catch(() => ({
          items: [],
          total: 0,
          limit: 50,
          offset: 0,
        })),
      ]);
      setHealthData(hRes);
      setRepos(repoRes.items);
      if (repoRes.items.length > 0 && !cleanupRepoId) {
        setCleanupRepoId(repoRes.items[0].id);
      }
      if (hRes.items.length > 0) {
        const nextSel =
          selectedFlag && hRes.items.find((f) => f.id === selectedFlag.id);
        setSelectedFlag(nextSel ?? hRes.items[0]);
      } else {
        setSelectedFlag(null);
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [cleanupRepoId, issueFilter, selectedFlag]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void loadData();
    }, 0);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [issueFilter]);

  const handleTriggerScan = async () => {
    setBusyAction("scan");
    setError(null);
    setNotice(null);
    try {
      const res = await scanFlagHealth({
        repository_id: cleanupRepoId || undefined,
      });
      setNotice(
        `Completed 7-detector Flag Health scan across ${res.scanned_count ?? res.items?.length ?? 0} flags.`
      );
      await loadData();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleCleanupPr = async (flag: FeatureFlag) => {
    if (!cleanupRepoId) {
      setError("Connect or select a target repository for the cleanup Draft PR.");
      return;
    }
    setBusyAction("cleanup");
    setError(null);
    setNotice(null);
    setQueuedApproval(null);
    setCleanupChange(null);
    try {
      const outcome = await generateCleanupPr(flag.id, {
        repository_id: cleanupRepoId,
        keep_branch: keepBranch,
        open_draft_pr: openDraftPr,
      });
      if (outcome.execution_status === "approval_required" && outcome.approval) {
        setQueuedApproval(outcome.approval);
      }
      if (outcome.data) {
        setCleanupChange(outcome.data);
        setNotice(
          outcome.data.draft_pr_url
            ? `Validated cleanup patch & opened Draft PR #${outcome.data.draft_pr_number}.`
            : `Generated & validated flag-removal cleanup patch for '${flag.key}'.`
        );
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleRequestDelete = async (flag: FeatureFlag) => {
    setBusyAction("delete");
    setError(null);
    setNotice(null);
    setQueuedApproval(null);
    try {
      const outcome = await requestFlagDelete(flag.id, {
        action: deleteAction,
        reason:
          deleteReason.trim() ||
          `Operator requested ${deleteAction} for '${flag.key}' via Flag Health Monitor`,
      });
      if (outcome.execution_status === "approval_required" && outcome.approval) {
        setQueuedApproval(outcome.approval);
        setNotice(
          `Queued mandatory Category C approval to ${deleteAction} '${flag.key}'. Flags are never auto-deleted.`
        );
      } else {
        setNotice(`Submitted Category C ${deleteAction} request for '${flag.key}'.`);
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const flags = healthData?.items ?? [];
  const projectScore =
    typeof healthData?.average_health_score === "number"
      ? healthData.average_health_score
      : flags.length > 0
        ? Math.round(flags.reduce((s, f) => s + f.health_score, 0) / flags.length)
        : 100;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Flag Health Monitor"
        description="Deterministic 7-detector flag hygiene scanner with explainable deductions, validated cleanup Draft PRs, and mandatory Category C delete approvals."
        actions={
          <button
            type="button"
            disabled={busyAction !== null}
            onClick={handleTriggerScan}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3.5 py-2 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
          >
            <RefreshCw
              className={cn("h-3.5 w-3.5", busyAction === "scan" && "animate-spin")}
            />
            {busyAction === "scan" ? "Scanning 7 Detectors..." : "Trigger Health Scan"}
          </button>
        }
      />

      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <SuccessBanner message={notice} onDismiss={() => setNotice(null)} />
      <ApprovalQueuedBanner
        approval={queuedApproval}
        onDismiss={() => setQueuedApproval(null)}
      />
      {queuedApproval && (
        <ApprovalActionCard
          approval={queuedApproval}
          highlighted
          onUpdated={() => {
            setQueuedApproval(null);
            void loadData();
          }}
        />
      )}

      {/* Project Gauge + Detector Filter Chips */}
      <div className="grid gap-6 lg:grid-cols-12">
        <SectionCard className="lg:col-span-5" title="Workspace Flag Health">
          <HealthScoreGauge
            score={projectScore}
            label={`Average Across ${flags.length} Synced Flags`}
          />
        </SectionCard>

        <SectionCard
          className="lg:col-span-7"
          title="7 Deterministic Detectors"
          subtitle="Filter flags by detected hygiene, drift, or staleness issue"
        >
          <div className="flex flex-wrap gap-2">
            {DETECTORS.map((d) => {
              const count =
                d.value && healthData?.detector_counts
                  ? healthData.detector_counts[d.value]
                  : undefined;
              return (
                <button
                  key={d.label}
                  type="button"
                  onClick={() => setIssueFilter(d.value)}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors",
                    issueFilter === d.value
                      ? "border-indigo-500/50 bg-indigo-500/20 text-indigo-200"
                      : "border-slate-800 bg-[#0d1119] text-slate-300 hover:border-slate-700"
                  )}
                >
                  <span>{d.label}</span>
                  {typeof count === "number" && (
                    <span className="rounded-full bg-slate-800 px-1.5 py-0.2 font-mono text-[10px] tabular-nums">
                      {count}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </SectionCard>
      </div>

      {/* Flags Sorted by Health Score ASC + Detail Breakdown */}
      <div className="grid gap-6 lg:grid-cols-12">
        <SectionCard
          className="lg:col-span-5"
          title="Flag Hygiene Ranking"
          subtitle="Sorted ascending by health_score (lowest first)"
        >
          {loading ? (
            <LoadingSkeleton rows={4} />
          ) : flags.length === 0 ? (
            <EmptyState
              icon={<HeartPulse className="h-5 w-5" />}
              title="No flags match this filter"
              description="Sync flags from Flagsmith and run Trigger Health Scan to evaluate all 7 detectors."
            />
          ) : (
            <div className="space-y-2 max-h-[600px] overflow-y-auto">
              {flags.map((flag) => (
                <button
                  key={flag.id}
                  type="button"
                  onClick={() => {
                    setSelectedFlag(flag);
                    setCleanupChange(null);
                  }}
                  className={cn(
                    "w-full rounded-xl border p-3 text-left transition-colors",
                    selectedFlag?.id === flag.id
                      ? "border-indigo-500/45 bg-indigo-500/10"
                      : "border-slate-800 bg-[#0d1119] hover:border-slate-700"
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-xs font-semibold text-slate-100">
                      {flag.key}
                    </span>
                    <span
                      className={cn(
                        "rounded border px-2 py-0.5 font-mono text-xs font-bold tabular-nums",
                        flag.health_score >= 80
                          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                          : flag.health_score >= 55
                            ? "border-amber-500/35 bg-amber-500/10 text-amber-300"
                            : "border-rose-500/40 bg-rose-500/15 text-rose-300"
                      )}
                    >
                      {flag.health_score}/100
                    </span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    {flag.health_issues?.length ? (
                      flag.health_issues.map((iss, idx) => (
                        <span
                          key={idx}
                          className="rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 font-mono text-[10px] text-amber-200"
                        >
                          {iss.issue_type}
                        </span>
                      ))
                    ) : (
                      <span className="text-[11px] text-emerald-300">Clean</span>
                    )}
                  </div>
                </button>
              ))}
            </div>
          )}
        </SectionCard>

        <div className="space-y-6 lg:col-span-7">
          {!selectedFlag ? (
            <SectionCard title="Health Breakdown & Remediation">
              <EmptyState
                icon={<HeartPulse className="h-5 w-5" />}
                title="Select a flag"
                description="Choose a flag on the left to inspect deduction evidence, generate a cleanup Draft PR, or request Category C archive/deletion."
              />
            </SectionCard>
          ) : (
            <>
              <SectionCard
                title={selectedFlag.key}
                subtitle={`Last scanned: ${formatTimestamp(selectedFlag.last_health_scan_at)}`}
                actions={
                  <StatusBadge
                    status={selectedFlag.is_archived ? "disabled" : "active"}
                    label={selectedFlag.is_archived ? "Archived" : "Active"}
                  />
                }
              >
                <HealthScoreBreakdown
                  score={selectedFlag.health_score}
                  issues={selectedFlag.health_issues ?? []}
                />
              </SectionCard>

              {/* Cleanup Draft PR Workflow */}
              <SectionCard
                title="Generate Cleanup Patch & Draft PR"
                subtitle="Removes flag branch in code, validates in isolated temp dir, and opens Draft PR (never auto-merges)"
                actions={<PolicyGateBadge category="B" compact />}
              >
                <div className="grid gap-3 sm:grid-cols-3">
                  <select
                    value={cleanupRepoId}
                    onChange={(e) => setCleanupRepoId(e.target.value)}
                    className="rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 text-xs text-slate-200"
                  >
                    <option value="">Select target repo...</option>
                    {repos.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.full_name}
                      </option>
                    ))}
                  </select>
                  <select
                    value={keepBranch}
                    onChange={(e) =>
                      setKeepBranch(e.target.value as "enabled" | "disabled")
                    }
                    className="rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 text-xs text-slate-200"
                  >
                    <option value="enabled">Keep Enabled Branch (100%)</option>
                    <option value="disabled">Keep Disabled Branch (0%)</option>
                  </select>
                  <button
                    type="button"
                    disabled={busyAction !== null}
                    onClick={() => void handleCleanupPr(selectedFlag)}
                    className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
                  >
                    <Wrench className="h-3.5 w-3.5" />
                    {busyAction === "cleanup"
                      ? "Generating..."
                      : "Generate Cleanup PR"}
                  </button>
                </div>

                <label className="mt-3 inline-flex items-center gap-2 text-xs text-slate-300">
                  <input
                    type="checkbox"
                    checked={openDraftPr}
                    onChange={(e) => setOpenDraftPr(e.target.checked)}
                    className="rounded border-slate-700 bg-[#090c12]"
                  />
                  <span>
                    Immediately open GitHub Draft PR if validation passes (or queue Category
                    B approval)
                  </span>
                </label>

                {cleanupChange && (
                  <div className="mt-4 space-y-3">
                    {cleanupChange.draft_pr_url && (
                      <div className="flex items-center justify-between rounded-lg border border-emerald-500/35 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
                        <span>Cleanup Draft PR #{cleanupChange.draft_pr_number}</span>
                        <a
                          href={cleanupChange.draft_pr_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 font-semibold underline"
                        >
                          Open on GitHub <ExternalLink className="h-3 w-3" />
                        </a>
                      </div>
                    )}
                    <CodeDiffViewer
                      filesDiff={cleanupChange.files_diff ?? []}
                      generatedTests={cleanupChange.generated_tests ?? []}
                      validationStatus={cleanupChange.validation_status}
                      validationReport={cleanupChange.validation_report}
                    />
                  </div>
                )}
              </SectionCard>

              {/* Category C Delete / Archive Request */}
              <SectionCard
                title="Archive or Delete Flag in Flagsmith"
                subtitle="Always Category C — queues a mandatory single-use human approval; never auto-deletes"
                actions={<PolicyGateBadge category="C" compact />}
              >
                <div className="grid gap-3 sm:grid-cols-4">
                  <select
                    value={deleteAction}
                    onChange={(e) =>
                      setDeleteAction(e.target.value as "archive" | "delete")
                    }
                    className="rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 text-xs text-slate-200"
                  >
                    <option value="archive">Archive Flag</option>
                    <option value="delete">Delete Flag</option>
                  </select>
                  <input
                    type="text"
                    value={deleteReason}
                    onChange={(e) => setDeleteReason(e.target.value)}
                    placeholder="Audit justification (e.g. cleanup PR merged)"
                    className="sm:col-span-2 rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 text-xs text-slate-100"
                  />
                  <button
                    type="button"
                    disabled={busyAction !== null}
                    onClick={() => void handleRequestDelete(selectedFlag)}
                    className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-rose-500/45 bg-rose-500/15 px-3 py-2 text-xs font-semibold text-rose-200 hover:bg-rose-500/25 disabled:opacity-50"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    {busyAction === "delete"
                      ? "Queueing..."
                      : `Request ${deleteAction === "archive" ? "Archive" : "Delete"}`}
                  </button>
                </div>
              </SectionCard>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
