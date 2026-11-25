"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Clock,
  RefreshCw,
  Rocket,
  Search,
  ShieldAlert,
} from "lucide-react";
import {
  getErrorMessage,
  getReleasesOverview,
  investigateReleaseIncident,
  rollbackFeatureFlag,
  rolloutFeatureFlag,
} from "@/lib/api";
import type {
  ActiveRolloutItem,
  Approval,
  IncidentInvestigationResult,
  ReleasesOverview,
  RolloutStage,
} from "@/types/releaseguard";
import { formatRate, formatTimestamp, isProductionEnv } from "@/lib/utils";
import {
  ApprovalQueuedBanner,
  EmptyState,
  ErrorBanner,
  LoadingSkeleton,
  PageHeader,
  SectionCard,
  SuccessBanner,
} from "@/components/ui/StatePrimitives";
import {
  EnvironmentBadge,
  RecommendationBadge,
  StatusBadge,
} from "@/components/StatusBadges";
import { ReleaseLifecycleList } from "@/components/ReleaseLifecycleList";
import { RolloutStepper } from "@/components/RolloutStepper";
import { ApprovalActionCard } from "@/components/ApprovalActionCard";

export default function ReleasesPage() {
  const [overview, setOverview] = useState<ReleasesOverview | null>(null);
  const [incident, setIncident] = useState<IncidentInvestigationResult | null>(null);

  const [invFlagKey, setInvFlagKey] = useState("");
  const [invEnv, setInvEnv] = useState("development");
  const [invWindow, setInvWindow] = useState(60);

  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [queuedApproval, setQueuedApproval] = useState<Approval | null>(null);

  const loadOverview = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getReleasesOverview();
      setOverview(res);
      setInvFlagKey((prev) => {
        if (prev.trim()) return prev;
        const firstActive = res.active_rollouts?.[0]?.flag_key;
        return firstActive || "";
      });
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      void loadOverview();
    }, 0);
    return () => clearTimeout(timer);
  }, [loadOverview]);

  const handleStage = async (item: ActiveRolloutItem, stage: RolloutStage) => {
    setBusyAction(`stage:${item.feature_flag_id}:${item.environment_name}`);
    setError(null);
    setNotice(null);
    setQueuedApproval(null);
    try {
      const outcome = await rolloutFeatureFlag(item.feature_flag_id, {
        environment_name: item.environment_name,
        target_percentage: stage,
        strategy:
          item.strategy === "multivariate" ? "multivariate" : "percentage_split_segment",
        reason: `Release progression to ${stage}% requested from Releases tracker`,
      });
      if (outcome.execution_status === "approval_required" && outcome.approval) {
        setQueuedApproval(outcome.approval);
      } else {
        setNotice(
          `Updated ${item.flag_key} (${item.environment_name}) to ${stage}% and verified live state.`
        );
        await loadOverview();
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleRollback = async (item: ActiveRolloutItem) => {
    setBusyAction(`rollback:${item.feature_flag_id}:${item.environment_name}`);
    setError(null);
    setNotice(null);
    setQueuedApproval(null);
    try {
      const outcome = await rollbackFeatureFlag(item.feature_flag_id, {
        environment_name: item.environment_name,
        reason: `Release rollback triggered for ${item.flag_key} in ${item.environment_name}`,
      });
      if (outcome.execution_status === "approval_required" && outcome.approval) {
        setQueuedApproval(outcome.approval);
      } else {
        setNotice(
          `Rolled back ${item.flag_key} in ${item.environment_name} to 0% / disabled.`
        );
        await loadOverview();
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleInvestigate = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusyAction("investigate");
    setError(null);
    try {
      const res = await investigateReleaseIncident({
        flag_key: invFlagKey.trim() || undefined,
        environment_name: invEnv.trim() || "development",
        window_minutes: invWindow,
      });
      setIncident(res);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const activeRollouts = overview?.active_rollouts ?? [];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Releases & Incident Investigation"
        description="Track gradual rollout progression (10% → 25% → 50% → 75% → 100%), trigger Category C production rollbacks, and investigate telemetry spikes without conflating correlation with causation."
        actions={
          <button
            type="button"
            onClick={() => void loadOverview()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-medium text-slate-200 hover:bg-slate-800"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh
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
            void loadOverview();
          }}
        />
      )}

      {/* Autonomous Release Lifecycles */}
      <ReleaseLifecycleList />

      {/* Active Gradual Rollouts */}
      <SectionCard
        title="Gradual Rollout Progression Tracker"
        subtitle="Production stage advances and rollbacks strictly queue Category C human approvals"
      >
        {loading ? (
          <LoadingSkeleton rows={3} />
        ) : activeRollouts.length === 0 ? (
          <EmptyState
            icon={<Rocket className="h-5 w-5" />}
            title="No active gradual rollouts"
            description="Sync Feature Flags and initiate a staged rollout (10%–100%) to track live progression here."
          />
        ) : (
          <div className="space-y-4">
            {activeRollouts.map((item) => {
              const prod = isProductionEnv(item.environment_name, item.is_production);
              return (
                <div
                  key={`${item.feature_flag_id}-${item.environment_name}`}
                  className="rounded-xl border border-slate-800 bg-[#0d1119] p-4 space-y-3"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-sm font-semibold text-slate-100">
                        {item.flag_key}
                      </span>
                      <EnvironmentBadge
                        environment={item.environment_name}
                        isProduction={prod}
                      />
                      <StatusBadge status={item.enabled ? "enabled" : "disabled"} />
                      {typeof item.verified === "boolean" && (
                        <StatusBadge
                          status={item.verified ? "verified" : "failed"}
                          label={item.verified ? "Verified" : "Drift"}
                        />
                      )}
                    </div>
                    <RecommendationBadge recommendation={item.latest_recommendation} />
                  </div>

                  <RolloutStepper
                    currentPercentage={item.current_percentage}
                    environmentName={item.environment_name}
                    isProduction={prod}
                    busy={busyAction !== null}
                    onSelectStage={(s) => void handleStage(item, s)}
                    onRollback={() => void handleRollback(item)}
                  />
                </div>
              );
            })}
          </div>
        )}
      </SectionCard>

      {/* Incident Investigation Tool */}
      <SectionCard
        title="Incident Investigation Tool"
        subtitle="Correlates error/abandonment spikes across 5-minute buckets against rollout_history and PR timestamps (±30m)"
      >
        <form onSubmit={handleInvestigate} className="grid gap-3 sm:grid-cols-4">
          <input
            type="text"
            value={invFlagKey}
            onChange={(e) => setInvFlagKey(e.target.value)}
            placeholder="Flag key (optional, e.g. checkout_v2)"
            className="rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 font-mono text-xs text-slate-100"
          />
          <select
            value={invEnv}
            onChange={(e) => setInvEnv(e.target.value)}
            className="rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 text-xs text-slate-200"
          >
            <option value="development">development</option>
            <option value="staging">staging</option>
            <option value="production">production</option>
          </select>
          <select
            value={invWindow}
            onChange={(e) => setInvWindow(Number(e.target.value))}
            className="rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 text-xs text-slate-200"
          >
            <option value={30}>Last 30 minutes</option>
            <option value={60}>Last 60 minutes</option>
            <option value={180}>Last 3 hours</option>
            <option value={1440}>Last 24 hours</option>
          </select>
          <button
            type="submit"
            disabled={busyAction === "investigate"}
            className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-indigo-500 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
          >
            <Search className="h-3.5 w-3.5" />
            {busyAction === "investigate" ? "Investigating..." : "Investigate Incident"}
          </button>
        </form>

        {incident && (
          <div className="mt-5 space-y-5">
            {/* Explicit Causal Disclaimer Contract */}
            <div className="flex items-start gap-3 rounded-xl border border-amber-500/40 bg-amber-500/10 p-4 text-xs text-amber-100">
              <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />
              <div className="space-y-1">
                <div className="flex flex-wrap items-center gap-2 font-semibold">
                  <span>Temporal Correlation ≠ Causal Proof</span>
                  <span className="rounded border border-amber-400/40 bg-amber-500/20 px-1.5 py-0.5 font-mono text-[10px]">
                    correlation_not_causation:{" "}
                    {String(incident.correlation_not_causation)}
                  </span>
                </div>
                <p className="text-amber-200/90 leading-relaxed">
                  {incident.causal_disclaimer ||
                    incident.disclaimer ||
                    "Temporal proximity between a rollout or PR merge and an error spike does not establish causality. Review variation-stratified confounding checks below before deciding on rollback."}
                </p>
              </div>
            </div>

            {/* Observed Metrics by Variation */}
            <div className="grid gap-4 md:grid-cols-2">
              <div className="rounded-xl border border-slate-800 bg-[#0d1119] p-4 space-y-3">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
                  Observed Metrics ({incident.environment_name || invEnv})
                </h4>
                <div className="grid grid-cols-3 gap-2 text-xs tabular-nums">
                  <div className="rounded-lg border border-slate-800 bg-[#111622] p-2.5">
                    <div className="text-slate-400">Exposures</div>
                    <div className="mt-1 font-mono text-base font-semibold text-slate-100">
                      {incident.observed_metrics?.total_exposures ?? 0}
                    </div>
                  </div>
                  <div className="rounded-lg border border-slate-800 bg-[#111622] p-2.5">
                    <div className="text-slate-400">Payment Failed</div>
                    <div className="mt-1 font-mono text-base font-semibold text-rose-300">
                      {incident.observed_metrics?.total_payment_failed ?? 0}
                    </div>
                  </div>
                  <div className="rounded-lg border border-slate-800 bg-[#111622] p-2.5">
                    <div className="text-slate-400">Error Rate</div>
                    <div className="mt-1 font-mono text-base font-semibold text-amber-300">
                      {formatRate(
                        incident.observed_metrics?.overall_payment_failed_rate ??
                          incident.observed_metrics?.overall_error_rate ??
                          0,
                        2
                      )}
                    </div>
                  </div>
                </div>

                {incident.observed_metrics?.by_variation && (
                  <div className="space-y-1.5">
                    <div className="text-[11px] font-medium text-slate-400">
                      Stratified by Variation
                    </div>
                    {Object.entries(incident.observed_metrics.by_variation).map(
                      ([variation, stats]) => (
                        <div
                          key={variation}
                          className="flex items-center justify-between rounded border border-slate-800 bg-[#111622] px-3 py-2 font-mono text-xs tabular-nums"
                        >
                          <span className="text-cyan-300">{variation}</span>
                          <span className="text-slate-300">
                            {stats.payment_failed}/{stats.exposures} errors (
                            {formatRate(
                              stats.payment_failed_rate ?? stats.error_rate ?? 0,
                              2
                            )}
                            )
                          </span>
                        </div>
                      )
                    )}
                  </div>
                )}
              </div>

              {/* Confounding Checks */}
              <div className="rounded-xl border border-slate-800 bg-[#0d1119] p-4 space-y-3">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
                  Confounding Checks
                </h4>
                {!incident.confounding_checks?.length ? (
                  <p className="text-xs text-slate-400">
                    No confounding checks returned.
                  </p>
                ) : (
                  <div className="space-y-2">
                    {incident.confounding_checks.map((chk, idx) => {
                      const isConfounder = Boolean(
                        chk.confounder_detected ?? chk.likely_confounder
                      );
                      return (
                        <div
                          key={idx}
                          className="rounded-lg border border-slate-800 bg-[#111622] p-3 text-xs space-y-1"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-semibold text-slate-100">
                              {chk.check_name}
                            </span>
                            <StatusBadge
                              status={isConfounder ? "pending" : "completed"}
                              label={
                                chk.status ||
                                (isConfounder ? "Confounder Likely" : "Isolated")
                              }
                            />
                          </div>
                          <p className="text-slate-300">
                            {chk.summary || chk.finding}
                          </p>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>

            {/* Temporal Correlations */}
            <div className="rounded-xl border border-slate-800 bg-[#0d1119] p-4 space-y-3">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
                Temporal Correlations (±30m Window)
              </h4>
              {!incident.temporal_correlations?.length ? (
                <p className="text-xs text-slate-400">
                  No rollout or PR events found within ±30 minutes of error activity.
                </p>
              ) : (
                <div className="space-y-2">
                  {incident.temporal_correlations.map((corr, idx) => {
                    const ts = corr.timestamp || corr.occurred_at;
                    const delta =
                      corr.delta_minutes_from_onset ??
                      corr.delta_minutes ??
                      corr.minutes_from_spike;
                    const score = corr.temporal_score ?? corr.correlation_score ?? 0;
                    const kind = corr.source_type || corr.event_type || "event";
                    return (
                      <div
                        key={idx}
                        className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-800 bg-[#111622] px-3.5 py-2.5 text-xs"
                      >
                        <div className="space-y-1">
                          <div className="flex items-center gap-2">
                            <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />
                            <span className="font-semibold text-slate-100">
                              {corr.title || corr.summary || kind}
                            </span>
                            <span className="rounded border border-slate-700 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                              {kind}
                            </span>
                          </div>
                          <div className="flex items-center gap-3 text-[11px] text-slate-400">
                            <span className="inline-flex items-center gap-1 tabular-nums">
                              <Clock className="h-3 w-3" />
                              {formatTimestamp(ts)}
                            </span>
                            {typeof delta === "number" && (
                              <span className="font-mono tabular-nums">
                                Δ {delta.toFixed(1)}m from spike
                              </span>
                            )}
                          </div>
                        </div>
                        <span className="rounded border border-amber-500/35 bg-amber-500/10 px-2 py-1 font-mono text-xs font-semibold text-amber-200 tabular-nums">
                          Score: {score.toFixed(2)} (not causal)
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}

              {incident.recommended_next_steps?.length > 0 && (
                <div className="border-t border-slate-800 pt-3">
                  <div className="mb-1.5 text-xs font-semibold text-slate-300">
                    Recommended Next Steps
                  </div>
                  <ul className="list-disc space-y-1 pl-5 text-xs text-slate-300">
                    {incident.recommended_next_steps.map((step, i) => (
                      <li key={i}>{step}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        )}
      </SectionCard>
    </div>
  );
}
