"use client";

import React, { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  GitBranch,
  Plus,
  RefreshCw,
  ShieldAlert,
  X,
} from "lucide-react";
import {
  createRollout,
  getErrorMessage,
  listExperiments,
  listFeatureFlags,
  listReleases,
  listRollouts,
} from "@/lib/api";
import type {
  Experiment,
  FeatureFlag,
  ProgressiveRolloutRecord,
  ProgressiveRolloutStatus,
  ReleaseRecord,
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
import { EnvironmentBadge, StatusBadge } from "@/components/StatusBadges";

export default function ProgressiveDeliveryPage() {
  const router = useRouter();
  const [rollouts, setRollouts] = useState<ProgressiveRolloutRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [envFilter, setEnvFilter] = useState<string>("");
  const [refreshTick, setRefreshTick] = useState(0);

  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [flags, setFlags] = useState<FeatureFlag[]>([]);
  const [releases, setReleases] = useState<ReleaseRecord[]>([]);
  const [experiments, setExperiments] = useState<Experiment[]>([]);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [selectedFlagId, setSelectedFlagId] = useState("");
  const [selectedReleaseId, setSelectedReleaseId] = useState("");
  const [selectedExperimentId, setSelectedExperimentId] = useState("");
  const [environment, setEnvironment] = useState("development");
  const [primaryMetricKey, setPrimaryMetricKey] = useState("checkout_completed");

  useEffect(() => {
    let active = true;
    listRollouts({
      status: (statusFilter || undefined) as ProgressiveRolloutStatus | undefined,
      environment: envFilter || undefined,
      limit: 50,
    })
      .then((res) => {
        if (!active) return;
        setRollouts(Array.isArray(res.items) ? res.items : []);
        setTotal(res.total ?? 0);
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
  }, [statusFilter, envFilter, refreshTick]);

  useEffect(() => {
    if (!showCreate) return;
    let active = true;
    Promise.all([
      listFeatureFlags({ limit: 50 }).catch(() => ({ items: [] })),
      listReleases({ limit: 50 }).catch(() => ({ items: [] })),
      listExperiments().catch(() => ({ items: [] })),
    ]).then(([fRes, rRes, eRes]) => {
      if (!active) return;
      const flagItems = Array.isArray(fRes.items) ? fRes.items : [];
      setFlags(flagItems);
      setReleases(Array.isArray(rRes.items) ? rRes.items : []);
      setExperiments(Array.isArray(eRes.items) ? eRes.items : []);
      if (!selectedFlagId && flagItems.length > 0) {
        setSelectedFlagId(flagItems[0].id);
      }
    });
    return () => {
      active = false;
    };
  }, [showCreate, selectedFlagId]);

  const stats = useMemo(() => {
    const observing = rollouts.filter((r) =>
      ["observing", "evaluating", "advance_recommended"].includes(r.status),
    ).length;
    const completed = rollouts.filter((r) => r.status === "completed").length;
    const breached = rollouts.filter((r) =>
      ["rollback_recommended", "awaiting_rollback_approval", "rolling_back", "rolled_back"].includes(
        r.status,
      ),
    ).length;
    const pendingApproval = rollouts.filter((r) =>
      ["awaiting_approval", "awaiting_rollback_approval"].includes(r.status),
    ).length;
    return { observing, completed, breached, pendingApproval };
  }, [rollouts]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedFlagId) {
      setError("Select a feature flag to orchestrate a progressive rollout.");
      return;
    }
    setCreating(true);
    setError(null);
    try {
      const detail = await createRollout({
        name: name.trim() || "Progressive Rollout",
        description: description.trim() || undefined,
        feature_flag_id: selectedFlagId,
        environment,
        release_id: selectedReleaseId || undefined,
        experiment_id: selectedExperimentId || undefined,
        primary_metric_key: primaryMetricKey,
      });
      setShowCreate(false);
      router.push(`/progressive-delivery/${detail.rollout.id}`);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Progressive Delivery Engine"
        description="Controlled 1% -> 5% -> 10% -> 25% -> 50% -> 100% feature-flag rollouts governed by real telemetry, guardrails, and human approvals."
        actions={
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setRefreshTick((t) => t + 1)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-medium text-slate-200 hover:bg-slate-800"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Refresh
            </button>
            <button
              type="button"
              onClick={() => setShowCreate(true)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3.5 py-2 text-xs font-semibold text-white hover:bg-indigo-400"
            >
              <Plus className="h-3.5 w-3.5" />
              New Rollout
            </button>
          </div>
        }
      />

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard label="Total Rollouts" value={total} subtext="Tracked rollout orchestrations" />
        <MetricCard label="Active / Observing" value={stats.observing} subtext="In observation or ready to advance" />
        <MetricCard label="Pending Approvals" value={stats.pendingApproval} subtext="Awaiting human production sign-off" />
        <MetricCard label="Guardrail / Rolled Back" value={stats.breached} subtext={`${stats.completed} completed to 100%`} />
      </div>

      <SectionCard
        title="Progressive Rollouts"
        subtitle="Filter by rollout status or target environment"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label="Filter by status"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-200"
            >
              <option value="">All Statuses</option>
              <option value="ready">Ready</option>
              <option value="awaiting_approval">Awaiting Approval</option>
              <option value="observing">Observing</option>
              <option value="advance_recommended">Advance Recommended</option>
              <option value="paused">Paused</option>
              <option value="rollback_recommended">Rollback Recommended</option>
              <option value="rolled_back">Rolled Back</option>
              <option value="completed">Completed</option>
            </select>
            <select
              aria-label="Filter by environment"
              value={envFilter}
              onChange={(e) => setEnvFilter(e.target.value)}
              className="rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-200"
            >
              <option value="">All Environments</option>
              <option value="development">Development</option>
              <option value="staging">Staging</option>
              <option value="production">Production</option>
            </select>
          </div>
        }
      >
        {loading ? (
          <LoadingSkeleton rows={5} />
        ) : rollouts.length === 0 ? (
          <EmptyState
            icon={<GitBranch className="h-5 w-5" />}
            title="No progressive rollouts yet"
            description="Create a progressive rollout plan for a feature flag to orchestrate 1% -> 5% -> 10% -> 25% -> 50% -> 100% stages with guardrails."
            action={
              <button
                type="button"
                onClick={() => setShowCreate(true)}
                className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400"
              >
                <Plus className="h-3.5 w-3.5" />
                Create Progressive Rollout
              </button>
            }
          />
        ) : (
          <div className="divide-y divide-slate-800/80">
            {rollouts.map((r) => (
              <Link
                key={r.id}
                href={`/progressive-delivery/${r.id}`}
                className="flex flex-col justify-between gap-3 py-3.5 transition hover:bg-slate-900/50 sm:flex-row sm:items-center"
              >
                <div className="min-w-0 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs text-indigo-300">{r.rollout_key}</span>
                    <span className="text-sm font-semibold text-slate-100">{r.name}</span>
                    <StatusBadge status={r.status} />
                    <EnvironmentBadge environment={r.environment} />
                  </div>
                  <div className="flex flex-wrap items-center gap-3 text-xs text-slate-400">
                    <span>
                      Flag: <code className="text-slate-200">{r.feature_flag_key}</code>
                    </span>
                    <span>Primary metric: {r.primary_metric_key}</span>
                    <span>Updated {formatTimestamp(r.updated_at)}</span>
                  </div>
                </div>
                <div className="flex items-center gap-4">
                  <div className="text-right">
                    <div className="font-mono text-sm font-semibold text-slate-100">
                      {r.current_percentage}% / {r.target_percentage}%
                    </div>
                    <div className="text-[11px] text-slate-400">Current exposure</div>
                  </div>
                  <ArrowRight className="h-4 w-4 text-slate-500" />
                </div>
              </Link>
            ))}
          </div>
        )}
      </SectionCard>

      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <div className="w-full max-w-lg rounded-xl border border-slate-800 bg-slate-950 p-6 shadow-2xl">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold text-slate-100">Create Progressive Rollout</h2>
              <button
                type="button"
                onClick={() => setShowCreate(false)}
                className="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <form onSubmit={(e) => void handleCreate(e)} className="space-y-4">
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-300">Rollout Name</label>
                <input
                  type="text"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Checkout Express Rollout"
                  className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100"
                />
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Feature Flag</label>
                  <select
                    required
                    value={selectedFlagId}
                    onChange={(e) => setSelectedFlagId(e.target.value)}
                    className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100"
                  >
                    <option value="">Select a feature flag...</option>
                    {flags.map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.key}{f.description ? ` — ${f.description.slice(0, 48)}` : ""}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Target Environment</label>
                  <select
                    value={environment}
                    onChange={(e) => setEnvironment(e.target.value)}
                    className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100"
                  >
                    <option value="development">development</option>
                    <option value="staging">staging</option>
                    <option value="production">production (Approval Gated)</option>
                  </select>
                </div>
              </div>
              {environment === "production" && (
                <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-2.5 text-xs text-amber-200">
                  <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>
                    Production rollouts and rollbacks require human approval before mutating Flagsmith.
                  </span>
                </div>
              )}
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Linked Release (optional)</label>
                  <select
                    value={selectedReleaseId}
                    onChange={(e) => setSelectedReleaseId(e.target.value)}
                    className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100"
                  >
                    <option value="">None</option>
                    {releases.map((rel) => (
                      <option key={rel.id} value={rel.id}>
                        {rel.release_key} — {rel.title}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Experiment (optional)</label>
                  <select
                    value={selectedExperimentId}
                    onChange={(e) => setSelectedExperimentId(e.target.value)}
                    className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100"
                  >
                    <option value="">Auto / Latest for flag</option>
                    {experiments.map((exp) => (
                      <option key={exp.id} value={exp.id}>
                        {exp.name} ({exp.status})
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-300">Primary Metric</label>
                <select
                  value={primaryMetricKey}
                  onChange={(e) => setPrimaryMetricKey(e.target.value)}
                  className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100"
                >
                  <option value="checkout_completed">checkout_completed (conversion rate)</option>
                  <option value="payment_failed">payment_failed (error rate)</option>
                  <option value="checkout_abandoned">checkout_abandoned (abandonment rate)</option>
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-300">Description</label>
                <textarea
                  rows={2}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Optional rollout context and success criteria..."
                  className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100"
                />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowCreate(false)}
                  className="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-300 hover:bg-slate-800"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={creating}
                  className="rounded-lg bg-indigo-500 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
                >
                  {creating ? "Creating..." : "Create Rollout (1% -> 100%)"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
