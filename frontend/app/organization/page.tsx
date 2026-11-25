"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Building2, GitBranch, Play, Plus, RefreshCw, Search, ShieldCheck } from "lucide-react";
import {
  approveCoordinatedReleasePlan,
  createOrganization,
  getErrorMessage,
  getOrganizationDependencyGraph,
  getOrganizationReleaseOverview,
  getOrganizationRepositoryRankings,
  listCoordinatedReleasePlans,
  listOrganizationPolicies,
  listOrganizations,
  publishOrganizationPolicy,
  queryOrganizationIntelligence,
  startCoordinatedReleasePlan,
} from "@/lib/api";
import type {
  CoordinatedReleasePlanRecord,
  DependencyGraphData,
  OrganizationRecord,
  OrganizationReleaseOverview,
  OrganizationReleasePolicyRecord,
  RepositoryHealthSnapshotRecord,
} from "@/types/releaseguard";
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

export default function OrganizationCommandCenterPage() {
  const [orgs, setOrgs] = useState<OrganizationRecord[]>([]);
  const [selectedOrgId, setSelectedOrgId] = useState("");
  const [overview, setOverview] = useState<OrganizationReleaseOverview | null>(null);
  const [graph, setGraph] = useState<DependencyGraphData | null>(null);
  const [plans, setPlans] = useState<CoordinatedReleasePlanRecord[]>([]);
  const [rankings, setRankings] = useState<RepositoryHealthSnapshotRecord[]>([]);
  const [policies, setPolicies] = useState<OrganizationReleasePolicyRecord[]>([]);

  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [newOrgName, setNewOrgName] = useState("");
  const [newOrgSlug, setNewOrgSlug] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<{ direct_answer: string } | null>(null);

  const loadOrgDetails = useCallback(async (orgId: string) => {
    const [ov, gr, pl, rk, pol] = await Promise.all([
      getOrganizationReleaseOverview(orgId),
      getOrganizationDependencyGraph(orgId),
      listCoordinatedReleasePlans(orgId),
      getOrganizationRepositoryRankings(orgId),
      listOrganizationPolicies(orgId),
    ]);
    setOverview(ov);
    setGraph(gr);
    setPlans(Array.isArray(pl) ? pl : []);
    setRankings(Array.isArray(rk) ? rk : []);
    setPolicies(Array.isArray(pol) ? pol : []);
  }, []);

  useEffect(() => {
    let active = true;
    listOrganizations()
      .then(async (list) => {
        if (!active) return;
        const items = Array.isArray(list) ? list : [];
        setOrgs(items);
        if (items.length > 0) {
          setSelectedOrgId(items[0].id);
          await loadOrgDetails(items[0].id);
        }
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
  }, [loadOrgDetails]);

  async function handleCreateOrg(e: React.FormEvent) {
    e.preventDefault();
    setBusy("create-org");
    setError(null);
    try {
      const org = await createOrganization({
        name: newOrgName.trim(),
        slug: newOrgSlug.trim() || newOrgName.trim().toLowerCase().replace(/\s+/g, "-"),
      });
      setOrgs((prev) => [org, ...prev]);
      setSelectedOrgId(org.id);
      await loadOrgDetails(org.id);
      setNewOrgName("");
      setNewOrgSlug("");
      setNotice(`Organization '${org.name}' created.`);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function handlePlanAction(
    label: string,
    fn: () => Promise<unknown>,
    msg: string,
  ) {
    if (!selectedOrgId) return;
    setBusy(label);
    setError(null);
    try {
      await fn();
      await loadOrgDetails(selectedOrgId);
      setNotice(msg);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function handleAsk(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedOrgId || !question.trim()) return;
    setBusy("ask");
    try {
      const res = await queryOrganizationIntelligence(selectedOrgId, question.trim());
      setAnswer(res);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Organization Release Command Center"
        description="Multi-repository release coordination, dependency graph impact analysis, repository health rankings, and shared release policies."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {orgs.length > 0 && (
              <select
                aria-label="Select organization"
                value={selectedOrgId}
                onChange={(e) => {
                  const id = e.target.value;
                  setSelectedOrgId(id);
                  void loadOrgDetails(id);
                }}
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-100"
              >
                {orgs.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name} ({o.slug})
                  </option>
                ))}
              </select>
            )}
            <button
              type="button"
              onClick={() => selectedOrgId && void loadOrgDetails(selectedOrgId)}
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

      {loading ? (
        <LoadingSkeleton rows={6} />
      ) : orgs.length === 0 ? (
        <SectionCard title="Create Organization" subtitle="Initialize your first multi-repository organization">
          <form onSubmit={(e) => void handleCreateOrg(e)} className="flex flex-wrap gap-2">
            <input
              type="text"
              required
              value={newOrgName}
              onChange={(e) => setNewOrgName(e.target.value)}
              placeholder="Organization name (e.g. NovaShop)"
              className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100"
            />
            <input
              type="text"
              value={newOrgSlug}
              onChange={(e) => setNewOrgSlug(e.target.value)}
              placeholder="Slug (e.g. novashop)"
              className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100"
            />
            <button
              type="submit"
              disabled={busy === "create-org"}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3.5 py-2 text-xs font-semibold text-white hover:bg-indigo-400"
            >
              <Plus className="h-3.5 w-3.5" />
              Create Organization
            </button>
          </form>
        </SectionCard>
      ) : (
        <>
          {overview && (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <MetricCard label="Repositories" value={overview.total_repositories} />
              <MetricCard
                label="Total Releases"
                value={overview.total_releases}
                subtext={`${overview.active_releases} active, ${overview.blocked_releases} blocked`}
              />
              <MetricCard
                label="Change Failure Rate"
                value={
                  overview.change_failure_rate !== null &&
                  overview.change_failure_rate !== undefined
                    ? `${(overview.change_failure_rate * 100).toFixed(1)}%`
                    : "N/A"
                }
                subtext={`${overview.failed_releases} failed`}
              />
              <MetricCard
                label="Pending Approvals"
                value={overview.pending_approvals}
                subtext={`${overview.active_rollouts} active rollouts`}
              />
            </div>
          )}

          <div className="grid gap-6 lg:grid-cols-2">
            {/* Repository Health Rankings */}
            <SectionCard
              title="Repository Health Rankings"
              subtitle="Deterministic reliability scores with explicit insufficient-data gating"
            >
              {rankings.length === 0 ? (
                <EmptyState
                  icon={<Building2 className="h-5 w-5" />}
                  title="No repositories registered"
                  description="Register repositories into this organization to compute health rankings."
                />
              ) : (
                <div className="space-y-2.5">
                  {rankings.map((r) => (
                    <div
                      key={r.id}
                      className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/50 p-3 text-xs"
                    >
                      <div>
                        <div className="font-semibold text-slate-100">
                          {String(r.evidence.repository_name || r.repository_id)}
                        </div>
                        <div className="text-slate-400">
                          Samples: {r.sample_size} | Completeness: {(r.data_completeness * 100).toFixed(0)}%
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-slate-200">
                          {r.health_score !== null && r.health_score !== undefined
                            ? `${r.health_score}/100`
                            : "N/A"}
                        </span>
                        <StatusBadge status={r.health_category} />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </SectionCard>

            {/* Dependency Graph */}
            <SectionCard
              title="Cross-Repository Dependency Graph"
              subtitle="Directed service & library dependencies with topological release order"
            >
              {!graph || graph.edges.length === 0 ? (
                <EmptyState
                  icon={<GitBranch className="h-5 w-5" />}
                  title="No cross-repository dependencies"
                  description="Add repository dependency edges to compute release ordering and impact assessments."
                />
              ) : (
                <div className="space-y-3 text-xs">
                  {graph.has_cycles && (
                    <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-2 text-rose-200">
                      Dependency cycle detected — coordinated plan blocked until resolved.
                    </div>
                  )}
                  <div className="space-y-1.5">
                    {graph.edges.map((e) => (
                      <div
                        key={e.id}
                        className="flex items-center justify-between rounded border border-slate-800 bg-slate-900/50 px-3 py-2"
                      >
                        <span className="font-mono text-slate-200">
                          {e.source_repository_id.slice(0, 8)} -&gt; {e.target_repository_id.slice(0, 8)} (
                          {e.dependency_type})
                        </span>
                        <StatusBadge status={e.verification_status} />
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </SectionCard>
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            {/* Coordinated Release Plans */}
            <SectionCard
              title="Coordinated Release Plans"
              subtitle="Approval-gated multi-repository release orchestration"
            >
              {plans.length === 0 ? (
                <p className="text-xs text-slate-400">No coordinated release plans created yet.</p>
              ) : (
                <div className="space-y-2.5">
                  {plans.map((p) => (
                    <div
                      key={p.id}
                      className="rounded-lg border border-slate-800 bg-slate-900/50 p-3 text-xs"
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-semibold text-slate-100">{p.name}</span>
                        <StatusBadge status={p.status} />
                      </div>
                      <div className="mt-2 flex gap-2">
                        {p.status === "ready" && (
                          <button
                            type="button"
                            disabled={busy !== null}
                            onClick={() =>
                              void handlePlanAction(
                                `approve-${p.id}`,
                                () => approveCoordinatedReleasePlan(p.id),
                                "Coordinated release plan approved.",
                              )
                            }
                            className="rounded bg-indigo-500 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-indigo-400"
                          >
                            Approve Plan
                          </button>
                        )}
                        {p.status === "approved" && (
                          <button
                            type="button"
                            disabled={busy !== null}
                            onClick={() =>
                              void handlePlanAction(
                                `start-${p.id}`,
                                () => startCoordinatedReleasePlan(p.id),
                                "Coordinated release plan started.",
                              )
                            }
                            className="inline-flex items-center gap-1 rounded bg-emerald-600 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-emerald-500"
                          >
                            <Play className="h-3 w-3" />
                            Start Plan
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </SectionCard>

            {/* Shared Organization Policies */}
            <SectionCard
              title="Shared Organization Policies"
              subtitle="Mandatory organization guardrails and compliance governance"
            >
              {policies.length === 0 ? (
                <EmptyState
                  icon={<ShieldCheck className="h-5 w-5" />}
                  title="No organization policies"
                  description="Create shared policies to enforce consistent test, flag, and rollout standards."
                />
              ) : (
                <div className="space-y-2">
                  {policies.map((pol) => (
                    <div
                      key={pol.id}
                      className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/50 p-3 text-xs"
                    >
                      <div>
                        <div className="font-semibold text-slate-100">
                          {pol.name} (v{pol.version})
                        </div>
                        <div className="text-slate-400">
                          {pol.policy_type} | {pol.is_mandatory ? "Mandatory" : "Advisory"}
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <StatusBadge status={pol.status} />
                        {pol.status === "draft" && (
                          <button
                            type="button"
                            disabled={busy !== null}
                            onClick={() =>
                              void handlePlanAction(
                                `pub-${pol.id}`,
                                () => publishOrganizationPolicy(selectedOrgId, pol.id),
                                "Organization policy published.",
                              )
                            }
                            className="rounded bg-indigo-500 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-indigo-400"
                          >
                            Publish
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </SectionCard>
          </div>

          {/* Organization Assistant */}
          <SectionCard
            title="Organization Intelligence Assistant"
            subtitle="Ask cross-repository risk, dependency, and release questions"
          >
            <form onSubmit={(e) => void handleAsk(e)} className="flex flex-wrap gap-2">
              <input
                type="text"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="e.g. Which shared dependencies are hotspots?"
                className="min-w-[260px] flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100"
              />
              <button
                type="submit"
                disabled={busy === "ask"}
                className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-400"
              >
                <Search className="h-3.5 w-3.5" />
                Ask Organization
              </button>
            </form>
            {answer && (
              <div className="mt-3 rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-xs text-slate-200">
                {answer.direct_answer}
              </div>
            )}
          </SectionCard>
        </>
      )}
    </div>
  );
}
