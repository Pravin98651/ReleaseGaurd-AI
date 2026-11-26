"use client";

import React, { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  Bot,
  FlaskConical,
  HelpCircle,
  Hourglass,
  Info,
  Play,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  Square,
  Trophy,
} from "lucide-react";
import {
  activateExperiment,
  getErrorMessage,
  getExperimentAnalysis,
  getExperimentRecommendation,
  getSystemHealth,
  listExperiments,
  listFeatureFlags,
  planExperiment,
  promoteExperimentWinner,
  stopExperiment,
} from "@/lib/api";
import type {
  Approval,
  Experiment,
  ExperimentAnalysis,
  ExperimentRecommendation,
  FeatureFlag,
  SystemHealth,
} from "@/types/releaseguard";
import { cn, formatRate, isProductionEnv } from "@/lib/utils";
import {
  ApprovalQueuedBanner,
  EmptyState,
  ErrorBanner,
  LoadingSkeleton,
  MetricCard,
  PageHeader,
  SectionCard,
  SuccessBanner,
} from "@/components/ui/StatePrimitives";
import {
  EnvironmentBadge,
  RecommendationBadge,
  StatusBadge,
} from "@/components/StatusBadges";
import { PolicyGateBadge } from "@/components/PolicyGateBadge";
import {
  BayesianPosteriorChart,
  ConversionGuardrailChart,
} from "@/components/BayesianCharts";
import { ApprovalActionCard } from "@/components/ApprovalActionCard";

function ExperimentsInner() {
  const searchParams = useSearchParams();
  const initialExpId = searchParams.get("id") ?? "";

  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [flags, setFlags] = useState<FeatureFlag[]>([]);
  const [selectedExp, setSelectedExp] = useState<Experiment | null>(null);
  const [analysis, setAnalysis] = useState<ExperimentAnalysis | null>(null);
  const [recommendation, setRecommendation] =
    useState<ExperimentRecommendation | null>(null);

  // NL Experiment Planner form
  const [planPrompt, setPlanPrompt] = useState("");
  const [planFlagId, setPlanFlagId] = useState("");
  const [planEnv, setPlanEnv] = useState("development");

  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [staleWarning, setStaleWarning] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [queuedApproval, setQueuedApproval] = useState<Approval | null>(null);

  const loadEvaluation = useCallback(
    async (exp: Experiment) => {
      const switchingExperiment = selectedExp?.id !== exp.id;
      setSelectedExp(exp);
      if (switchingExperiment) {
        setAnalysis(exp.latest_bayesian_result ?? null);
        setRecommendation(null);
        setStaleWarning(null);
      }
      try {
        const [ana, rec] = await Promise.all([
          getExperimentAnalysis(exp.id),
          getExperimentRecommendation(exp.id),
        ]);
        setAnalysis(ana);
        setRecommendation(rec);
        setStaleWarning(null);
      } catch (err) {
        // Surface real error if analysis fails rather than fabricating numbers,
        // and preserve stale warning when previously loaded evaluation remains on screen.
        const msg = getErrorMessage(err);
        setError(msg);
        if (!switchingExperiment) {
          setStaleWarning(
            `Refresh failed (${msg}). Showing previously loaded evaluation — results may be stale.`
          );
        }
      }
    },
    [selectedExp?.id]
  );

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [h, expRes, flagRes] = await Promise.all([
        getSystemHealth().catch(() => null),
        listExperiments(),
        listFeatureFlags({ limit: 100 }).catch(() => ({
          items: [],
          total: 0,
          limit: 100,
          offset: 0,
        })),
      ]);
      setHealth(h);
      setExperiments(expRes.items);
      setFlags(flagRes.items);
      if (expRes.items.length > 0) {
        const target =
          expRes.items.find((e) => e.id === (selectedExp?.id || initialExpId)) ??
          expRes.items[0];
        await loadEvaluation(target);
      } else {
        setStaleWarning(null);
      }
    } catch (err) {
      const msg = getErrorMessage(err);
      setError(msg);
      if (experiments.length > 0 || analysis) {
        setStaleWarning(
          `Refresh failed (${msg}). Showing previously loaded experiment data — results may be stale.`
        );
      }
    } finally {
      setLoading(false);
    }
  }, [analysis, experiments.length, initialExpId, loadEvaluation, selectedExp?.id]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void loadAll();
    }, 0);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handlePlan = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!planPrompt.trim()) return;
    setBusyAction("plan");
    setError(null);
    setNotice(null);
    try {
      const created = await planExperiment({
        prompt: planPrompt.trim(),
        feature_flag_id: planFlagId || undefined,
        environment_name: planEnv || undefined,
      });
      setPlanPrompt("");
      setExperiments((prev) => [created, ...prev]);
      setNotice(`Drafted experiment plan '${created.name}'.`);
      await loadEvaluation(created);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleActivate = async (exp: Experiment) => {
    setBusyAction("activate");
    setError(null);
    setNotice(null);
    setQueuedApproval(null);
    try {
      const outcome = await activateExperiment(exp.id, {
        environment_name: exp.environment_name,
      });
      if (outcome.execution_status === "approval_required" && outcome.approval) {
        setQueuedApproval(outcome.approval);
      } else {
        setNotice(`Activated experiment '${exp.name}' in ${exp.environment_name}.`);
        await loadAll();
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handlePromoteWinner = async (exp: Experiment) => {
    setBusyAction("promote");
    setError(null);
    setNotice(null);
    setQueuedApproval(null);
    try {
      const outcome = await promoteExperimentWinner(exp.id, exp.treatment_variation);
      if (outcome.execution_status === "approval_required" && outcome.approval) {
        setQueuedApproval(outcome.approval);
        setNotice(
          `Approval requested to promote '${exp.treatment_variation}' on Flagsmith.`
        );
      } else {
        setNotice(`Promoted winning variant for '${exp.name}'.`);
        await loadAll();
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleStop = async (exp: Experiment) => {
    setBusyAction("stop");
    setError(null);
    setNotice(null);
    setQueuedApproval(null);
    try {
      const outcome = await stopExperiment(exp.id, {
        reason: "Stopped by operator from Experiments dashboard",
      });
      if (outcome.execution_status === "approval_required" && outcome.approval) {
        setQueuedApproval(outcome.approval);
      } else {
        setNotice(`Stopped experiment '${exp.name}'.`);
        await loadAll();
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const activeAnalyticsMode =
    analysis?.analytics_mode ||
    selectedExp?.analytics_mode ||
    health?.analytics_mode ||
    "tracked_event_fallback";
  const isNative = activeAnalyticsMode === "flagsmith_native";

  return (
    <div className="space-y-6">
      <PageHeader
        title="Experiments & Bayesian Analytics"
        description="Natural-language experiment planning paired with deterministic Python Beta-Binomial posteriors, credible intervals, expected loss, and guardrail monitoring."
        actions={
          selectedExp && (
            <button
              type="button"
              onClick={() => void loadEvaluation(selectedExp)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-medium text-slate-200 hover:bg-slate-800"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Recompute Bayesian Posterior
            </button>
          )
        }
      />

      {/* Prominent Capability Banner */}
      <div
        className={cn(
          "flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-3 text-xs",
          isNative
            ? "border-emerald-500/35 bg-emerald-500/10 text-emerald-100"
            : "border-cyan-500/35 bg-cyan-500/10 text-cyan-100"
        )}
      >
        <div className="flex items-start gap-2.5">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-cyan-300" />
          <div>
            <div className="font-semibold">
              Analytics Capability Mode:{" "}
              <span className="font-mono uppercase">{activeAnalyticsMode}</span>
            </div>
            <p className="mt-0.5 text-slate-300">
              {isNative
                ? "Flagsmith Native Warehouse Experiments enabled (ClickHouse/Databricks detected)."
                : "Flagsmith Native Experiments require an enterprise warehouse plan. Running the verified Tracked-Events Fallback Layer over real ingested checkout telemetry in ReleaseGuard DB."}
            </p>
          </div>
        </div>
        <Link
          href="/settings"
          className="rounded-lg border border-slate-700 bg-[#0d1119] px-3 py-1.5 font-medium text-slate-200 hover:border-slate-500"
        >
          Probe Capability in Settings
        </Link>
      </div>

      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {staleWarning && (
        <div
          role="status"
          className="flex items-center justify-between gap-3 rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-2.5 text-xs text-amber-200"
        >
          <span>{staleWarning}</span>
          <button
            type="button"
            onClick={() => setStaleWarning(null)}
            className="shrink-0 rounded border border-amber-500/30 px-2 py-0.5 text-[11px] font-medium text-amber-200 hover:bg-amber-500/20"
          >
            Dismiss
          </button>
        </div>
      )}
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
            void loadAll();
          }}
        />
      )}

      {/* NL Experiment Planner */}
      <SectionCard
        title="Natural-Language Experiment Planner"
        subtitle="Category A: experiment_agent translates plain English into a structured hypothesis, traffic split, and guardrail thresholds"
        actions={<PolicyGateBadge category="A" compact />}
      >
        <form onSubmit={handlePlan} className="grid gap-3 md:grid-cols-12">
          <input
            type="text"
            required
            value={planPrompt}
            onChange={(e) => setPlanPrompt(e.target.value)}
            placeholder='e.g. "Test checkout_v2 vs checkout_v1 50/50 in development optimizing checkout_completed with payment_failed < 5%"'
            className="md:col-span-6 rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
          />
          <select
            value={planFlagId}
            onChange={(e) => setPlanFlagId(e.target.value)}
            className="md:col-span-2 rounded-lg border border-slate-700 bg-[#090c12] px-2.5 py-2 text-xs text-slate-200"
          >
            <option value="">Auto-detect flag</option>
            {flags.map((f) => (
              <option key={f.id} value={f.id}>
                {f.key}
              </option>
            ))}
          </select>
          <select
            value={planEnv}
            onChange={(e) => setPlanEnv(e.target.value)}
            className="md:col-span-2 rounded-lg border border-slate-700 bg-[#090c12] px-2.5 py-2 text-xs text-slate-200"
          >
            <option value="development">development</option>
            <option value="staging">staging</option>
            <option value="production">production</option>
          </select>
          <button
            type="submit"
            disabled={busyAction !== null || !planPrompt.trim()}
            className="md:col-span-2 inline-flex items-center justify-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
          >
            <Sparkles className="h-3.5 w-3.5" />
            {busyAction === "plan" ? "Planning..." : "Plan Experiment"}
          </button>
        </form>
      </SectionCard>

      {/* Experiments List + Bayesian Dashboard */}
      <div className="grid gap-6 lg:grid-cols-12">
        <SectionCard
          className="lg:col-span-4"
          title="Experiments"
          subtitle={`${experiments.length} defined`}
        >
          {loading ? (
            <LoadingSkeleton rows={3} />
          ) : experiments.length === 0 ? (
            <EmptyState
              icon={<FlaskConical className="h-5 w-5" />}
              title="No experiments created"
              description="Use the NL Experiment Planner above to draft an experiment."
            />
          ) : (
            <div className="space-y-2">
              {experiments.map((exp) => (
                <button
                  key={exp.id}
                  type="button"
                  onClick={() => void loadEvaluation(exp)}
                  className={cn(
                    "w-full rounded-xl border p-3 text-left transition-colors space-y-2",
                    selectedExp?.id === exp.id
                      ? "border-indigo-500/45 bg-indigo-500/10"
                      : "border-slate-800 bg-[#0d1119] hover:border-slate-700"
                  )}
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-xs font-semibold text-slate-100">
                      {exp.name}
                    </span>
                    <StatusBadge status={exp.status} />
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <EnvironmentBadge environment={exp.environment_name} />
                    <RecommendationBadge recommendation={exp.latest_recommendation} />
                  </div>
                </button>
              ))}
            </div>
          )}
        </SectionCard>

        <div className="space-y-6 lg:col-span-8">
          {!selectedExp ? (
            <SectionCard title="Bayesian Experiment Evaluation">
              <EmptyState
                icon={<FlaskConical className="h-5 w-5" />}
                title="Select an experiment"
                description="Choose an experiment to inspect Bayesian Beta-Binomial posteriors, guardrails, and deterministic release recommendations."
              />
            </SectionCard>
          ) : (
            <>
              {/* Header + Controls */}
              <SectionCard
                title={selectedExp.name}
                subtitle={selectedExp.hypothesis}
                actions={
                  <div className="flex flex-wrap items-center gap-2">
                    <PolicyGateBadge
                      category={
                        isProductionEnv(selectedExp.environment_name) ? "C" : "B"
                      }
                      compact
                    />
                    <StatusBadge status={selectedExp.status} />
                    {selectedExp.status !== "running" && (
                      <button
                        type="button"
                        disabled={busyAction !== null}
                        onClick={() => void handleActivate(selectedExp)}
                        className="inline-flex items-center gap-1 rounded-lg bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-slate-950 hover:bg-emerald-400 disabled:opacity-50"
                      >
                        <Play className="h-3.5 w-3.5" />
                        {busyAction === "activate" ? "Activating..." : "Activate"}
                      </button>
                    )}
                    {selectedExp.status === "running" && (
                      <button
                        type="button"
                        disabled={busyAction !== null}
                        onClick={() => void handleStop(selectedExp)}
                        className="inline-flex items-center gap-1 rounded-lg border border-rose-500/40 bg-rose-500/15 px-3 py-1.5 text-xs font-semibold text-rose-200 hover:bg-rose-500/25 disabled:opacity-50"
                      >
                        <Square className="h-3.5 w-3.5" />
                        {busyAction === "stop" ? "Stopping..." : "Stop Experiment"}
                      </button>
                    )}
                    <button
                      type="button"
                      disabled={
                        busyAction !== null ||
                        analysis?.experiment_outcome === "GUARDRAIL_BREACHED" ||
                        Boolean(analysis?.readiness && !analysis.readiness.is_ready_for_winner_decision)
                      }
                      title={
                        analysis?.experiment_outcome === "GUARDRAIL_BREACHED"
                          ? "Blocked: mandatory guardrail breached"
                          : analysis?.readiness && !analysis.readiness.is_ready_for_winner_decision
                            ? "Blocked: experiment readiness criteria not met"
                            : "Queue Category C human approval to promote winner"
                      }
                      onClick={() => void handlePromoteWinner(selectedExp)}
                      className="inline-flex items-center gap-1 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
                    >
                      {busyAction === "promote"
                        ? "Requesting Approval..."
                        : "Promote Winner (Requires Approval)"}
                    </button>
                  </div>
                }
              >
                <div className="flex flex-wrap items-center gap-3 text-xs text-slate-300">
                  <EnvironmentBadge environment={selectedExp.environment_name} />
                  {selectedExp.environment_name !== "production" ? (
                    <span className="inline-flex items-center gap-1 rounded-full border border-amber-500/40 bg-amber-500/15 px-2.5 py-0.5 text-[11px] font-semibold text-amber-200">
                      Synthetic / Non-Prod Traffic
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/35 bg-emerald-500/10 px-2.5 py-0.5 text-[11px] font-semibold text-emerald-300">
                      Production Telemetry
                    </span>
                  )}
                  {selectedExp.flagsmith_experiment_id ? (
                    <span className="rounded border border-emerald-500/40 bg-emerald-500/15 px-2 py-0.5 font-mono text-[11px] text-emerald-300">
                      Flagsmith Exp #{selectedExp.flagsmith_experiment_id}
                    </span>
                  ) : (
                    <span className="rounded border border-cyan-500/35 bg-cyan-500/10 px-2 py-0.5 font-mono text-[11px] text-cyan-200">
                      Tracked-Events Fallback
                    </span>
                  )}
                  <span className="font-mono">
                    Control: {selectedExp.control_variation} (
                    {selectedExp.traffic_allocation?.[selectedExp.control_variation] ?? 50}
                    %)
                  </span>
                  <span className="font-mono">
                    Variant: {selectedExp.treatment_variation} (
                    {selectedExp.traffic_allocation?.[selectedExp.treatment_variation] ??
                      50}
                    %)
                  </span>
                  <span className="font-mono text-cyan-300">
                    Metric: {selectedExp.primary_metric}
                  </span>
                </div>
              </SectionCard>

              {/* Deterministic Recommendation vs AI Narrative */}
              {(recommendation || selectedExp.latest_recommendation) && (
                <SectionCard
                  title="Release Recommendation Engine"
                  subtitle="Deterministic 5-state evaluator (math/rules only) separated from Gemini AI narrative"
                  actions={
                    <RecommendationBadge
                      recommendation={
                        recommendation?.recommendation ??
                        selectedExp.latest_recommendation
                      }
                    />
                  }
                >
                  <div className="space-y-3 text-xs">
                    {Array.isArray(recommendation?.triggers) &&
                      recommendation.triggers.length > 0 && (
                        <div className="space-y-1.5">
                          <div className="text-[10px] font-semibold uppercase tracking-wider text-cyan-300">
                            Deterministic Rule Triggers
                          </div>
                          <div className="flex flex-wrap gap-1.5">
                            {recommendation.triggers.map((t, i) => (
                              <span
                                key={i}
                                className="rounded border border-slate-700 bg-slate-900 px-2 py-0.5 font-mono text-[11px] text-cyan-200"
                              >
                                {typeof t === "string"
                                  ? t
                                  : t.description || `${t.metric} ${t.operator} ${t.threshold}`}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                    {(recommendation?.narrative ||
                      recommendation?.rationale ||
                      selectedExp.recommendation_rationale) && (
                      <div className="rounded-lg border border-indigo-500/30 bg-[#0d1119] p-3 space-y-1.5">
                        <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-indigo-300">
                          <Bot className="h-3.5 w-3.5" />
                          AI Recommendation Narrative (Gemini Inference)
                        </div>
                        <p className="leading-relaxed text-slate-200">
                          {recommendation?.narrative ||
                            recommendation?.rationale ||
                            selectedExp.recommendation_rationale}
                        </p>
                      </div>
                    )}
                    {recommendation?.confidence_summary && (
                      <div className="text-slate-300">
                        <span className="font-semibold text-slate-200">
                          Confidence / Uncertainty:{" "}
                        </span>
                        {recommendation.confidence_summary}
                      </div>
                    )}
                  </div>
                </SectionCard>
              )}

              {/* Bayesian Metrics & Charts */}
              {analysis && (
                <>
                  {/* Unmistakably Distinct Experiment Outcome & Sample Adequacy Banner */}
                  {(() => {
                    const isGuardrailBreached =
                      analysis.experiment_outcome === "GUARDRAIL_BREACHED" ||
                      Boolean(analysis.guardrails?.payment_failed?.breached);
                    const isInsufficient =
                      !isGuardrailBreached &&
                      (!analysis.sample_adequacy.is_adequate ||
                        analysis.experiment_outcome === "INSUFFICIENT_DATA");
                    const isWinner =
                      !isGuardrailBreached &&
                      !isInsufficient &&
                      (analysis.experiment_outcome === "VARIANT_WINS" ||
                        analysis.experiment_outcome === "CONTROL_WINS");

                    const minReq = Math.max(1, analysis.sample_adequacy.min_required);
                    const ctrlPct = Math.min(
                      100,
                      Math.round((analysis.sample_adequacy.control_exposures / minReq) * 100)
                    );
                    const trtPct = Math.min(
                      100,
                      Math.round((analysis.sample_adequacy.treatment_exposures / minReq) * 100)
                    );

                    const bannerStyle = isGuardrailBreached
                      ? "border-2 border-rose-500/70 bg-rose-950/30 text-rose-100"
                      : isInsufficient
                        ? "border-2 border-dashed border-amber-500/60 bg-amber-950/20 text-amber-100"
                        : isWinner
                          ? "border-2 border-emerald-500/60 bg-emerald-950/20 text-emerald-100"
                          : "border-2 border-sky-500/50 bg-slate-900/80 text-slate-100";

                    const verdictTitle = isGuardrailBreached
                      ? "GUARDRAIL BREACHED — DO NOT PROMOTE / ROLLBACK RECOMMENDED"
                      : isInsufficient
                        ? "INSUFFICIENT DATA — STATISTICAL POWER NOT MET (DO NOT SHIP)"
                        : isWinner
                          ? analysis.experiment_outcome === "CONTROL_WINS"
                            ? "CONTROL WINS — VARIANT UNDERPERFORMS"
                            : "WINNING VARIANT DETECTED — READY FOR PROMOTION REVIEW"
                          : "INCONCLUSIVE — NO CLEAR WINNER YET";

                    return (
                      <div className={cn("rounded-xl p-4 text-xs space-y-3", bannerStyle)}>
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div className="flex items-start gap-3">
                            {isGuardrailBreached ? (
                              <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-rose-400" />
                            ) : isInsufficient ? (
                              <Hourglass className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />
                            ) : isWinner ? (
                              <Trophy className="mt-0.5 h-5 w-5 shrink-0 text-emerald-400" />
                            ) : (
                              <HelpCircle className="mt-0.5 h-5 w-5 shrink-0 text-sky-400" />
                            )}
                            <div className="space-y-1">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="font-mono text-sm font-bold tracking-tight uppercase">
                                  {verdictTitle}
                                </span>
                                <span className="rounded border border-current/30 px-2 py-0.5 font-mono text-[10px] uppercase">
                                  {analysis.experiment_outcome ??
                                    (isInsufficient ? "INSUFFICIENT_DATA" : "INCONCLUSIVE")}
                                </span>
                              </div>
                              <p className="text-slate-300 leading-relaxed">
                                {analysis.outcome_rationale ||
                                  (isInsufficient
                                    ? `Minimum sample size of ${minReq} exposures per arm has not been reached. Continue collecting telemetry before making a rollout decision.`
                                    : `Control: ${analysis.sample_adequacy.control_exposures}, Treatment: ${analysis.sample_adequacy.treatment_exposures} (min ${minReq}/arm)`)}
                              </p>
                            </div>
                          </div>

                          <div className="flex flex-wrap items-center gap-2">
                            <span
                              className={cn(
                                "rounded-full border px-2.5 py-0.5 font-mono text-[11px] font-semibold",
                                analysis.sample_adequacy.is_adequate
                                  ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-300"
                                  : "border-amber-500/40 bg-amber-500/15 text-amber-200"
                              )}
                            >
                              Sample Adequacy:{" "}
                              {analysis.sample_adequacy.is_adequate ? "ADEQUATE" : "INADEQUATE"}
                            </span>
                            {analysis.flagsmith_native_summary && (
                              <span className="rounded border border-slate-700 bg-slate-900 px-2 py-0.5 font-mono text-[11px] text-slate-300">
                                Flagsmith Native:{" "}
                                {analysis.flagsmith_native_summary.available
                                  ? analysis.flagsmith_native_summary.is_final
                                    ? "Final"
                                    : "Aggregating (is_final=false)"
                                  : "Unavailable"}
                              </span>
                            )}
                          </div>
                        </div>

                        {/* Sample Adequacy Progress Bars */}
                        <div className="grid gap-3 sm:grid-cols-2 pt-1">
                          <div className="space-y-1">
                            <div className="flex justify-between font-mono text-[11px] text-slate-300">
                              <span>Control ({selectedExp.control_variation})</span>
                              <span>
                                {analysis.sample_adequacy.control_exposures} / {minReq} min (
                                {ctrlPct}%)
                              </span>
                            </div>
                            <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                              <div
                                className={cn(
                                  "h-full rounded-full transition-all",
                                  ctrlPct >= 100 ? "bg-emerald-400" : "bg-amber-400"
                                )}
                                style={{ width: `${ctrlPct}%` }}
                              />
                            </div>
                          </div>
                          <div className="space-y-1">
                            <div className="flex justify-between font-mono text-[11px] text-slate-300">
                              <span>Variant ({selectedExp.treatment_variation})</span>
                              <span>
                                {analysis.sample_adequacy.treatment_exposures} / {minReq} min (
                                {trtPct}%)
                              </span>
                            </div>
                            <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                              <div
                                className={cn(
                                  "h-full rounded-full transition-all",
                                  trtPct >= 100 ? "bg-emerald-400" : "bg-amber-400"
                                )}
                                style={{ width: `${trtPct}%` }}
                              />
                            </div>
                          </div>
                        </div>
                      </div>
                    );
                  })()}

                  {/* Distinct Participants, Exposures, and Conversions */}
                  {analysis.participant_metrics && (
                    <div className="grid gap-3 sm:grid-cols-4 text-xs tabular-nums">
                      <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3">
                        <div className="text-slate-400">Unique Participants</div>
                        <div className="mt-1 font-mono font-semibold text-slate-100">
                          C: {analysis.participant_metrics.control_unique_participants} / T:{" "}
                          {analysis.participant_metrics.treatment_unique_participants}
                        </div>
                      </div>
                      <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3">
                        <div className="text-slate-400">Raw Exposures (Deduped: {analysis.participant_metrics.deduplicated_events})</div>
                        <div className="mt-1 font-mono font-semibold text-slate-100">
                          C: {analysis.participant_metrics.control_raw_exposures} / T:{" "}
                          {analysis.participant_metrics.treatment_raw_exposures}
                        </div>
                      </div>
                      <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3">
                        <div className="text-slate-400">Total vs Unique Conversions</div>
                        <div className="mt-1 font-mono font-semibold text-slate-100">
                          C: {analysis.participant_metrics.control_total_conversions} ({analysis.participant_metrics.control_unique_conversions}u) · T:{" "}
                          {analysis.participant_metrics.treatment_total_conversions} ({analysis.participant_metrics.treatment_unique_conversions}u)
                        </div>
                      </div>
                      <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3">
                        <div className="text-slate-400">Observed Conv. Rate</div>
                        <div className="mt-1 font-mono font-semibold text-slate-100">
                          C: {(analysis.participant_metrics.control_conversion_rate * 100).toFixed(2)}% / T:{" "}
                          {(analysis.participant_metrics.treatment_conversion_rate * 100).toFixed(2)}%
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Key Bayesian KPIs */}
                  <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                    <MetricCard
                      label="P(Treatment > Control)"
                      value={`${(
                        analysis.comparison.prob_treatment_beats_control * 100
                      ).toFixed(2)}%`}
                      subtext={`Rel lift: ${(
                        analysis.comparison.relative_lift_mean * 100
                      ).toFixed(1)}%${analysis.frequentist ? ` · Freq p=${analysis.frequentist.p_value.toFixed(4)}` : ""}`}
                      tone={
                        analysis.comparison.prob_treatment_beats_control >= 0.9
                          ? "emerald"
                          : analysis.comparison.prob_treatment_beats_control < 0.25
                            ? "rose"
                            : "default"
                      }
                    />
                    <MetricCard
                      label={`Control (${selectedExp.control_variation})`}
                      value={formatRate(analysis.control.mean_rate, 2)}
                      subtext={`${analysis.control.conversions}/${analysis.control.exposures} · 95% CrI [${formatRate(analysis.control.credible_interval_95[0], 1)}, ${formatRate(analysis.control.credible_interval_95[1], 1)}]`}
                      tone="sky"
                    />
                    <MetricCard
                      label={`Treatment (${selectedExp.treatment_variation})`}
                      value={formatRate(analysis.treatment.mean_rate, 2)}
                      subtext={`${analysis.treatment.conversions}/${analysis.treatment.exposures} · 95% CrI [${formatRate(analysis.treatment.credible_interval_95[0], 1)}, ${formatRate(analysis.treatment.credible_interval_95[1], 1)}]`}
                      tone={
                        analysis.experiment_outcome === "GUARDRAIL_BREACHED" ||
                        Boolean(analysis.guardrails?.payment_failed?.breached) ||
                        analysis.experiment_outcome === "CONTROL_WINS"
                          ? "rose"
                          : analysis.experiment_outcome === "VARIANT_WINS" &&
                              analysis.sample_adequacy.is_adequate
                            ? "emerald"
                            : "default"
                      }
                    />
                    <MetricCard
                      label="Expected Loss (Treatment)"
                      value={formatRate(
                        analysis.comparison.expected_loss_choose_treatment,
                        3
                      )}
                      subtext={`Loss(Control): ${formatRate(
                        analysis.comparison.expected_loss_choose_control,
                        3
                      )}`}
                    />
                  </div>

                  {/* Experiment Readiness Checklist */}
                  {analysis.readiness && (
                    <SectionCard
                      title="Experiment Readiness Gate"
                      subtitle="All criteria must pass before recommending or promoting a winner"
                      actions={
                        <StatusBadge
                          status={analysis.readiness.is_ready_for_winner_decision ? "passed" : "warning"}
                          label={analysis.readiness.is_ready_for_winner_decision ? "Ready" : "Not Ready"}
                        />
                      }
                    >
                      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 text-xs">
                        {analysis.readiness.checks.map((chk) => (
                          <div
                            key={chk.key}
                            className={cn(
                              "rounded-lg border p-2.5",
                              chk.passed
                                ? "border-emerald-500/30 bg-emerald-500/5"
                                : "border-amber-500/30 bg-amber-500/5"
                            )}
                          >
                            <div className="flex items-center justify-between font-medium text-slate-200">
                              <span>{chk.label}</span>
                              <span className={chk.passed ? "text-emerald-400" : "text-amber-400"}>
                                {chk.passed ? "PASS" : "BLOCK"}
                              </span>
                            </div>
                            <div className="mt-1 font-mono text-[11px] text-slate-400">{chk.detail}</div>
                          </div>
                        ))}
                      </div>
                    </SectionCard>
                  )}

                  {/* Recharts */}
                  <div className="grid gap-6 xl:grid-cols-2">
                    <BayesianPosteriorChart
                      analysis={analysis}
                      controlLabel={selectedExp.control_variation}
                      treatmentLabel={selectedExp.treatment_variation}
                    />
                    <ConversionGuardrailChart
                      analysis={analysis}
                      controlLabel={selectedExp.control_variation}
                      treatmentLabel={selectedExp.treatment_variation}
                      maxErrorCeilingPct={
                        (selectedExp.guardrail_thresholds?.guardrail_max_error_rate ??
                          0.05) * 100
                      }
                    />
                  </div>

                  {/* Guardrail Breakdown Table */}
                  {analysis.guardrails?.payment_failed && (
                    <SectionCard
                      title="Guardrail Posterior Details (payment_failed)"
                      actions={
                        <StatusBadge
                          status={
                            analysis.guardrails.payment_failed.breached
                              ? "failed"
                              : "passed"
                          }
                          label={
                            analysis.guardrails.payment_failed.breached
                              ? "Guardrail Breached"
                              : "Within Guardrails"
                          }
                        />
                      }
                    >
                      <div className="grid gap-3 sm:grid-cols-4 text-xs tabular-nums">
                        <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3">
                          <div className="text-slate-400">Control Error Rate</div>
                          <div className="mt-1 font-mono text-base font-semibold text-slate-100">
                            {formatRate(analysis.guardrails.payment_failed.control_rate, 2)}
                          </div>
                          <div className="text-[11px] text-slate-500">
                            {analysis.guardrails.payment_failed.control_errors} failures
                          </div>
                        </div>
                        <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3">
                          <div className="text-slate-400">Treatment Error Rate</div>
                          <div className="mt-1 font-mono text-base font-semibold text-slate-100">
                            {formatRate(
                              analysis.guardrails.payment_failed.treatment_rate,
                              2
                            )}
                          </div>
                          <div className="text-[11px] text-slate-500">
                            {analysis.guardrails.payment_failed.treatment_errors} failures
                          </div>
                        </div>
                        <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3">
                          <div className="text-slate-400">P(err_t &gt; err_c + soft)</div>
                          <div className="mt-1 font-mono text-base font-semibold text-amber-300">
                            {formatRate(
                              analysis.guardrails.payment_failed
                                .prob_treatment_exceeds_soft_delta,
                              1
                            )}
                          </div>
                        </div>
                        <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3">
                          <div className="text-slate-400">P(err_t &gt; err_c + hard)</div>
                          <div className="mt-1 font-mono text-base font-semibold text-rose-300">
                            {formatRate(
                              analysis.guardrails.payment_failed
                                .prob_treatment_exceeds_hard_delta,
                              1
                            )}
                          </div>
                        </div>
                      </div>
                    </SectionCard>
                  )}
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ExperimentsPage() {
  return (
    <Suspense fallback={<LoadingSkeleton rows={4} />}>
      <ExperimentsInner />
    </Suspense>
  );
}
