"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowRight,
  Plus,
  RefreshCw,
  ShieldCheck,
  X,
} from "lucide-react";
import {
  createRiskAssessment,
  getErrorMessage,
  listPullRequests,
  listReleases,
  listRiskAssessments,
} from "@/lib/api";
import type {
  PullRequest,
  ReleaseRecord,
  ReleaseRiskAssessmentRecord,
} from "@/types/releaseguard";
import { formatTimestamp } from "@/lib/utils";
import {
  EmptyState,
  ErrorBanner,
  LoadingSkeleton,
  MetricCard,
  PageHeader,
  SectionCard,
} from "@/components/ui/StatePrimitives";
import { EnvironmentBadge, RiskLevelBadge, StatusBadge } from "@/components/StatusBadges";

export default function RiskIntelligencePage() {
  const router = useRouter();
  const [assessments, setAssessments] = useState<ReleaseRiskAssessmentRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);

  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [releases, setReleases] = useState<ReleaseRecord[]>([]);
  const [prs, setPrs] = useState<PullRequest[]>([]);
  const [selectedReleaseId, setSelectedReleaseId] = useState("");
  const [selectedPrId, setSelectedPrId] = useState("");
  const [targetEnv, setTargetEnv] = useState("staging");

  useEffect(() => {
    let active = true;
    listRiskAssessments({ limit: 50 })
      .then((res) => {
        if (!active) return;
        setAssessments(res.items);
        setTotal(res.total);
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
  }, [refreshTick]);

  const openModal = async () => {
    setShowCreate(true);
    try {
      const [relRes, prRes] = await Promise.all([
        listReleases({ limit: 50 }),
        listPullRequests({ limit: 50 }),
      ]);
      setReleases(relRes.items);
      setPrs(prRes.items);
    } catch {
      // Optional dropdowns
    }
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    setError(null);
    try {
      const detail = await createRiskAssessment({
        release_id: selectedReleaseId || undefined,
        pull_request_id: selectedPrId || undefined,
        target_environment: targetEnv,
      });
      setShowCreate(false);
      router.push(`/risk-intelligence/${detail.assessment.id}`);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setCreating(false);
    }
  };

  const highCritCount = assessments.filter((a) =>
    ["high", "critical"].includes(a.risk_level),
  ).length;
  const partialCount = assessments.filter((a) => a.assessment_status === "partial").length;
  const outdatedCount = assessments.filter((a) => a.assessment_status === "outdated").length;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Intelligent Release Risk Engine"
        description="Multi-dimensional, evidence-driven risk assessments combining code impact, dependency graph, historical failures, test coverage, environment criticality, and feature-flag suitability."
        actions={
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setRefreshTick((n) => n + 1)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-800"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Refresh
            </button>
            <button
              type="button"
              onClick={() => void openModal()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400"
            >
              <Plus className="h-3.5 w-3.5" />
              Run Risk Assessment
            </button>
          </div>
        }
      />

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          label="Total Assessments"
          value={total}
          subtext="Persisted versioned history"
          icon={<ShieldCheck className="h-4 w-4" />}
        />
        <MetricCard
          label="High / Critical Risk"
          value={highCritCount}
          subtext="Requires mitigation or review"
          tone={highCritCount > 0 ? "rose" : "emerald"}
          icon={<AlertTriangle className="h-4 w-4" />}
        />
        <MetricCard
          label="Partial Completeness"
          value={partialCount}
          subtext="Missing optional dimensions"
          tone={partialCount > 0 ? "amber" : "default"}
        />
        <MetricCard
          label="Outdated Assessments"
          value={outdatedCount}
          subtext="Superseded by newer commits"
        />
      </div>

      <SectionCard
        title={`Risk Assessments (${total})`}
        subtitle="Click any assessment to inspect dimension scores, dependency graph, evidence, scenarios, and mitigations"
      >
        {loading ? (
          <LoadingSkeleton rows={4} />
        ) : assessments.length === 0 ? (
          <EmptyState
            icon={<ShieldCheck className="h-5 w-5" />}
            title="No risk assessments yet"
            description="Run an Intelligent Risk Assessment against any Release or Pull Request."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-slate-800 text-[11px] uppercase tracking-wider text-slate-400">
                  <th className="py-2 pr-3">Assessment</th>
                  <th className="py-2 pr-3">Environment</th>
                  <th className="py-2 pr-3">Score</th>
                  <th className="py-2 pr-3">Classification</th>
                  <th className="py-2 pr-3">Completeness</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3">Created</th>
                  <th className="py-2 text-right">Inspect</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/80">
                {assessments.map((a) => (
                  <tr key={a.id} className="hover:bg-slate-900/40">
                    <td className="py-2.5 pr-3">
                      <Link
                        href={`/risk-intelligence/${a.id}`}
                        className="font-mono font-semibold text-indigo-300 hover:underline"
                      >
                        {a.assessment_key}
                      </Link>
                      <div className="mt-0.5 text-[11px] text-slate-500">
                        v{a.scoring_version} · {a.is_current ? "Current" : "Historical"}
                      </div>
                    </td>
                    <td className="py-2.5 pr-3">
                      <EnvironmentBadge environment={a.target_environment} />
                    </td>
                    <td className="py-2.5 pr-3 font-mono font-semibold text-slate-100 tabular-nums">
                      {a.risk_score !== null && a.risk_score !== undefined
                        ? `${a.risk_score}/100`
                        : "—"}
                    </td>
                    <td className="py-2.5 pr-3">
                      <RiskLevelBadge level={a.risk_level} />
                    </td>
                    <td className="py-2.5 pr-3 font-mono text-slate-300 tabular-nums">
                      {Math.round(a.data_completeness * 100)}%
                    </td>
                    <td className="py-2.5 pr-3">
                      <StatusBadge status={a.assessment_status} />
                    </td>
                    <td className="py-2.5 pr-3 text-[11px] text-slate-400 tabular-nums">
                      {formatTimestamp(a.created_at)}
                    </td>
                    <td className="py-2.5 text-right">
                      <Link
                        href={`/risk-intelligence/${a.id}`}
                        className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-900 px-2.5 py-1 text-[11px] text-slate-200 hover:bg-slate-800"
                      >
                        Open
                        <ArrowRight className="h-3 w-3" />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <div className="w-full max-w-md rounded-xl border border-slate-800 bg-[#111622] p-5 shadow-2xl">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-100">Run Risk Assessment</h3>
              <button
                type="button"
                onClick={() => setShowCreate(false)}
                className="rounded-lg p-1 text-slate-400 hover:bg-slate-800"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <form onSubmit={handleCreate} className="space-y-3 text-xs">
              <div>
                <label className="mb-1 block text-slate-300">Release (Optional)</label>
                <select
                  value={selectedReleaseId}
                  onChange={(e) => setSelectedReleaseId(e.target.value)}
                  className="w-full rounded-lg border border-slate-700 bg-[#0d1119] px-2.5 py-2 text-slate-100"
                >
                  <option value="">None</option>
                  {releases.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.release_key} — {r.title}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-slate-300">Pull Request (Optional)</label>
                <select
                  value={selectedPrId}
                  onChange={(e) => setSelectedPrId(e.target.value)}
                  className="w-full rounded-lg border border-slate-700 bg-[#0d1119] px-2.5 py-2 text-slate-100"
                >
                  <option value="">None</option>
                  {prs.map((p) => (
                    <option key={p.id} value={p.id}>
                      #{p.github_pr_number} — {p.title}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-slate-300">Target Environment</label>
                <select
                  value={targetEnv}
                  onChange={(e) => setTargetEnv(e.target.value)}
                  className="w-full rounded-lg border border-slate-700 bg-[#0d1119] px-2.5 py-2 text-slate-100"
                >
                  <option value="development">development</option>
                  <option value="staging">staging</option>
                  <option value="production">production</option>
                </select>
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowCreate(false)}
                  className="rounded-lg border border-slate-700 px-3 py-1.5 text-slate-300"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={creating}
                  className="rounded-lg bg-indigo-500 px-4 py-1.5 font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
                >
                  {creating ? "Analyzing..." : "Analyze Risk"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
