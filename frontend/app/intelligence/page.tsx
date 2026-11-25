"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  Bot,
  Brain,
  CheckCircle2,
  FolderGit2,
  GitBranch,
  Play,
  RefreshCw,
  Search,
  ShieldCheck,
} from "lucide-react";
import {
  approvePolicyRecommendation,
  getErrorMessage,
  getIntelligenceOverview,
  getRollbackAnalysis,
  implementPolicyRecommendation,
  listDependencyIntelligence,
  listFailurePatterns,
  listPolicyRecommendations,
  listRepositoryIntelligence,
  queryReleaseKnowledgeBase,
  reviewPolicyRecommendation,
  triggerIntelligenceAnalysis,
  verifyPolicyRecommendation,
} from "@/lib/api";
import type {
  DependencyIntelligenceItem,
  IntelligenceKnowledgeResponse,
  IntelligenceOverview,
  PolicyRecommendationRecord,
  ReleaseFailurePattern,
  RepositoryIntelligenceProfile,
} from "@/types/releaseguard";
import { formatTimestamp } from "@/lib/utils";
import {
  EmptyState,
  ErrorBanner,
  LoadingSkeleton,
  MetricCard,
  PageHeader,
  SectionCard,
  SuccessBanner,
} from "@/components/ui/StatePrimitives";
import { StatusBadge } from "@/components/StatusBadges";

type TabKey =
  | "overview"
  | "patterns"
  | "repositories"
  | "rollbacks"
  | "accuracy"
  | "policies"
  | "assistant";

export default function ReleaseIntelligencePage() {
  const [tab, setTab] = useState<TabKey>("overview");
  const [days, setDays] = useState(30);
  const [environment, setEnvironment] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [overview, setOverview] = useState<IntelligenceOverview | null>(null);
  const [patterns, setPatterns] = useState<ReleaseFailurePattern[]>([]);
  const [repos, setRepos] = useState<RepositoryIntelligenceProfile[]>([]);
  const [deps, setDeps] = useState<DependencyIntelligenceItem[]>([]);
  const [rollbacks, setRollbacks] = useState<{
    total_rollbacks: number;
    top_causes: Array<{ category: string; count: number }>;
    by_environment: Record<string, number>;
    avg_recovery_seconds?: number | null;
    rollbacks: Array<Record<string, unknown>>;
  } | null>(null);
  const [policies, setPolicies] = useState<PolicyRecommendationRecord[]>([]);

  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<IntelligenceKnowledgeResponse | null>(null);

  const loadAll = useCallback(async () => {
    const [ov, pat, rep, dep, rb, pol] = await Promise.all([
      getIntelligenceOverview({ days, environment: environment || undefined }),
      listFailurePatterns({ limit: 50 }),
      listRepositoryIntelligence(),
      listDependencyIntelligence(),
      getRollbackAnalysis(),
      listPolicyRecommendations(),
    ]);
    setOverview(ov);
    setPatterns(Array.isArray(pat.items) ? pat.items : []);
    setRepos(Array.isArray(rep.items) ? rep.items : []);
    setDeps(Array.isArray(dep.items) ? dep.items : []);
    setRollbacks(rb);
    setPolicies(Array.isArray(pol.items) ? pol.items : []);
  }, [days, environment]);

  useEffect(() => {
    let active = true;
    Promise.all([
      getIntelligenceOverview({ days, environment: environment || undefined }),
      listFailurePatterns({ limit: 50 }),
      listRepositoryIntelligence(),
      listDependencyIntelligence(),
      getRollbackAnalysis(),
      listPolicyRecommendations(),
    ])
      .then(([ov, pat, rep, dep, rb, pol]) => {
        if (!active) return;
        setOverview(ov);
        setPatterns(Array.isArray(pat.items) ? pat.items : []);
        setRepos(Array.isArray(rep.items) ? rep.items : []);
        setDeps(Array.isArray(dep.items) ? dep.items : []);
        setRollbacks(rb);
        setPolicies(Array.isArray(pol.items) ? pol.items : []);
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
  }, [days, environment]);

  async function handleRunAnalysis() {
    setBusy("analyze");
    setError(null);
    setNotice(null);
    try {
      const run = await triggerIntelligenceAnalysis();
      await loadAll();
      setNotice(
        `Learning analysis ${run.status}: processed ${run.input_record_count} release(s), updated ${run.output_record_count} intelligence record(s).`,
      );
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function handlePolicyAction(label: string, fn: () => Promise<unknown>, msg: string) {
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      await fn();
      await loadAll();
      setNotice(msg);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function handleAsk(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim()) return;
    setBusy("query");
    setError(null);
    try {
      const res = await queryReleaseKnowledgeBase({ question: question.trim() });
      setAnswer(res);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  const perf = overview?.performance;
  const acc = overview?.recommendation_accuracy;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Release Intelligence & Learning Engine"
        description="Evidence-driven historical analytics, failure pattern detection, recommendation accuracy, and human-governed policy improvements."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label="Time window"
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className="rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-200"
            >
              <option value={7}>Last 7 days</option>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
              <option value={365}>Last 365 days</option>
            </select>
            <select
              aria-label="Environment filter"
              value={environment}
              onChange={(e) => setEnvironment(e.target.value)}
              className="rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-200"
            >
              <option value="">All Environments</option>
              <option value="development">development</option>
              <option value="staging">staging</option>
              <option value="production">production</option>
            </select>
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => void handleRunAnalysis()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3.5 py-2 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
            >
              <Play className="h-3.5 w-3.5" />
              {busy === "analyze" ? "Analyzing..." : "Run Learning Analysis"}
            </button>
            <button
              type="button"
              onClick={() => void loadAll()}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-200 hover:bg-slate-800"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Refresh
            </button>
          </div>
        }
      />

      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <SuccessBanner message={notice} onDismiss={() => setNotice(null)} />

      {/* Navigation Tabs */}
      <div className="flex flex-wrap gap-1.5 border-b border-slate-800 pb-3">
        {(
          [
            ["overview", "Overview & KPIs"],
            ["patterns", `Failure Patterns (${patterns.length})`],
            ["repositories", `Repos & Dependencies (${repos.length})`],
            ["rollbacks", `Rollbacks (${rollbacks?.total_rollbacks ?? 0})`],
            ["accuracy", "Agent Accuracy"],
            ["policies", `Policy Governance (${policies.length})`],
            ["assistant", "Knowledge Assistant"],
          ] as Array<[TabKey, string]>
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
              tab === key
                ? "bg-indigo-500/20 text-indigo-200 border border-indigo-500/40"
                : "text-slate-400 hover:bg-slate-900 hover:text-slate-200"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {loading ? (
        <LoadingSkeleton rows={6} />
      ) : (
        <>
          {tab === "overview" && perf && (
            <div className="space-y-6">
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <MetricCard
                  label="Qualifying Releases"
                  value={perf.qualifying_releases}
                  subtext={`${perf.successful_releases} succeeded, ${perf.failed_releases} failed (${perf.window_days}d)`}
                />
                <MetricCard
                  label="Change Failure Rate"
                  value={
                    perf.change_failure_rate !== null && perf.change_failure_rate !== undefined
                      ? `${(perf.change_failure_rate * 100).toFixed(1)}%`
                      : "N/A"
                  }
                  subtext={`Trend: ${overview?.trend_direction ?? "unknown"} | ${perf.data_sufficiency}`}
                />
                <MetricCard
                  label="Mean Time to Recovery"
                  value={
                    perf.mean_time_to_recovery_seconds !== null &&
                    perf.mean_time_to_recovery_seconds !== undefined
                      ? `${Math.round(perf.mean_time_to_recovery_seconds)}s`
                      : "N/A"
                  }
                  subtext={`Rollbacks: ${perf.rolled_back_releases}`}
                />
                <MetricCard
                  label="Pending Policy Reviews"
                  value={overview?.pending_policy_recommendations ?? 0}
                  subtext={`Verified outcomes: ${perf.verified_outcomes} / ${perf.total_records}`}
                />
              </div>

              {perf.data_sufficiency !== "sufficient" && (
                <div className="flex items-start gap-2.5 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-xs text-amber-200">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  <div>
                    <div className="font-semibold">Limited Historical Sample Size</div>
                    <p className="mt-0.5 text-amber-200/80">
                      Only {perf.qualifying_releases} qualifying release(s) exist in the selected{" "}
                      {perf.window_days}-day window. Exploratory patterns are clearly labeled until
                      minimum sample thresholds are met.
                    </p>
                  </div>
                </div>
              )}

              <div className="grid gap-6 lg:grid-cols-2">
                <SectionCard
                  title="Generated Insights"
                  subtitle="Evidence-backed observations from persisted outcomes"
                >
                  {overview && overview.insights.length > 0 ? (
                    <div className="space-y-3">
                      {overview.insights.map((ins) => (
                        <div
                          key={ins.id}
                          className="rounded-lg border border-slate-800 bg-slate-900/50 p-3 text-xs"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-semibold text-slate-100">{ins.title}</span>
                            <StatusBadge status={ins.evidence_classification} />
                          </div>
                          <p className="mt-1 text-slate-300">{ins.description}</p>
                          <div className="mt-1.5 text-[11px] text-slate-400">
                            Sample size: {ins.sample_size} | Source: {ins.generated_by}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <EmptyState
                      icon={<Brain className="h-5 w-5" />}
                      title="No insights generated yet"
                      description="Run Learning Analysis to normalize release outcomes and generate insights."
                    />
                  )}
                </SectionCard>

                <SectionCard
                  title="Metric Definitions & Data Quality"
                  subtitle="Transparent denominators and completeness diagnostics"
                >
                  <div className="space-y-2 text-xs text-slate-300">
                    <div>
                      <span className="font-semibold text-slate-100">Qualifying Denominator:</span>{" "}
                      {perf.definitions.qualifying_denominator}
                    </div>
                    <div>
                      <span className="font-semibold text-slate-100">Change Failure Rate:</span>{" "}
                      <code>{perf.definitions.change_failure_rate}</code>
                    </div>
                    <div>
                      <span className="font-semibold text-slate-100">Deployment Frequency:</span>{" "}
                      {perf.deployment_frequency_per_week} / week
                    </div>
                    <div>
                      <span className="font-semibold text-slate-100">Guardrail Breach Rate:</span>{" "}
                      {perf.guardrail_breach_rate !== null && perf.guardrail_breach_rate !== undefined
                        ? `${(perf.guardrail_breach_rate * 100).toFixed(1)}% (${perf.guardrail_evaluations_count} evals)`
                        : "No guardrail evaluations"}
                    </div>
                    <div>
                      <span className="font-semibold text-slate-100">Incomplete Outcomes:</span>{" "}
                      {perf.incomplete_outcomes} record(s) with data-quality flags
                    </div>
                  </div>
                </SectionCard>
              </div>
            </div>
          )}

          {tab === "patterns" && (
            <SectionCard
              title="Detected Failure Patterns"
              subtitle="Recurring failure categories and module concentrations with sample-size safeguards"
            >
              {patterns.length === 0 ? (
                <EmptyState
                  icon={<AlertTriangle className="h-5 w-5" />}
                  title="No recurring failure patterns detected"
                  description="Patterns appear once failed or rolled-back releases are analyzed."
                />
              ) : (
                <div className="space-y-3">
                  {patterns.map((p) => (
                    <div
                      key={p.id}
                      className="rounded-xl border border-slate-800 bg-slate-900/50 p-4 text-xs"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-semibold text-slate-100">{p.name}</span>
                          <StatusBadge status={p.evidence_quality} />
                          {p.is_exploratory && (
                            <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-300">
                              Exploratory (small sample)
                            </span>
                          )}
                        </div>
                        <span className="font-mono text-slate-200">
                          {p.failure_count}/{p.sample_size} ({(p.observed_rate * 100).toFixed(1)}%)
                        </span>
                      </div>
                      <p className="mt-1.5 text-slate-300">{p.description}</p>
                      {p.recommended_mitigation && (
                        <div className="mt-2 rounded-lg border border-indigo-500/20 bg-indigo-500/5 p-2 text-indigo-200">
                          Recommended mitigation: {p.recommended_mitigation}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </SectionCard>
          )}

          {tab === "repositories" && (
            <div className="grid gap-6 lg:grid-cols-2">
              <SectionCard
                title="Repository Intelligence Profiles"
                subtitle="Per-repository historical failure rates and high-risk modules"
              >
                {repos.length === 0 ? (
                  <EmptyState
                    icon={<FolderGit2 className="h-5 w-5" />}
                    title="No repository profiles yet"
                    description="Run Learning Analysis to compute repository intelligence profiles."
                  />
                ) : (
                  <div className="divide-y divide-slate-800">
                    {repos.map((r) => (
                      <Link
                        key={r.id}
                        href={`/intelligence/repositories/${r.repository_id}`}
                        className="flex items-center justify-between py-3 transition hover:bg-slate-900/60"
                      >
                        <div>
                          <div className="text-sm font-semibold text-slate-100">
                            {r.repository_name || r.repository_id}
                          </div>
                          <div className="text-xs text-slate-400">
                            {r.release_count} releases | {r.failure_count} failures |{" "}
                            {r.rollback_count} rollbacks
                          </div>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="font-mono text-xs font-semibold text-slate-200">
                            {(r.historical_failure_rate * 100).toFixed(1)}% fail
                          </span>
                          <ArrowRight className="h-4 w-4 text-slate-500" />
                        </div>
                      </Link>
                    ))}
                  </div>
                )}
              </SectionCard>

              <SectionCard
                title="Dependency Failure Intelligence"
                subtitle="Dependencies and manifests correlated with failed releases"
              >
                {deps.length === 0 ? (
                  <EmptyState
                    icon={<GitBranch className="h-5 w-5" />}
                    title="No dependency failure associations"
                    description="Dependency correlations appear when PR diffs or RiskFindings touch dependency manifests."
                  />
                ) : (
                  <div className="space-y-2.5">
                    {deps.map((d) => (
                      <div
                        key={d.dependency}
                        className="rounded-lg border border-slate-800 bg-slate-900/50 p-3 text-xs"
                      >
                        <div className="flex items-center justify-between">
                          <code className="font-semibold text-slate-100">{d.dependency}</code>
                          <span className="font-mono text-slate-300">
                            {d.failure_count}/{d.release_count} failed ({(d.failure_rate * 100).toFixed(1)}%)
                          </span>
                        </div>
                        <div className="mt-1 text-slate-400">{d.recommendation}</div>
                      </div>
                    ))}
                  </div>
                )}
              </SectionCard>
            </div>
          )}

          {tab === "rollbacks" && rollbacks && (
            <SectionCard
              title="Rollback Cause Intelligence"
              subtitle="Classified rollback triggers, recovery durations, and rollout stage distribution"
            >
              <div className="mb-4 grid gap-3 sm:grid-cols-3">
                <MetricCard label="Total Rollbacks" value={rollbacks.total_rollbacks} />
                <MetricCard
                  label="Avg Recovery Time"
                  value={
                    rollbacks.avg_recovery_seconds !== null &&
                    rollbacks.avg_recovery_seconds !== undefined
                      ? `${Math.round(rollbacks.avg_recovery_seconds)}s`
                      : "N/A"
                  }
                />
                <MetricCard
                  label="Top Cause"
                  value={rollbacks.top_causes[0]?.category ?? "None"}
                />
              </div>
              {rollbacks.rollbacks.length === 0 ? (
                <p className="text-xs text-slate-400">No rollback operations recorded yet.</p>
              ) : (
                <div className="space-y-2">
                  {rollbacks.rollbacks.map((rb) => (
                    <div
                      key={String(rb.id)}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-900/50 p-3 text-xs"
                    >
                      <div>
                        <span className="font-semibold text-slate-100">
                          {String(rb.cause_category)}
                        </span>
                        <span className="mx-2 text-slate-600">|</span>
                        <span className="text-slate-300">{String(rb.reason)}</span>
                      </div>
                      <div className="flex items-center gap-2 text-slate-400">
                        <span>Env: {String(rb.environment)}</span>
                        <span>Stage: {String(rb.percentage_at_rollback)}%</span>
                        <StatusBadge status={String(rb.status)} />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </SectionCard>
          )}

          {tab === "accuracy" && acc && (
            <div className="space-y-6">
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <MetricCard label="Total Recommendations" value={acc.total_recommendations} />
                <MetricCard
                  label="Acceptance Rate"
                  value={
                    acc.acceptance_rate !== null && acc.acceptance_rate !== undefined
                      ? `${(acc.acceptance_rate * 100).toFixed(1)}%`
                      : "N/A"
                  }
                  subtext={`${acc.accepted_count}/${acc.responded_count} responded`}
                />
                <MetricCard
                  label="Positive Outcome Rate"
                  value={
                    acc.positive_outcome_rate !== null && acc.positive_outcome_rate !== undefined
                      ? `${(acc.positive_outcome_rate * 100).toFixed(1)}%`
                      : "N/A"
                  }
                  subtext={`${acc.positive_outcome_count}/${acc.evaluable_count} evaluable`}
                />
                <MetricCard
                  label="Inconclusive / Unevaluable"
                  value={acc.inconclusive_or_not_evaluable_count}
                  subtext="Acceptance alone != verified effectiveness"
                />
              </div>

              <SectionCard title="Breakdown by Category" subtitle={acc.methodology_note}>
                {Object.keys(acc.by_category).length === 0 ? (
                  <EmptyState
                    icon={<Bot className="h-5 w-5" />}
                    title="No evaluated agent recommendations yet"
                    description="Agent decisions will appear here once evaluated against release outcomes."
                  />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs">
                      <thead className="border-b border-slate-800 text-slate-400">
                        <tr>
                          <th className="py-2 pr-4">Category</th>
                          <th className="py-2 pr-4">Total</th>
                          <th className="py-2 pr-4">Accepted</th>
                          <th className="py-2 pr-4">Positive Outcomes</th>
                          <th className="py-2">Negative Outcomes</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800/70">
                        {Object.entries(acc.by_category).map(([cat, stats]) => (
                          <tr key={cat}>
                            <td className="py-2 pr-4 font-mono text-slate-200">{cat}</td>
                            <td className="py-2 pr-4 text-slate-300">{stats.total}</td>
                            <td className="py-2 pr-4 text-slate-300">{stats.accepted}</td>
                            <td className="py-2 pr-4 text-emerald-300">{stats.positive}</td>
                            <td className="py-2 text-rose-300">{stats.negative}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </SectionCard>
            </div>
          )}

          {tab === "policies" && (
            <SectionCard
              title="Policy Recommendations & Governance"
              subtitle="Human-reviewed policy improvements. Changes require explicit approval before implementation."
            >
              {policies.length === 0 ? (
                <EmptyState
                  icon={<ShieldCheck className="h-5 w-5" />}
                  title="No policy recommendations yet"
                  description="Policy recommendations are generated when recurring failure patterns or risky repositories are detected."
                />
              ) : (
                <div className="space-y-4">
                  {policies.map((pol) => (
                    <div
                      key={pol.id}
                      className="rounded-xl border border-slate-800 bg-slate-900/50 p-4 text-xs"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-semibold text-slate-100">{pol.title}</span>
                          <StatusBadge status={pol.review_status} />
                        </div>
                        <span className="font-mono text-slate-400">{pol.policy_area}</span>
                      </div>
                      <p className="mt-1.5 text-slate-300">{pol.description}</p>
                      <div className="mt-2 grid gap-2 sm:grid-cols-2">
                        <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-2">
                          <div className="text-[11px] font-semibold text-slate-400">Current</div>
                          <code className="text-[11px] text-slate-300">
                            {JSON.stringify(pol.current_configuration)}
                          </code>
                        </div>
                        <div className="rounded-lg border border-indigo-500/30 bg-indigo-500/5 p-2">
                          <div className="text-[11px] font-semibold text-indigo-300">Proposed</div>
                          <code className="text-[11px] text-indigo-200">
                            {JSON.stringify(pol.proposed_configuration)}
                          </code>
                        </div>
                      </div>
                      {pol.tradeoffs && (
                        <div className="mt-2 text-slate-400">Trade-offs: {pol.tradeoffs}</div>
                      )}
                      <div className="mt-3 flex flex-wrap gap-2">
                        {pol.review_status === "pending_review" && (
                          <>
                            <button
                              type="button"
                              disabled={busy !== null}
                              onClick={() =>
                                void handlePolicyAction(
                                  `accept-${pol.id}`,
                                  () =>
                                    reviewPolicyRecommendation(pol.id, {
                                      decision: "accept",
                                    }),
                                  "Policy recommendation accepted for approval.",
                                )
                              }
                              className="rounded-lg bg-emerald-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-emerald-500"
                            >
                              Accept
                            </button>
                            <button
                              type="button"
                              disabled={busy !== null}
                              onClick={() =>
                                void handlePolicyAction(
                                  `reject-${pol.id}`,
                                  () =>
                                    reviewPolicyRecommendation(pol.id, {
                                      decision: "reject",
                                    }),
                                  "Policy recommendation rejected.",
                                )
                              }
                              className="rounded-lg border border-rose-500/40 px-2.5 py-1 text-xs text-rose-300 hover:bg-rose-500/10"
                            >
                              Reject
                            </button>
                          </>
                        )}
                        {["pending_review", "accepted", "modified"].includes(pol.review_status) &&
                          !pol.approval_id && (
                            <button
                              type="button"
                              disabled={busy !== null}
                              onClick={() =>
                                void handlePolicyAction(
                                  `approve-${pol.id}`,
                                  () => approvePolicyRecommendation(pol.id),
                                  "Policy recommendation approved with Category C audit record.",
                                )
                              }
                              className="rounded-lg bg-indigo-500 px-2.5 py-1 text-xs font-semibold text-white hover:bg-indigo-400"
                            >
                              Approve (Category C)
                            </button>
                          )}
                        {pol.approval_id &&
                          ["accepted", "modified"].includes(pol.review_status) && (
                            <button
                              type="button"
                              disabled={busy !== null}
                              onClick={() =>
                                void handlePolicyAction(
                                  `impl-${pol.id}`,
                                  () => implementPolicyRecommendation(pol.id),
                                  "Approved policy implemented.",
                                )
                              }
                              className="rounded-lg bg-sky-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-sky-500"
                            >
                              Implement Policy
                            </button>
                          )}
                        {pol.review_status === "implemented" && (
                          <button
                            type="button"
                            disabled={busy !== null}
                            onClick={() =>
                              void handlePolicyAction(
                                `verify-${pol.id}`,
                                () => verifyPolicyRecommendation(pol.id),
                                "Policy effectiveness verified against post-change outcomes.",
                              )
                            }
                            className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-emerald-500"
                          >
                            <CheckCircle2 className="h-3.5 w-3.5" />
                            Verify Effectiveness
                          </button>
                        )}
                      </div>
                      <div className="mt-2 text-[11px] text-slate-500">
                        Updated {formatTimestamp(pol.updated_at)}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </SectionCard>
          )}

          {tab === "assistant" && (
            <SectionCard
              title="Release Knowledge Assistant"
              subtitle="Ask historical engineering questions grounded in persisted release, rollback, dependency, and recommendation records"
            >
              <form onSubmit={(e) => void handleAsk(e)} className="flex flex-wrap gap-2">
                <input
                  type="text"
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  placeholder="e.g. What are the most common rollback causes?"
                  className="min-w-[260px] flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100"
                />
                <button
                  type="submit"
                  disabled={busy === "query"}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
                >
                  <Search className="h-3.5 w-3.5" />
                  {busy === "query" ? "Searching..." : "Ask Knowledge Base"}
                </button>
              </form>

              {answer && (
                <div className="mt-4 space-y-3 rounded-xl border border-slate-800 bg-slate-900/60 p-4 text-xs">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-indigo-300">Direct Answer</span>
                    <StatusBadge status={answer.evidence_classification} />
                  </div>
                  <p className="text-sm text-slate-100">{answer.direct_answer}</p>
                  <div className="text-slate-300">
                    <span className="font-semibold text-slate-200">Observed Pattern:</span>{" "}
                    {answer.observed_pattern}
                  </div>
                  <div className="text-slate-300">
                    <span className="font-semibold text-slate-200">Recommended Action:</span>{" "}
                    {answer.recommended_action}
                  </div>
                  <div>
                    <div className="font-semibold text-amber-300">Confidence & Limitations</div>
                    <ul className="mt-1 list-disc pl-5 text-slate-400">
                      {answer.confidence_and_limitations.map((lim, idx) => (
                        <li key={idx}>{lim}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}
            </SectionCard>
          )}
        </>
      )}
    </div>
  );
}
