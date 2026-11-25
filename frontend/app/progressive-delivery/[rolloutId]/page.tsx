"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import {
  advanceRolloutStage,
  cancelRollout,
  evaluateRolloutStage,
  executeRolloutRollback,
  getErrorMessage,
  getRolloutDetail,
  getRolloutStreamUrl,
  pauseRollout,
  resumeRollout,
  startRollout,
  validateRollout,
} from "@/lib/api";
import type { ProgressiveRolloutDetail } from "@/types/releaseguard";
import { formatTimestamp } from "@/lib/utils";
import {
  ErrorBanner,
  LoadingSkeleton,
  PageHeader,
  SectionCard,
  SuccessBanner,
} from "@/components/ui/StatePrimitives";
import { EnvironmentBadge, RiskLevelBadge, StatusBadge } from "@/components/StatusBadges";

export default function ProgressiveRolloutDetailPage() {
  const params = useParams<{ rolloutId: string }>();
  const rolloutId = params?.rolloutId ?? "";

  const [detail, setDetail] = useState<ProgressiveRolloutDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [validationResult, setValidationResult] = useState<{
    valid: boolean;
    blockers: string[];
  } | null>(null);

  const loadDetail = useCallback(async () => {
    if (!rolloutId) return;
    try {
      const res = await getRolloutDetail(rolloutId);
      setDetail(res);
      setError(null);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [rolloutId]);

  useEffect(() => {
    if (!rolloutId) return;
    let active = true;
    getRolloutDetail(rolloutId)
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
  }, [rolloutId]);

  useEffect(() => {
    if (!rolloutId || typeof EventSource === "undefined") return;
    const source = new EventSource(getRolloutStreamUrl(rolloutId));
    source.onmessage = () => {
      void loadDetail();
    };
    return () => {
      source.close();
    };
  }, [rolloutId, loadDetail]);

  async function runAction(label: string, fn: () => Promise<unknown>, successMsg: string) {
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      await fn();
      await loadDetail();
      setNotice(successMsg);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  if (loading) return <LoadingSkeleton rows={6} />;

  if (!detail) {
    return (
      <div className="space-y-4">
        <ErrorBanner error={error || "Rollout not found"} />
        <Link href="/progressive-delivery" className="text-xs text-indigo-400 hover:underline">
          Back to Progressive Delivery
        </Link>
      </div>
    );
  }

  const { rollout, stages, guardrails, metrics, evaluations, decisions, events, rollbacks } = detail;
  const currentStage = stages.find((s) => s.id === rollout.current_stage_id) ?? stages[0];
  const latestDecision = decisions[0];
  const isTerminal = ["completed", "rolled_back", "cancelled", "failed"].includes(rollout.status);

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <Link href="/progressive-delivery" className="inline-flex items-center gap-1 hover:text-slate-200">
          <ArrowLeft className="h-3.5 w-3.5" />
          Progressive Delivery
        </Link>
        <span>/</span>
        <span className="font-mono text-slate-300">{rollout.rollout_key}</span>
      </div>

      <PageHeader
        title={rollout.name}
        description={`Flag: ${rollout.feature_flag_key} | Primary metric: ${rollout.primary_metric_key}`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={rollout.status} />
            <EnvironmentBadge environment={rollout.environment} />
            <span className="rounded-full border border-slate-700 bg-slate-900 px-3 py-1 font-mono text-xs text-slate-200">
              Exposure: {rollout.current_percentage}% / {rollout.target_percentage}%
            </span>
            <button
              type="button"
              onClick={() => void loadDetail()}
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

      {/* Operator Controls */}
      {!isTerminal && (
        <SectionCard title="Rollout Controls" subtitle="Validate preconditions, evaluate guardrails, advance stages, or trigger rollback">
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy !== null}
              onClick={() =>
                void runAction(
                  "validate",
                  async () => {
                    const res = await validateRollout(rollout.id);
                    setValidationResult(res);
                  },
                  "Rollout preconditions evaluated.",
                )
              }
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800 disabled:opacity-50"
            >
              <ShieldCheck className="h-3.5 w-3.5" />
              Validate Preconditions
            </button>

            {["draft", "ready", "awaiting_approval"].includes(rollout.status) && (
              <button
                type="button"
                disabled={busy !== null}
                onClick={() =>
                  void runAction(
                    "start",
                    () => startRollout(rollout.id),
                    "Rollout start processed.",
                  )
                }
                className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
              >
                <Play className="h-3.5 w-3.5" />
                {busy === "start" ? "Starting..." : "Start Rollout"}
              </button>
            )}

            {currentStage && ["observing", "evaluating", "advance_recommended"].includes(rollout.status) && (
              <button
                type="button"
                disabled={busy !== null}
                onClick={() =>
                  void runAction(
                    "evaluate",
                    () => evaluateRolloutStage(rollout.id, currentStage.id),
                    `Evaluated stage ${currentStage.stage_number} (${currentStage.target_percentage}%).`,
                  )
                }
                className="inline-flex items-center gap-1.5 rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-500 disabled:opacity-50"
              >
                <Activity className="h-3.5 w-3.5" />
                {busy === "evaluate" ? "Evaluating..." : "Evaluate Stage"}
              </button>
            )}

            {currentStage && rollout.status === "advance_recommended" && (
              <button
                type="button"
                disabled={busy !== null}
                onClick={() =>
                  void runAction(
                    "advance",
                    () => advanceRolloutStage(rollout.id, currentStage.id),
                    "Advanced to next rollout stage.",
                  )
                }
                className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                <ArrowRight className="h-3.5 w-3.5" />
                {busy === "advance" ? "Advancing..." : "Advance Stage"}
              </button>
            )}

            {rollout.status === "paused" ? (
              <button
                type="button"
                disabled={busy !== null}
                onClick={() =>
                  void runAction("resume", () => resumeRollout(rollout.id), "Rollout resumed.")
                }
                className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-50"
              >
                <Play className="h-3.5 w-3.5" />
                Resume
              </button>
            ) : (
              ["observing", "evaluating", "advance_recommended"].includes(rollout.status) && (
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() =>
                    void runAction("pause", () => pauseRollout(rollout.id, { reason: "Paused by operator" }), "Rollout paused.")
                  }
                  className="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs font-medium text-amber-300 hover:bg-amber-500/20 disabled:opacity-50"
                >
                  <Pause className="h-3.5 w-3.5" />
                  Pause
                </button>
              )
            )}

            <button
              type="button"
              disabled={busy !== null}
              onClick={() =>
                void runAction(
                  "rollback",
                  () =>
                    executeRolloutRollback(rollout.id, {
                      reason: "Operator triggered rollback",
                    }),
                  "Rollback executed.",
                )
              }
              className="inline-flex items-center gap-1.5 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 text-xs font-semibold text-rose-300 hover:bg-rose-500/20 disabled:opacity-50"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              {busy === "rollback" ? "Rolling back..." : "Execute Rollback"}
            </button>

            <button
              type="button"
              disabled={busy !== null}
              onClick={() =>
                void runAction(
                  "cancel",
                  () => cancelRollout(rollout.id, { reason: "Cancelled by operator" }),
                  "Rollout cancelled.",
                )
              }
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-50"
            >
              <XCircle className="h-3.5 w-3.5" />
              Cancel
            </button>
          </div>

          {validationResult && (
            <div className="mt-3 rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-xs">
              <div className="font-medium text-slate-200">
                Preconditions: {validationResult.valid ? "Passed" : "Blocked"}
              </div>
              {validationResult.blockers.length > 0 && (
                <ul className="mt-1 list-disc pl-5 text-amber-300">
                  {validationResult.blockers.map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </SectionCard>
      )}

      {/* Stage Progression Visualizer */}
      <SectionCard
        title="Stage Progression"
        subtitle="Controlled percentage rollout sequence with observation windows and sample gates"
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
          {stages.map((st) => {
            const isCurrent = st.id === rollout.current_stage_id;
            return (
              <div
                key={st.id}
                className={`rounded-xl border p-3 ${
                  isCurrent
                    ? "border-indigo-500/60 bg-indigo-500/10"
                    : "border-slate-800 bg-slate-900/50"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-slate-400">Stage {st.stage_number}</span>
                  <StatusBadge status={st.status} />
                </div>
                <div className="mt-2 font-mono text-2xl font-bold text-slate-100">
                  {st.target_percentage}%
                </div>
                <div className="mt-2 space-y-0.5 text-[11px] text-slate-400">
                  <div>
                    Samples: {st.actual_sample_size} / {st.minimum_sample_size}
                  </div>
                  <div>Min window: {st.minimum_observation_seconds}s</div>
                  {st.decision && (
                    <div className="pt-1">
                      <StatusBadge status={st.decision} />
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </SectionCard>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Latest Decision */}
        <SectionCard title="Decision Engine" subtitle="Evidence-backed progression or rollback reasoning">
          {latestDecision ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <StatusBadge status={latestDecision.decision_type} />
                <span className="text-xs text-slate-400">
                  Source: {latestDecision.decision_source} | {formatTimestamp(latestDecision.created_at)}
                </span>
              </div>
              <p className="text-sm text-slate-200">{latestDecision.decision_summary}</p>
              {latestDecision.supporting_evidence.length > 0 && (
                <div>
                  <div className="text-xs font-semibold text-emerald-300">Supporting Evidence</div>
                  <ul className="mt-1 space-y-1 text-xs text-slate-300">
                    {latestDecision.supporting_evidence.map((item, idx) => (
                      <li key={idx} className="flex items-start gap-1.5">
                        <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" />
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {latestDecision.missing_evidence.length > 0 && (
                <div>
                  <div className="text-xs font-semibold text-amber-300">Missing / Blocking Evidence</div>
                  <ul className="mt-1 space-y-1 text-xs text-slate-300">
                    {latestDecision.missing_evidence.map((item, idx) => (
                      <li key={idx} className="flex items-start gap-1.5">
                        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-400" />
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <p className="text-xs text-slate-400">No stage evaluations recorded yet. Start or evaluate a stage.</p>
          )}
        </SectionCard>

        {/* Guardrails */}
        <SectionCard title="Configured Guardrails" subtitle="Safety thresholds evaluated during each observation cycle">
          <div className="space-y-2.5">
            {guardrails.map((g) => {
              const latestEval = evaluations.find((ev) => ev.guardrail_id === g.id);
              return (
                <div
                  key={g.id}
                  className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/50 px-3 py-2.5 text-xs"
                >
                  <div>
                    <div className="flex items-center gap-2">
                      <code className="font-semibold text-slate-100">{g.metric_key}</code>
                      <span className="font-mono text-slate-400">
                        {g.operator} {g.threshold}
                      </span>
                      <RiskLevelBadge level={g.severity} />
                    </div>
                    <div className="mt-1 text-[11px] text-slate-400">
                      Min samples: {g.minimum_sample_size} | Action: {g.action}
                      {latestEval && latestEval.observed_value !== null && latestEval.observed_value !== undefined
                        ? ` | Observed: ${latestEval.observed_value.toFixed(4)} (n=${latestEval.sample_size})`
                        : ""}
                    </div>
                  </div>
                  {latestEval ? <StatusBadge status={latestEval.status} /> : <StatusBadge status="pending" />}
                </div>
              );
            })}
          </div>
        </SectionCard>
      </div>

      {/* Metrics & Rollbacks */}
      <div className="grid gap-6 lg:grid-cols-2">
        <SectionCard title="Collected Metric Snapshots" subtitle="Real telemetry snapshots from linked experiment & application events">
          {metrics.length === 0 ? (
            <p className="text-xs text-slate-400">No metric snapshots collected yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="border-b border-slate-800 text-slate-400">
                  <tr>
                    <th className="py-2 pr-3">Metric</th>
                    <th className="py-2 pr-3">Value</th>
                    <th className="py-2 pr-3">Samples</th>
                    <th className="py-2 pr-3">Freshness</th>
                    <th className="py-2">Recorded</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {metrics.slice(0, 12).map((m) => (
                    <tr key={m.id}>
                      <td className="py-2 pr-3 font-mono text-slate-200">{m.metric_key}</td>
                      <td className="py-2 pr-3 font-mono text-slate-100">
                        {m.metric_value.toFixed(4)} {m.unit}
                      </td>
                      <td className="py-2 pr-3 text-slate-300">{m.sample_size}</td>
                      <td className="py-2 pr-3">
                        <StatusBadge status={m.freshness_status === "fresh" ? "pass" : "stale_data"} />
                      </td>
                      <td className="py-2 text-slate-400">{formatTimestamp(m.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </SectionCard>

        <SectionCard title="Rollback Operations" subtitle="Tracked rollback requests, approvals, and verification status">
          {rollbacks.length === 0 ? (
            <p className="text-xs text-slate-400">No rollback operations triggered for this rollout.</p>
          ) : (
            <div className="space-y-2.5">
              {rollbacks.map((rb) => (
                <div key={rb.id} className="rounded-lg border border-slate-800 bg-slate-900/50 p-3 text-xs">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <ShieldAlert className="h-4 w-4 text-rose-400" />
                      <span className="font-semibold text-slate-100">{rb.reason}</span>
                    </div>
                    <StatusBadge status={rb.status} />
                  </div>
                  <div className="mt-1 text-[11px] text-slate-400">
                    Env: {rb.environment} | Created: {formatTimestamp(rb.created_at)}
                    {rb.approval_id ? ` | Approval: ${rb.approval_id}` : ""}
                  </div>
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      </div>

      {/* Audit Timeline */}
      <SectionCard title="Rollout Timeline" subtitle="Chronological audit trail of stage activations, evaluations, and operator actions">
        <div className="space-y-2">
          {events.map((ev) => (
            <div key={ev.id} className="flex items-start justify-between gap-4 border-b border-slate-800/60 pb-2 text-xs last:border-none">
              <div>
                <span className="font-mono text-indigo-300">{ev.event_type}</span>
                <span className="mx-2 text-slate-600">|</span>
                <span className="text-slate-200">{ev.message}</span>
              </div>
              <span className="shrink-0 text-slate-400">{formatTimestamp(ev.created_at)}</span>
            </div>
          ))}
        </div>
      </SectionCard>
    </div>
  );
}
