"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Download,
  FileCode2,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  XCircle,
} from "lucide-react";
import {
  API_BASE_URL,
  analyzeRelease,
  cancelRelease,
  createRiskAssessment,
  evaluateReleaseReadiness,
  getErrorMessage,
  getReleaseDetail,
  getReleaseReportExportUrl,
  listAgentExecutions,
  pauseRelease,
  requestReleaseApproval,
  resumeRelease,
  retryRelease,
  validateRelease,
} from "@/lib/api";
import type { AgentExecutionRecord, ReleaseDetail } from "@/types/releaseguard";
import { formatTimestamp } from "@/lib/utils";
import {
  ErrorBanner,
  LoadingSkeleton,
  PageHeader,
  SectionCard,
  SuccessBanner,
} from "@/components/ui/StatePrimitives";
import { EnvironmentBadge, RiskLevelBadge, StatusBadge } from "@/components/StatusBadges";
import { ApprovalActionCard } from "@/components/ApprovalActionCard";

export default function ReleaseDetailPage() {
  const params = useParams<{ id: string }>();
  const releaseId = params?.id ?? "";

  const [detail, setDetail] = useState<ReleaseDetail | null>(null);
  const [executions, setExecutions] = useState<AgentExecutionRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"overview" | "approvals" | "artifacts" | "timeline">(
    "overview",
  );
  const [timelineFilter, setTimelineFilter] = useState("");

  const loadDetail = useCallback(async () => {
    if (!releaseId) return;
    try {
      const res = await getReleaseDetail(releaseId);
      setDetail(res);
      setError(null);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [releaseId]);

  useEffect(() => {
    if (!releaseId) return;
    let active = true;
    Promise.all([
      getReleaseDetail(releaseId),
      listAgentExecutions({ release_id: releaseId, limit: 10 }).catch(() => ({
        items: [],
        total: 0,
        limit: 10,
        offset: 0,
      })),
    ])
      .then(([res, exeRes]) => {
        if (!active) return;
        setDetail(res);
        setExecutions(Array.isArray(exeRes?.items) ? exeRes.items : []);
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
  }, [releaseId]);

  useEffect(() => {
    if (!releaseId || typeof EventSource === "undefined") return;
    const es = new EventSource(`${API_BASE_URL}/api/v1/events/stream`, {
      withCredentials: true,
    });
    const handler = (evt: MessageEvent) => {
      try {
        const parsed = JSON.parse(evt.data);
        if (parsed?.release_id === releaseId) {
          void loadDetail();
        }
      } catch {
        // Ignore malformed SSE payloads
      }
    };
    es.addEventListener("release.updated", handler);
    es.addEventListener("release.readiness_updated", handler);
    es.addEventListener("release.approval_requested", handler);
    return () => {
      es.close();
    };
  }, [releaseId, loadDetail]);

  const runAction = async (key: string, fn: () => Promise<unknown>, successMsg: string) => {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      await fn();
      setNotice(successMsg);
      await loadDetail();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  if (loading) {
    return (
      <div className="space-y-4">
        <LoadingSkeleton rows={6} />
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="space-y-4">
        <ErrorBanner error={error || "Release not found."} onDismiss={() => setError(null)} />
        <Link
          href="/releases"
          className="inline-flex items-center gap-1.5 text-xs text-indigo-300 hover:underline"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Back to Releases
        </Link>
      </div>
    );
  }

  const { release, stages, checks, events, artifacts, approvals } = detail;
  const isTerminal = ["completed", "rolled_back", "cancelled"].includes(release.status);
  const filteredEvents = timelineFilter.trim()
    ? events.filter((e) =>
        `${e.event_type} ${e.title} ${e.message ?? ""}`
          .toLowerCase()
          .includes(timelineFilter.trim().toLowerCase()),
      )
    : events;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <Link href="/releases" className="inline-flex items-center gap-1 hover:text-slate-200">
          <ArrowLeft className="h-3.5 w-3.5" />
          Releases
        </Link>
        <span>/</span>
        <span className="font-mono text-indigo-300">{release.release_key}</span>
      </div>

      <PageHeader
        title={`${release.release_key}: ${release.title}`}
        description={
          release.description ||
          `Managed ${release.release_type} release targeting ${release.target_environment} (v${release.version}).`
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Link
              href="/progressive-delivery"
              className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-500/40 bg-indigo-500/10 px-3 py-1.5 text-xs font-medium text-indigo-200 hover:bg-indigo-500/20"
            >
              Progressive Delivery
            </Link>
            <button
              type="button"
              onClick={() => void loadDetail()}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-800"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Refresh
            </button>
            <a
              href={getReleaseReportExportUrl(release.id)}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-800"
            >
              <Download className="h-3.5 w-3.5" />
              Export Markdown
            </a>
          </div>
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={release.status} />
        <EnvironmentBadge environment={release.target_environment} />
        <RiskLevelBadge level={release.risk_level} />
        <StatusBadge status={release.readiness_status} />
        <span className="rounded-full border border-slate-700 bg-slate-900 px-2.5 py-0.5 font-mono text-[11px] text-slate-300">
          Readiness: {release.readiness_score}/100
        </span>
        <span className="rounded-full border border-slate-700 bg-slate-900 px-2.5 py-0.5 font-mono text-[11px] text-slate-300">
          v{release.version}
        </span>
      </div>

      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <SuccessBanner message={notice} onDismiss={() => setNotice(null)} />

      {/* Operator Controls */}
      {!isTerminal && (
        <SectionCard title="Lifecycle Controls" subtitle="Execute stages, evaluate readiness, or manage release state">
          <div className="flex flex-wrap gap-2">
            {release.pull_request_id && (
              <>
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() =>
                    void runAction(
                      "analyze",
                      () => analyzeRelease(release.id),
                      "PR risk & static analysis completed.",
                    )
                  }
                  className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
                >
                  <Sparkles className="h-3.5 w-3.5" />
                  {busy === "analyze" ? "Analyzing..." : "Analyze PR"}
                </button>
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() =>
                    void runAction(
                      "validate",
                      () => validateRelease(release.id),
                      "Isolated patch validation completed.",
                    )
                  }
                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800 disabled:opacity-50"
                >
                  <FileCode2 className="h-3.5 w-3.5" />
                  {busy === "validate" ? "Validating..." : "Validate Patch"}
                </button>
              </>
            )}

            <button
              type="button"
              disabled={busy !== null}
              onClick={() =>
                void runAction(
                  "readiness",
                  () => evaluateReleaseReadiness(release.id),
                  "Readiness score and verification checks updated.",
                )
              }
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800 disabled:opacity-50"
            >
              <ShieldCheck className="h-3.5 w-3.5 text-emerald-400" />
              {busy === "readiness" ? "Evaluating..." : "Evaluate Readiness"}
            </button>

            <button
              type="button"
              disabled={busy !== null}
              onClick={() =>
                void runAction(
                  "risk",
                  () =>
                    createRiskAssessment({
                      release_id: release.id,
                      pull_request_id: release.pull_request_id ?? undefined,
                      repository_id: release.repository_id ?? undefined,
                      target_environment: release.target_environment,
                    }),
                  "Intelligent Risk Assessment completed (view in Risk Intelligence).",
                )
              }
              className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-500/40 bg-indigo-500/10 px-3 py-1.5 text-xs font-medium text-indigo-200 hover:bg-indigo-500/20 disabled:opacity-50"
            >
              <ShieldCheck className="h-3.5 w-3.5 text-indigo-400" />
              {busy === "risk" ? "Assessing Risk..." : "Run Risk Assessment"}
            </button>

            <button
              type="button"
              disabled={busy !== null}
              onClick={() =>
                void runAction(
                  "approval",
                  () => requestReleaseApproval(release.id),
                  "Approval request submitted for human review.",
                )
              }
              className="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs font-semibold text-amber-200 hover:bg-amber-500/20 disabled:opacity-50"
            >
              <CheckCircle2 className="h-3.5 w-3.5" />
              {busy === "approval" ? "Requesting..." : "Request Approval"}
            </button>

            {release.status === "paused" ? (
              <button
                type="button"
                disabled={busy !== null}
                onClick={() =>
                  void runAction(
                    "resume",
                    () => resumeRelease(release.id, { expected_version: release.version }),
                    "Release resumed.",
                  )
                }
                className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-200 hover:bg-emerald-500/20"
              >
                <Play className="h-3.5 w-3.5" />
                Resume
              </button>
            ) : (
              <button
                type="button"
                disabled={busy !== null}
                onClick={() =>
                  void runAction(
                    "pause",
                    () => pauseRelease(release.id, { expected_version: release.version }),
                    "Release paused.",
                  )
                }
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800"
              >
                <Pause className="h-3.5 w-3.5" />
                Pause
              </button>
            )}

            {(release.status === "failed" || release.status === "blocked") && (
              <button
                type="button"
                disabled={busy !== null}
                onClick={() =>
                  void runAction(
                    "retry",
                    () => retryRelease(release.id, { expected_version: release.version }),
                    "Release retried from failed stage.",
                  )
                }
                className="inline-flex items-center gap-1.5 rounded-lg border border-sky-500/40 bg-sky-500/10 px-3 py-1.5 text-xs font-medium text-sky-200 hover:bg-sky-500/20"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                Retry Failed Stage
              </button>
            )}

            <button
              type="button"
              disabled={busy !== null}
              onClick={() =>
                void runAction(
                  "cancel",
                  () => cancelRelease(release.id, { expected_version: release.version }),
                  "Release cancelled.",
                )
              }
              className="inline-flex items-center gap-1.5 rounded-lg border border-rose-500/35 bg-rose-500/10 px-3 py-1.5 text-xs font-medium text-rose-200 hover:bg-rose-500/20"
            >
              <XCircle className="h-3.5 w-3.5" />
              Cancel
            </button>
          </div>
        </SectionCard>
      )}

      {/* Stage Tracker */}
      <SectionCard title="Release Stage Tracker" subtitle="Backend-persisted lifecycle progression">
        <div className="grid gap-2.5 sm:grid-cols-3 lg:grid-cols-9">
          {stages.map((st) => (
            <div
              key={st.id}
              className="rounded-lg border border-slate-800 bg-[#0d1119] p-2.5 text-xs"
            >
              <div className="mb-1 flex items-center justify-between gap-1">
                <span className="font-mono text-[10px] text-slate-500">#{st.stage_order}</span>
                <StatusBadge status={st.status} />
              </div>
              <div className="font-medium text-slate-200">{st.title}</div>
              {st.summary && (
                <p className="mt-1 line-clamp-2 text-[11px] text-slate-400">{st.summary}</p>
              )}
            </div>
          ))}
        </div>
      </SectionCard>

      {/* Readiness & Checks */}
      <SectionCard
        title={`Readiness Score: ${release.readiness_score}/100`}
        subtitle="Deterministic verification checks & blocking gate evaluation"
        actions={<StatusBadge status={release.readiness_status} />}
      >
        {release.readiness_summary?.blocking_issues &&
          release.readiness_summary.blocking_issues.length > 0 && (
            <div className="mb-3 rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-200">
              <div className="mb-1 flex items-center gap-1.5 font-semibold">
                <AlertTriangle className="h-3.5 w-3.5" />
                Blocking Issues
              </div>
              <ul className="list-disc pl-5">
                {release.readiness_summary.blocking_issues.map((msg, idx) => (
                  <li key={idx}>{msg}</li>
                ))}
              </ul>
            </div>
          )}

        <div className="grid gap-2.5 md:grid-cols-2 lg:grid-cols-3">
          {checks.map((chk) => (
            <div
              key={chk.id}
              className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 text-xs"
            >
              <div className="mb-1.5 flex items-center justify-between gap-2">
                <span className="font-semibold text-slate-100">{chk.title}</span>
                <StatusBadge status={chk.status} />
              </div>
              <p className="text-[11px] text-slate-400">{chk.message || "—"}</p>
              <div className="mt-2 flex items-center justify-between font-mono text-[10px] text-slate-500">
                <span>weight: {chk.weight}</span>
                <span>{chk.is_blocking ? "blocking gate" : "advisory"}</span>
              </div>
            </div>
          ))}
        </div>
      </SectionCard>

      {/* Tabs */}
      <div className="flex flex-wrap gap-2 border-b border-slate-800 pb-2 text-xs">
        {(
          [
            ["overview", "Overview & Links"],
            ["approvals", `Approvals (${approvals.length})`],
            ["artifacts", `Artifacts (${artifacts.length})`],
            ["timeline", `Timeline (${events.length})`],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setActiveTab(key)}
            className={`rounded-lg px-3 py-1.5 font-medium ${
              activeTab === key
                ? "bg-indigo-500/20 text-indigo-200 border border-indigo-500/40"
                : "text-slate-400 hover:bg-slate-900 hover:text-slate-200"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {activeTab === "overview" && (
        <div className="grid gap-4 lg:grid-cols-2">
          <SectionCard title="AI Summary & Rollout Scope">
            <p className="text-xs leading-relaxed text-slate-300">
              {release.ai_summary || "No AI summary recorded yet. Run Analyze PR to generate one."}
            </p>
            <div className="mt-4 grid grid-cols-2 gap-3 text-xs">
              <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3">
                <div className="text-slate-400">Rollout Progress</div>
                <div className="mt-1 font-mono text-sm font-semibold text-slate-100">
                  {release.current_rollout_percentage}% / {release.target_rollout_percentage}%
                </div>
              </div>
              <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3">
                <div className="text-slate-400">Created</div>
                <div className="mt-1 font-mono text-xs text-slate-200">
                  {formatTimestamp(release.created_at)}
                </div>
              </div>
            </div>
          </SectionCard>

          <SectionCard title="Linked Resources">
            <div className="space-y-2 text-xs">
              <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2">
                <span className="text-slate-400">Pull Request</span>
                {release.pull_request_id ? (
                  <Link href="/pull-requests" className="font-mono text-indigo-300 hover:underline">
                    {release.pull_request_id.slice(0, 8)}...
                  </Link>
                ) : (
                  <span className="text-slate-500">None</span>
                )}
              </div>
              <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2">
                <span className="text-slate-400">Feature Flag</span>
                {release.feature_flag_id ? (
                  <Link href="/feature-flags" className="font-mono text-indigo-300 hover:underline">
                    {release.feature_flag_id.slice(0, 8)}...
                  </Link>
                ) : (
                  <span className="text-slate-500">None</span>
                )}
              </div>
              <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2">
                <span className="text-slate-400">Experiment</span>
                {release.experiment_id ? (
                  <Link href="/experiments" className="font-mono text-indigo-300 hover:underline">
                    {release.experiment_id.slice(0, 8)}...
                  </Link>
                ) : (
                  <span className="text-slate-500">None</span>
                )}
              </div>
              <div className="border-t border-slate-800 pt-2">
                <div className="mb-1.5 text-[11px] font-semibold text-slate-300">
                  Agent Executions ({executions.length})
                </div>
                {executions.length === 0 ? (
                  <span className="text-slate-500">No agent executions linked yet.</span>
                ) : (
                  <div className="space-y-1.5">
                    {executions.map((ex) => (
                      <Link
                        key={ex.id}
                        href={`/command-center/${ex.id}`}
                        className="flex items-center justify-between rounded-lg border border-slate-800 bg-[#0d1119] px-2.5 py-1.5 hover:border-indigo-500/40"
                      >
                        <span className="font-mono text-indigo-300">
                          {ex.execution_key} · {ex.agent_name}
                        </span>
                        <StatusBadge status={ex.status} />
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </SectionCard>
        </div>
      )}

      {activeTab === "approvals" && (
        <div className="space-y-3">
          {approvals.length === 0 ? (
            <SectionCard title="Approvals">
              <p className="text-xs text-slate-400">
                No approvals requested yet for this release.
              </p>
            </SectionCard>
          ) : (
            approvals.map((app) => (
              <ApprovalActionCard key={app.id} approval={app} onUpdated={() => void loadDetail()} />
            ))
          )}
        </div>
      )}

      {activeTab === "artifacts" && (
        <SectionCard title="Release Artifacts">
          {artifacts.length === 0 ? (
            <p className="text-xs text-slate-400">No artifacts recorded yet.</p>
          ) : (
            <div className="space-y-2">
              {artifacts.map((art) => (
                <div
                  key={art.id}
                  className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 text-xs"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-slate-100">{art.title}</span>
                    <span className="rounded border border-slate-700 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                      {art.artifact_type}
                    </span>
                  </div>
                  {art.summary && <p className="mt-1 text-slate-400">{art.summary}</p>}
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      )}

      {activeTab === "timeline" && (
        <SectionCard
          title="Chronological Lifecycle Timeline"
          actions={
            <input
              type="text"
              value={timelineFilter}
              onChange={(e) => setTimelineFilter(e.target.value)}
              placeholder="Filter events..."
              className="rounded-lg border border-slate-700 bg-[#0d1119] px-2.5 py-1 text-xs text-slate-100 placeholder:text-slate-500"
            />
          }
        >
          <div className="space-y-2">
            {filteredEvents.map((ev) => (
              <div
                key={ev.id}
                className="flex flex-wrap items-start justify-between gap-2 rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2 text-xs"
              >
                <div>
                  <div className="font-semibold text-slate-100">{ev.title}</div>
                  {ev.message && <div className="mt-0.5 text-slate-400">{ev.message}</div>}
                </div>
                <div className="text-right font-mono text-[10px] text-slate-500">
                  <div>{ev.event_type}</div>
                  <div>{formatTimestamp(ev.created_at)}</div>
                </div>
              </div>
            ))}
          </div>
        </SectionCard>
      )}
    </div>
  );
}
