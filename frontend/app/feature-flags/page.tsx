"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Flag,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  ToggleLeft,
  ToggleRight,
} from "lucide-react";
import {
  createFeatureFlag,
  getErrorMessage,
  listFeatureFlags,
  rollbackFeatureFlag,
  rolloutFeatureFlag,
  runFlagNlCommand,
  syncFeatureFlags,
  toggleFeatureFlag,
} from "@/lib/api";
import type {
  Approval,
  FeatureFlag,
  RolloutStage,
} from "@/types/releaseguard";
import { cn, formatTimestamp, isProductionEnv } from "@/lib/utils";
import {
  ApprovalQueuedBanner,
  EmptyState,
  ErrorBanner,
  LoadingSkeleton,
  Modal,
  PageHeader,
  SectionCard,
  SuccessBanner,
} from "@/components/ui/StatePrimitives";
import { EnvironmentBadge, StatusBadge } from "@/components/StatusBadges";
import { PolicyGateBadge } from "@/components/PolicyGateBadge";
import { RolloutStepper } from "@/components/RolloutStepper";
import { ApprovalActionCard } from "@/components/ApprovalActionCard";

export default function FeatureFlagsPage() {
  const [flags, setFlags] = useState<FeatureFlag[]>([]);
  const [selectedFlag, setSelectedFlag] = useState<FeatureFlag | null>(null);
  const [search, setSearch] = useState("");
  const [envFilter, setEnvFilter] = useState("");
  const [sortBy, setSortBy] = useState<"key" | "health_score" | "updated_at">("key");

  // NL Flag Command
  const [nlCommand, setNlCommand] = useState("");
  const [nlResult, setNlResult] = useState<Record<string, unknown> | null>(null);

  // Create Flag Modal
  const [createOpen, setCreateOpen] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newType, setNewType] = useState<"STANDARD" | "MULTIVARIATE">("STANDARD");
  const [newProjectId, setNewProjectId] = useState("");
  const [mvControlPct, setMvControlPct] = useState(50);
  const [mvTreatmentPct, setMvTreatmentPct] = useState(50);

  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [queuedApproval, setQueuedApproval] = useState<Approval | null>(null);

  const loadFlags = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listFeatureFlags({
        search: search.trim() || undefined,
        environment: envFilter || undefined,
        limit: 100,
      });
      setFlags(res.items);
      if (selectedFlag) {
        const refreshed = res.items.find((f) => f.id === selectedFlag.id) ?? null;
        setSelectedFlag(refreshed ?? res.items[0] ?? null);
      } else if (res.items.length > 0) {
        setSelectedFlag(res.items[0]);
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [envFilter, search, selectedFlag]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void loadFlags();
    }, 0);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [envFilter]);

  const discoveredEnvs = useMemo(() => {
    const set = new Set<string>();
    for (const f of flags) {
      for (const envName of Object.keys(f.env_states ?? {})) {
        set.add(envName);
      }
    }
    const ordered = Array.from(set);
    if (ordered.length === 0) return ["development", "staging", "production"];
    return ordered.sort((a, b) => {
      const rank = (name: string) =>
        name.toLowerCase().includes("dev")
          ? 0
          : name.toLowerCase().includes("stag")
            ? 1
            : isProductionEnv(name)
              ? 3
              : 2;
      return rank(a) - rank(b);
    });
  }, [flags]);

  const sortedFlags = useMemo(() => {
    const copy = [...flags];
    copy.sort((a, b) => {
      if (sortBy === "health_score") return a.health_score - b.health_score;
      if (sortBy === "updated_at") {
        return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
      }
      return a.key.localeCompare(b.key);
    });
    return copy;
  }, [flags, sortBy]);

  const handleSync = async () => {
    setBusyAction("sync");
    setError(null);
    setNotice(null);
    try {
      const res = await syncFeatureFlags();
      await loadFlags();
      setNotice(`Synced ${res.synced_count ?? "latest"} flags from Flagsmith.`);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleCreateFlag = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newKey.trim()) return;
    setBusyAction("create");
    setError(null);
    setNotice(null);
    setQueuedApproval(null);
    try {
      const outcome = await createFeatureFlag({
        project_id: newProjectId.trim() ? Number(newProjectId.trim()) : undefined,
        key: newKey.trim(),
        description: newDesc.trim() || undefined,
        flag_type: newType,
        mv_options:
          newType === "MULTIVARIATE"
            ? [
                { value: "v1", default_percentage_allocation: Number(mvControlPct) },
                { value: "v2", default_percentage_allocation: Number(mvTreatmentPct) },
              ]
            : undefined,
      });
      setCreateOpen(false);
      setNewKey("");
      setNewDesc("");
      if (outcome.execution_status === "approval_required" && outcome.approval) {
        setQueuedApproval(outcome.approval);
      } else {
        setNotice(`Created flag '${newKey.trim()}' (starts disabled in production).`);
        await loadFlags();
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleNlCommand = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!nlCommand.trim()) return;
    setBusyAction("nl");
    setError(null);
    setNotice(null);
    setNlResult(null);
    setQueuedApproval(null);
    try {
      const outcome = await runFlagNlCommand({
        command: nlCommand.trim(),
        environment: envFilter || undefined,
      });
      if (outcome.execution_status === "approval_required" && outcome.approval) {
        setQueuedApproval(outcome.approval);
      } else {
        setNlResult(
          (outcome.data as Record<string, unknown>) ?? {
            task_id: outcome.task_id,
            stream_url: outcome.stream_url,
            execution_status: outcome.execution_status,
          }
        );
        setNotice("Natural-language flag command dispatched.");
        await loadFlags();
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleToggle = async (flag: FeatureFlag, envName: string, currentEnabled: boolean) => {
    setBusyAction(`toggle:${flag.id}:${envName}`);
    setError(null);
    setNotice(null);
    setQueuedApproval(null);
    try {
      const outcome = await toggleFeatureFlag(flag.id, {
        environment_name: envName,
        enabled: !currentEnabled,
        reason: `Operator toggled ${flag.key} in ${envName} via Control Plane`,
      });
      if (outcome.execution_status === "approval_required" && outcome.approval) {
        setQueuedApproval(outcome.approval);
      } else {
        setNotice(
          `Flag '${flag.key}' ${!currentEnabled ? "enabled" : "disabled"} and verified in ${envName}.`
        );
        await loadFlags();
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleRollout = async (
    flag: FeatureFlag,
    envName: string,
    targetPercentage: RolloutStage
  ) => {
    setBusyAction(`rollout:${flag.id}:${envName}`);
    setError(null);
    setNotice(null);
    setQueuedApproval(null);
    try {
      const outcome = await rolloutFeatureFlag(flag.id, {
        environment_name: envName,
        target_percentage: targetPercentage,
        strategy:
          flag.flag_type === "MULTIVARIATE" ? "multivariate" : "percentage_split_segment",
        reason: `Gradual rollout stage ${targetPercentage}% requested from Feature Flags matrix`,
      });
      if (outcome.execution_status === "approval_required" && outcome.approval) {
        setQueuedApproval(outcome.approval);
      } else {
        setNotice(
          `Updated '${flag.key}' in ${envName} to ${targetPercentage}% and verified live state.`
        );
        await loadFlags();
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleRollback = async (flag: FeatureFlag, envName: string) => {
    setBusyAction(`rollback:${flag.id}:${envName}`);
    setError(null);
    setNotice(null);
    setQueuedApproval(null);
    try {
      const outcome = await rollbackFeatureFlag(flag.id, {
        environment_name: envName,
        reason: `Emergency rollback requested on ${flag.key} (${envName})`,
      });
      if (outcome.execution_status === "approval_required" && outcome.approval) {
        setQueuedApproval(outcome.approval);
      } else {
        setNotice(`Rolled back '${flag.key}' in ${envName} to 0% / disabled.`);
        await loadFlags();
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Feature Flags Control"
        description="Multi-environment Flagsmith matrix supporting Feature Versioning v2, % Split segments, Multivariate allocations, and Category B/C gates."
        actions={
          <>
            <button
              type="button"
              disabled={busyAction !== null}
              onClick={handleSync}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-semibold text-slate-200 hover:bg-slate-800 disabled:opacity-50"
            >
              <RefreshCw
                className={cn("h-3.5 w-3.5", busyAction === "sync" && "animate-spin")}
              />
              Sync Flagsmith
            </button>
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-400"
            >
              <Plus className="h-3.5 w-3.5" />
              Create Flag (Cat B)
            </button>
          </>
        }
      />

      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <SuccessBanner message={notice} onDismiss={() => setNotice(null)} />
      <ApprovalQueuedBanner
        approval={queuedApproval}
        onDismiss={() => setQueuedApproval(null)}
      />

      {/* Inline approval card if queued directly */}
      {queuedApproval && (
        <ApprovalActionCard
          approval={queuedApproval}
          highlighted
          onUpdated={() => {
            setQueuedApproval(null);
            void loadFlags();
          }}
        />
      )}

      {/* Natural Language Flag Command Runner */}
      <SectionCard
        title="Natural-Language Flag Command"
        subtitle="Dispatches through flag_agent LangGraph pipeline with strict Category B/C policy gating"
      >
        <form onSubmit={handleNlCommand} className="flex flex-col gap-3 sm:flex-row">
          <input
            type="text"
            value={nlCommand}
            onChange={(e) => setNlCommand(e.target.value)}
            placeholder='e.g. "Roll out checkout_v2 to 25% in staging" or "Disable checkout_v2 in production"'
            className="flex-1 rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
          />
          <button
            type="submit"
            disabled={busyAction !== null || !nlCommand.trim()}
            className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-cyan-500 px-4 py-2 text-xs font-semibold text-slate-950 hover:bg-cyan-400 disabled:opacity-50"
          >
            <Sparkles className="h-3.5 w-3.5" />
            {busyAction === "nl" ? "Dispatching..." : "Execute Command"}
          </button>
        </form>
        {nlResult && (
          <pre className="mt-3 max-h-40 overflow-auto rounded-lg border border-slate-800 bg-[#090c12] p-3 font-mono text-[11px] text-cyan-200">
            {JSON.stringify(nlResult, null, 2)}
          </pre>
        )}
      </SectionCard>

      {/* Search / Filter / Sort */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[220px]">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-3.5 w-3.5 text-slate-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void loadFlags();
            }}
            placeholder="Search flag keys or descriptions..."
            className="w-full rounded-lg border border-slate-700 bg-[#111622] py-2 pl-9 pr-3 text-xs text-slate-100 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
          />
        </div>
        <select
          value={envFilter}
          onChange={(e) => setEnvFilter(e.target.value)}
          className="rounded-lg border border-slate-700 bg-[#111622] px-3 py-2 text-xs text-slate-200"
        >
          <option value="">All Environments</option>
          {discoveredEnvs.map((env) => (
            <option key={env} value={env}>
              {env}
            </option>
          ))}
        </select>
        <select
          value={sortBy}
          onChange={(e) =>
            setSortBy(e.target.value as "key" | "health_score" | "updated_at")
          }
          className="rounded-lg border border-slate-700 bg-[#111622] px-3 py-2 text-xs text-slate-200"
        >
          <option value="key">Sort: Key (A-Z)</option>
          <option value="health_score">Sort: Lowest Health Score</option>
          <option value="updated_at">Sort: Recently Updated</option>
        </select>
        <button
          type="button"
          onClick={() => void loadFlags()}
          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-medium text-slate-200 hover:bg-slate-800"
        >
          Apply
        </button>
      </div>

      {/* Multi-Environment Matrix + Detail Drawer */}
      <div className="grid gap-6 xl:grid-cols-12">
        <SectionCard
          className="xl:col-span-7"
          title="Multi-Environment Flag Matrix"
          subtitle={`${sortedFlags.length} flags`}
        >
          {loading ? (
            <LoadingSkeleton rows={4} />
          ) : sortedFlags.length === 0 ? (
            <EmptyState
              icon={<Flag className="h-5 w-5" />}
              title="No feature flags synced"
              description="Connect Flagsmith in Settings and click Sync Flagsmith, or create a new flag."
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="border-b border-slate-800 text-[11px] uppercase tracking-wider text-slate-400">
                  <tr>
                    <th className="py-2.5 pr-3">Flag Key</th>
                    <th className="py-2.5 pr-3">Type</th>
                    {discoveredEnvs.map((env) => (
                      <th key={env} className="py-2.5 pr-3">
                        {env}
                      </th>
                    ))}
                    <th className="py-2.5 text-right">Health</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {sortedFlags.map((flag) => {
                    const isSelected = selectedFlag?.id === flag.id;
                    return (
                      <tr
                        key={flag.id}
                        onClick={() => setSelectedFlag(flag)}
                        className={cn(
                          "cursor-pointer transition-colors",
                          isSelected
                            ? "bg-indigo-500/12"
                            : "hover:bg-slate-800/40"
                        )}
                      >
                        <td className="py-3 pr-3">
                          <div className="font-mono font-semibold text-slate-100">
                            {flag.key}
                          </div>
                          {flag.description && (
                            <div className="max-w-[200px] truncate text-[11px] text-slate-400">
                              {flag.description}
                            </div>
                          )}
                        </td>
                        <td className="py-3 pr-3 font-mono text-[11px] text-slate-300">
                          {flag.flag_type}
                        </td>
                        {discoveredEnvs.map((env) => {
                          const st = flag.env_states?.[env];
                          if (!st) {
                            return (
                              <td key={env} className="py-3 pr-3 text-slate-500">
                                —
                              </td>
                            );
                          }
                          return (
                            <td key={env} className="py-3 pr-3">
                              <div className="flex items-center gap-1.5">
                                <StatusBadge
                                  status={st.enabled ? "enabled" : "disabled"}
                                  label={st.enabled ? "ON" : "OFF"}
                                />
                                {typeof st.rollout_percentage === "number" && (
                                  <span className="font-mono text-[11px] text-cyan-300 tabular-nums">
                                    {st.rollout_percentage}%
                                  </span>
                                )}
                              </div>
                            </td>
                          );
                        })}
                        <td className="py-3 text-right font-mono font-semibold tabular-nums">
                          <span
                            className={cn(
                              flag.health_score >= 80
                                ? "text-emerald-300"
                                : flag.health_score >= 55
                                  ? "text-amber-300"
                                  : "text-rose-300"
                            )}
                          >
                            {flag.health_score}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </SectionCard>

        {/* Flag Detail & Controls Panel */}
        <div className="xl:col-span-5">
          {!selectedFlag ? (
            <SectionCard title="Flag Controls">
              <EmptyState
                icon={<Flag className="h-5 w-5" />}
                title="Select a flag"
                description="Click any flag row in the matrix to toggle environments, step gradual rollouts, or inspect Flagsmith versioning & code references."
              />
            </SectionCard>
          ) : (
            <SectionCard
              title={selectedFlag.key}
              subtitle={`Project #${selectedFlag.flagsmith_project_id} • ${selectedFlag.flag_type} • Synced ${formatTimestamp(selectedFlag.last_synced_at)}`}
            >
              <div className="space-y-4">
                {selectedFlag.description && (
                  <p className="text-xs text-slate-300">{selectedFlag.description}</p>
                )}

                {/* Per-Environment Controls */}
                <div className="space-y-3">
                  {Object.entries(selectedFlag.env_states ?? {}).map(
                    ([envName, envState]) => {
                      const prod = isProductionEnv(envName, envState.is_production);
                      const pct =
                        typeof envState.rollout_percentage === "number"
                          ? envState.rollout_percentage
                          : envState.enabled
                            ? 100
                            : 0;
                      return (
                        <div
                          key={envName}
                          className="rounded-xl border border-slate-800 bg-[#0d1119] p-3.5 space-y-3"
                        >
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="flex flex-wrap items-center gap-2">
                              <EnvironmentBadge
                                environment={envName}
                                isProduction={prod}
                              />
                              <PolicyGateBadge category={prod ? "C" : "B"} compact />
                              {envState.use_v2_versioning && (
                                <span className="rounded border border-indigo-500/30 bg-indigo-500/10 px-1.5 py-0.5 font-mono text-[10px] text-indigo-300">
                                  Versioning v2
                                </span>
                              )}
                            </div>

                            <button
                              type="button"
                              disabled={busyAction !== null}
                              onClick={() =>
                                void handleToggle(selectedFlag, envName, envState.enabled)
                              }
                              className={cn(
                                "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-semibold transition-colors disabled:opacity-50",
                                envState.enabled
                                  ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-200 hover:bg-emerald-500/25"
                                  : "border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800"
                              )}
                            >
                              {envState.enabled ? (
                                <ToggleRight className="h-4 w-4 text-emerald-400" />
                              ) : (
                                <ToggleLeft className="h-4 w-4 text-slate-500" />
                              )}
                              <span>{envState.enabled ? "Enabled" : "Disabled"}</span>
                            </button>
                          </div>

                          <RolloutStepper
                            currentPercentage={pct}
                            environmentName={envName}
                            isProduction={prod}
                            busy={busyAction !== null}
                            onSelectStage={(stage) =>
                              void handleRollout(selectedFlag, envName, stage)
                            }
                            onRollback={() => void handleRollback(selectedFlag, envName)}
                          />
                        </div>
                      );
                    }
                  )}
                </div>

                {/* Multivariate Options */}
                {selectedFlag.mv_options?.length > 0 && (
                  <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 text-xs">
                    <div className="mb-1.5 font-semibold text-slate-300">
                      Multivariate Options
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {selectedFlag.mv_options.map((mv, idx) => (
                        <span
                          key={idx}
                          className="rounded border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-[11px] text-cyan-200"
                        >
                          {mv.value}: {mv.default_percentage_allocation}%
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {/* Code References */}
                <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 text-xs">
                  <div className="mb-1.5 font-semibold text-slate-300">
                    Repository SDK References ({selectedFlag.code_references?.length ?? 0})
                  </div>
                  {!selectedFlag.code_references?.length ? (
                    <p className="text-slate-400">
                      No code references recorded yet. Run SDK Scan in Repositories.
                    </p>
                  ) : (
                    <div className="space-y-1.5 max-h-40 overflow-y-auto">
                      {selectedFlag.code_references.map((ref, idx) => (
                        <div
                          key={idx}
                          className="rounded border border-slate-800 bg-[#090c12] px-2.5 py-1.5 font-mono text-[11px]"
                        >
                          <div className="text-cyan-300">
                            {ref.file_path}:{ref.line_number}
                          </div>
                          <div className="truncate text-slate-400">{ref.snippet}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </SectionCard>
          )}
        </div>
      </div>

      {/* Create Flag Modal */}
      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Create Flagsmith Feature Flag"
        subtitle="Category B Policy: Creates project definition disabled in production"
      >
        <form onSubmit={handleCreateFlag} className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-300">
                Flag Key
              </label>
              <input
                type="text"
                required
                value={newKey}
                onChange={(e) => setNewKey(e.target.value)}
                placeholder="checkout_v2"
                className="w-full rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 font-mono text-xs text-slate-100"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-300">
                Flagsmith Project ID (optional)
              </label>
              <input
                type="number"
                value={newProjectId}
                onChange={(e) => setNewProjectId(e.target.value)}
                placeholder="Uses connected default project"
                className="w-full rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 font-mono text-xs text-slate-100"
              />
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-slate-300">
              Flag Type
            </label>
            <select
              value={newType}
              onChange={(e) =>
                setNewType(e.target.value as "STANDARD" | "MULTIVARIATE")
              }
              className="w-full rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 text-xs text-slate-100"
            >
              <option value="STANDARD">STANDARD (Boolean + % Split Segment)</option>
              <option value="MULTIVARIATE">MULTIVARIATE (v1 / v2 Variations)</option>
            </select>
          </div>

          {newType === "MULTIVARIATE" && (
            <div className="grid gap-3 sm:grid-cols-2 rounded-lg border border-slate-800 bg-[#0d1119] p-3">
              <div>
                <label className="mb-1 block text-xs text-slate-300">
                  v1 (Control) Default %
                </label>
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={mvControlPct}
                  onChange={(e) => setMvControlPct(Number(e.target.value))}
                  className="w-full rounded-lg border border-slate-700 bg-[#090c12] px-3 py-1.5 font-mono text-xs text-slate-100"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-slate-300">
                  v2 (Treatment) Default %
                </label>
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={mvTreatmentPct}
                  onChange={(e) => setMvTreatmentPct(Number(e.target.value))}
                  className="w-full rounded-lg border border-slate-700 bg-[#090c12] px-3 py-1.5 font-mono text-xs text-slate-100"
                />
              </div>
            </div>
          )}

          <div>
            <label className="mb-1 block text-xs font-medium text-slate-300">
              Description
            </label>
            <textarea
              rows={2}
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              placeholder="Controls Express 1-page Checkout v2 rollout"
              className="w-full rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 text-xs text-slate-100"
            />
          </div>

          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setCreateOpen(false)}
              className="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-300 hover:bg-slate-800"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={busyAction !== null}
              className="rounded-lg bg-indigo-500 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
            >
              {busyAction === "create" ? "Creating..." : "Create Flag"}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
