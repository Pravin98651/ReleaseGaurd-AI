"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  Plus,
  Rocket,
  Search,
  Sparkles,
  X,
} from "lucide-react";
import {
  createRelease,
  getErrorMessage,
  listFeatureFlags,
  listPullRequests,
  listReleases,
  listRepositories,
} from "@/lib/api";
import type {
  FeatureFlag,
  PullRequest,
  ReleaseReadinessStatus,
  ReleaseRecord,
  ReleaseStatus,
  ReleaseType,
  Repository,
  RiskLevel,
} from "@/types/releaseguard";
import { formatTimestamp } from "@/lib/utils";
import {
  EmptyState,
  ErrorBanner,
  LoadingSkeleton,
  SectionCard,
} from "@/components/ui/StatePrimitives";
import { EnvironmentBadge, RiskLevelBadge, StatusBadge } from "@/components/StatusBadges";

export function ReleaseLifecycleList() {
  const router = useRouter();
  const [releases, setReleases] = useState<ReleaseRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [readinessFilter, setReadinessFilter] = useState<string>("");
  const [envFilter, setEnvFilter] = useState<string>("");

  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [repos, setRepos] = useState<Repository[]>([]);
  const [prs, setPrs] = useState<PullRequest[]>([]);
  const [flags, setFlags] = useState<FeatureFlag[]>([]);

  const [formTitle, setFormTitle] = useState("");
  const [formDesc, setFormDesc] = useState("");
  const [formType, setFormType] = useState<ReleaseType>("feature");
  const [formEnv, setFormEnv] = useState("development");
  const [formRepoId, setFormRepoId] = useState("");
  const [formPrId, setFormPrId] = useState("");
  const [formFlagId, setFormFlagId] = useState("");
  const [formRisk, setFormRisk] = useState<RiskLevel>("medium");
  const [formRolloutPct, setFormRolloutPct] = useState(25);
  const [formAiAssist, setFormAiAssist] = useState(true);

  useEffect(() => {
    let active = true;
    listReleases({
      q: search.trim() || undefined,
      status: (statusFilter || undefined) as ReleaseStatus | undefined,
      readiness_status: (readinessFilter || undefined) as ReleaseReadinessStatus | undefined,
      environment: envFilter.trim() || undefined,
      limit: 50,
    })
      .then((res) => {
        if (!active) return;
        setReleases(res.items);
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
  }, [search, statusFilter, readinessFilter, envFilter]);

  const openCreateModal = async () => {
    setShowCreate(true);
    try {
      const [rRes, pRes, fRes] = await Promise.all([
        listRepositories(),
        listPullRequests({ limit: 50 }),
        listFeatureFlags({ limit: 50 }),
      ]);
      setRepos(rRes.items);
      setPrs(pRes.items);
      setFlags(fRes.items);
    } catch {
      // Non-fatal; manual creation works without dropdowns
    }
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    setError(null);
    try {
      const detail = await createRelease({
        title: formTitle.trim() || undefined,
        description: formDesc.trim() || undefined,
        release_type: formType,
        target_environment: formEnv,
        repository_id: formRepoId || undefined,
        pull_request_id: formPrId || undefined,
        feature_flag_id: formFlagId || undefined,
        risk_level: formRisk,
        target_rollout_percentage: formRolloutPct,
        ai_assist: formAiAssist,
      });
      setShowCreate(false);
      router.push(`/releases/${detail.release.id}`);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="space-y-4">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <SectionCard
        title={`Release Lifecycles (${total})`}
        subtitle="End-to-end tracked releases unifying PR analysis, patch validation, Flagsmith flags, experiments, and approvals"
        actions={
          <button
            type="button"
            onClick={() => void openCreateModal()}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400"
          >
            <Plus className="h-3.5 w-3.5" />
            New Release
          </button>
        }
      >
        <div className="mb-4 grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-500" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search key or title..."
              className="w-full rounded-lg border border-slate-700 bg-[#0d1119] py-1.5 pl-8 pr-3 text-xs text-slate-100 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
            />
          </div>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            aria-label="Filter by status"
            className="rounded-lg border border-slate-700 bg-[#0d1119] px-2.5 py-1.5 text-xs text-slate-100 focus:border-indigo-500 focus:outline-none"
          >
            <option value="">All Statuses</option>
            <option value="draft">Draft</option>
            <option value="analyzing">Analyzing</option>
            <option value="validating">Validating</option>
            <option value="ready_for_flag">Ready for Flag</option>
            <option value="awaiting_approval">Awaiting Approval</option>
            <option value="approved">Approved</option>
            <option value="rolling_out">Rolling Out</option>
            <option value="paused">Paused</option>
            <option value="blocked">Blocked</option>
            <option value="failed">Failed</option>
            <option value="completed">Completed</option>
            <option value="cancelled">Cancelled</option>
          </select>
          <select
            value={readinessFilter}
            onChange={(e) => setReadinessFilter(e.target.value)}
            aria-label="Filter by readiness"
            className="rounded-lg border border-slate-700 bg-[#0d1119] px-2.5 py-1.5 text-xs text-slate-100 focus:border-indigo-500 focus:outline-none"
          >
            <option value="">All Readiness</option>
            <option value="ready">Ready</option>
            <option value="needs_attention">Needs Attention</option>
            <option value="not_ready">Not Ready</option>
            <option value="blocked">Blocked</option>
          </select>
          <select
            value={envFilter}
            onChange={(e) => setEnvFilter(e.target.value)}
            aria-label="Filter by environment"
            className="rounded-lg border border-slate-700 bg-[#0d1119] px-2.5 py-1.5 text-xs text-slate-100 focus:border-indigo-500 focus:outline-none"
          >
            <option value="">All Environments</option>
            <option value="development">development</option>
            <option value="staging">staging</option>
            <option value="production">production</option>
          </select>
        </div>

        {loading ? (
          <LoadingSkeleton rows={4} />
        ) : releases.length === 0 ? (
          <EmptyState
            icon={<Rocket className="h-5 w-5" />}
            title="No release lifecycles found"
            description="Create a release manually or directly from any synchronized Pull Request."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-slate-800 text-[11px] uppercase tracking-wider text-slate-400">
                  <th className="py-2 pr-3">Release</th>
                  <th className="py-2 pr-3">Environment</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3">Readiness</th>
                  <th className="py-2 pr-3">Risk</th>
                  <th className="py-2 pr-3">Rollout</th>
                  <th className="py-2 pr-3">Updated</th>
                  <th className="py-2 text-right">Cockpit</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/80">
                {releases.map((rel) => (
                  <tr key={rel.id} className="hover:bg-slate-900/40">
                    <td className="py-2.5 pr-3">
                      <Link
                        href={`/releases/${rel.id}`}
                        className="font-semibold text-slate-100 hover:text-indigo-300"
                      >
                        <span className="mr-1.5 font-mono text-[11px] text-indigo-300">
                          {rel.release_key}
                        </span>
                        {rel.title}
                      </Link>
                      <div className="mt-0.5 text-[11px] text-slate-500">
                        {rel.release_type} · v{rel.version}
                      </div>
                    </td>
                    <td className="py-2.5 pr-3">
                      <EnvironmentBadge environment={rel.target_environment} />
                    </td>
                    <td className="py-2.5 pr-3">
                      <StatusBadge status={rel.status} />
                    </td>
                    <td className="py-2.5 pr-3">
                      <div className="flex items-center gap-2">
                        <StatusBadge status={rel.readiness_status} />
                        <span className="font-mono text-[11px] text-slate-300 tabular-nums">
                          {rel.readiness_score}/100
                        </span>
                      </div>
                    </td>
                    <td className="py-2.5 pr-3">
                      <RiskLevelBadge level={rel.risk_level} />
                    </td>
                    <td className="py-2.5 pr-3 font-mono text-slate-300 tabular-nums">
                      {rel.current_rollout_percentage}% / {rel.target_rollout_percentage}%
                    </td>
                    <td className="py-2.5 pr-3 text-[11px] text-slate-400 tabular-nums">
                      {formatTimestamp(rel.updated_at)}
                    </td>
                    <td className="py-2.5 text-right">
                      <Link
                        href={`/releases/${rel.id}`}
                        className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-900 px-2.5 py-1 text-[11px] font-medium text-slate-200 hover:bg-slate-800"
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
          <div className="w-full max-w-lg rounded-xl border border-slate-800 bg-[#111622] p-5 shadow-2xl">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-100">Create Release Lifecycle</h3>
              <button
                type="button"
                onClick={() => setShowCreate(false)}
                className="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <form onSubmit={handleCreate} className="space-y-3 text-xs">
              <div>
                <label className="mb-1 block text-slate-300">Release Title</label>
                <input
                  type="text"
                  value={formTitle}
                  onChange={(e) => setFormTitle(e.target.value)}
                  placeholder="e.g. Checkout v2 Gradual Rollout"
                  className="w-full rounded-lg border border-slate-700 bg-[#0d1119] px-3 py-2 text-slate-100 focus:border-indigo-500 focus:outline-none"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-slate-300">Type</label>
                  <select
                    value={formType}
                    onChange={(e) => setFormType(e.target.value as ReleaseType)}
                    className="w-full rounded-lg border border-slate-700 bg-[#0d1119] px-2.5 py-2 text-slate-100"
                  >
                    <option value="feature">Feature</option>
                    <option value="hotfix">Hotfix</option>
                    <option value="experiment">Experiment</option>
                    <option value="config">Config</option>
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-slate-300">Target Environment</label>
                  <select
                    value={formEnv}
                    onChange={(e) => setFormEnv(e.target.value)}
                    className="w-full rounded-lg border border-slate-700 bg-[#0d1119] px-2.5 py-2 text-slate-100"
                  >
                    <option value="development">development</option>
                    <option value="staging">staging</option>
                    <option value="production">production</option>
                  </select>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-slate-300">Linked Repository (Optional)</label>
                  <select
                    value={formRepoId}
                    onChange={(e) => setFormRepoId(e.target.value)}
                    className="w-full rounded-lg border border-slate-700 bg-[#0d1119] px-2.5 py-2 text-slate-100"
                  >
                    <option value="">None</option>
                    {repos.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.full_name}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-slate-300">Linked Pull Request (Optional)</label>
                  <select
                    value={formPrId}
                    onChange={(e) => setFormPrId(e.target.value)}
                    className="w-full rounded-lg border border-slate-700 bg-[#0d1119] px-2.5 py-2 text-slate-100"
                  >
                    <option value="">None</option>
                    {prs.map((p) => (
                      <option key={p.id} value={p.id}>
                        #{p.github_pr_number} {p.title.slice(0, 36)}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="mb-1 block text-slate-300">Feature Flag</label>
                  <select
                    value={formFlagId}
                    onChange={(e) => setFormFlagId(e.target.value)}
                    className="w-full rounded-lg border border-slate-700 bg-[#0d1119] px-2.5 py-2 text-slate-100"
                  >
                    <option value="">None</option>
                    {flags.map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.key}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-slate-300">Risk Level</label>
                  <select
                    value={formRisk}
                    onChange={(e) => setFormRisk(e.target.value as RiskLevel)}
                    className="w-full rounded-lg border border-slate-700 bg-[#0d1119] px-2.5 py-2 text-slate-100"
                  >
                    <option value="low">low</option>
                    <option value="medium">medium</option>
                    <option value="high">high</option>
                    <option value="critical">critical</option>
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-slate-300">Target Rollout %</label>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={formRolloutPct}
                    onChange={(e) => setFormRolloutPct(Number(e.target.value))}
                    className="w-full rounded-lg border border-slate-700 bg-[#0d1119] px-2.5 py-2 text-slate-100"
                  />
                </div>
              </div>
              <div>
                <label className="mb-1 block text-slate-300">Description</label>
                <textarea
                  rows={2}
                  value={formDesc}
                  onChange={(e) => setFormDesc(e.target.value)}
                  placeholder="Release goals, scope, or rollback notes..."
                  className="w-full rounded-lg border border-slate-700 bg-[#0d1119] px-3 py-2 text-slate-100 focus:border-indigo-500 focus:outline-none"
                />
              </div>
              <label className="flex items-center gap-2 text-slate-300">
                <input
                  type="checkbox"
                  checked={formAiAssist}
                  onChange={(e) => setFormAiAssist(e.target.checked)}
                  className="rounded border-slate-700 bg-[#0d1119]"
                />
                <Sparkles className="h-3.5 w-3.5 text-indigo-400" />
                Draft AI release summary if Gemini is configured
              </label>
              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowCreate(false)}
                  className="rounded-lg border border-slate-700 px-3 py-1.5 text-slate-300 hover:bg-slate-800"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={creating}
                  className="rounded-lg bg-indigo-500 px-4 py-1.5 font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
                >
                  {creating ? "Creating..." : "Create Release"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
