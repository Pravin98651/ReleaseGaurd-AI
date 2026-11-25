"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  CheckCircle2,
  Flag,
  FlaskConical,
  HeartPulse,
  RefreshCw,
  ShieldCheck,
  Terminal,
  UserCheck,
} from "lucide-react";
import {
  explainFlagsmithIdentity,
  getErrorMessage,
  getFlagsmithCommandCenter,
  promoteExperimentWinner,
  provisionFlagsmithCatalog,
  runFlagsmithTestHarness,
} from "@/lib/api";
import {
  EmptyState,
  ErrorBanner,
  LoadingSkeleton,
  MetricCard,
  PageHeader,
  SectionCard,
} from "@/components/ui/StatePrimitives";

interface CatalogItem {
  key: string;
  registered_in_flagsmith: boolean;
  flagsmith_feature_id: number | null;
  flag_type: string;
  category: string;
  owner: string;
  purpose: string;
  variants: string[];
  code_references: string[];
  linked_tests: string[];
  primary_metric: string | null;
  inconsistent_environments: boolean;
  health_score: number | null;
  health_status: string;
  stale: boolean;
  cleanup_recommendations: string[];
}

interface HubExperiment {
  id: string;
  name: string;
  hypothesis: string;
  environment_name: string;
  status: string;
  analytics_mode: string;
  flagsmith_experiment_id: string | null;
  control_variation: string;
  treatment_variation: string;
  primary_metric: string;
  latest_recommendation: string | null;
}

interface PendingApprovalRow {
  id: string;
  title: string;
  action_type: string;
  environment_name: string;
  policy_category: string;
  rationale: string;
}

interface ExecutionRow {
  id: string;
  event_action: string;
  resource_type: string;
  environment_name: string | null;
  status: string;
  policy_category: string;
  approval_id: string | null;
  created_at: string;
}

export default function FlagsmithCommandCenterPage() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // Identity Debugger state
  const [envName, setEnvName] = useState("development");
  const [identifier, setIdentifier] = useState("shopper_42");
  const [userType, setUserType] = useState("internal");
  const [plan, setPlan] = useState("enterprise");
  const [identityResult, setIdentityResult] = useState<Record<string, unknown> | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getFlagsmithCommandCenter();
      setData(res);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    getFlagsmithCommandCenter()
      .then((res) => {
        if (!cancelled) {
          setData(res);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(getErrorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleProvision = async () => {
    setBusy("provision");
    setError(null);
    setNotice(null);
    try {
      const res = await provisionFlagsmithCatalog({ include_targeting_segments: true });
      const prov = (res.provisioned as string[]) || [];
      const segs = (res.segments_synced as string[]) || [];
      setNotice(
        `Provisioned ${prov.length} new catalog flags and synced ${segs.length} targeting segments.`
      );
      await loadData();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const handleExplainIdentity = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy("identity");
    setError(null);
    try {
      const res = await explainFlagsmithIdentity({
        environment_name: envName,
        identifier,
        traits: {
          user_type: userType,
          subscription_plan: plan,
          is_internal_tester: userType === "internal",
        },
      });
      setIdentityResult(res);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const handleRunHarness = async (exp: HubExperiment) => {
    setBusy(exp.id);
    setError(null);
    setNotice(null);
    try {
      const res = await runFlagsmithTestHarness(exp.id, { identities_per_arm: 20 });
      setNotice(
        `Synthetic test harness batch ${String(res.batch_id)} evaluated ${String(
          res.identities_evaluated
        )} Flagsmith identities (${String(res.conversions_recorded)} conversions).`
      );
      await loadData();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const handlePromote = async (exp: HubExperiment) => {
    setBusy(`promote:${exp.id}`);
    setError(null);
    setNotice(null);
    try {
      const outcome = await promoteExperimentWinner(exp.id, exp.treatment_variation);
      if (outcome.execution_status === "approval_required" && outcome.approval) {
        setNotice(
          `Queued human approval #${outcome.approval.id.slice(0, 8)} to promote '${exp.treatment_variation}'. Review in Approvals.`
        );
      } else {
        setNotice(`Promoted winning variant for '${exp.name}'.`);
      }
      await loadData();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  if (loading && !data) {
    return <LoadingSkeleton rows={5} />;
  }

  const metrics = (data?.metrics as Record<string, number>) || {};
  const cli = (data?.cli as Record<string, unknown>) || {};
  const catalog = (data?.catalog as CatalogItem[]) || [];
  const experiments = (data?.experiments as HubExperiment[]) || [];
  const approvals = (data?.pending_approvals as PendingApprovalRow[]) || [];
  const executions = (data?.recent_executions as ExecutionRow[]) || [];
  const capabilityFlags = (data?.capability_flags as Array<Record<string, unknown>>) || [];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Flagsmith Command Center"
        description="Unified Flagsmith Feature Flag Catalog, Multivariate Experimentation, Identity Targeting Debugger, Lifecycle Audit, and Human-Approved Winner Rollouts."
        actions={
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => void handleProvision()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
            >
              <Flag className="h-3.5 w-3.5" />
              {busy === "provision" ? "Syncing Catalog..." : "Sync Governed Catalog to Flagsmith"}
            </button>
            <button
              type="button"
              onClick={() => void loadData()}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-medium text-slate-200 hover:bg-slate-800"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Refresh
            </button>
          </div>
        }
      />

      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {notice && (
        <div className="flex items-center gap-2 rounded-xl border border-emerald-500/40 bg-emerald-500/10 px-4 py-3 text-xs text-emerald-200">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" />
          <span>{notice}</span>
        </div>
      )}

      {/* Overview Metrics */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricCard
          label="Flagsmith Project"
          value={data?.project_id ? `#${String(data.project_id)}` : "Not set"}
          subtext={`${metrics.provisioned_in_flagsmith ?? 0} / ${metrics.catalog_flags_total ?? 0} catalog flags provisioned`}
          icon={<Flag className="h-4 w-4" />}
          tone="indigo"
        />
        <MetricCard
          label="Experiments"
          value={`${metrics.active_experiments ?? 0} active`}
          subtext={`${metrics.total_experiments ?? 0} total tracked experiments`}
          icon={<FlaskConical className="h-4 w-4" />}
          tone="sky"
        />
        <MetricCard
          label="Pending Approvals"
          value={metrics.pending_approvals ?? 0}
          subtext={`${metrics.rollouts_in_progress ?? 0} rollouts observing`}
          icon={<ShieldCheck className="h-4 w-4" />}
          tone="amber"
        />
        <MetricCard
          label="Lifecycle Warnings"
          value={metrics.flags_with_warnings ?? 0}
          subtext={`${metrics.stale_flags ?? 0} expired review SLA`}
          icon={<HeartPulse className="h-4 w-4" />}
          tone={metrics.stale_flags ? "rose" : "emerald"}
        />
      </div>

      {/* CLI & Capability Flags Status */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SectionCard
          title="Flagsmith CLI & Capability Integration"
          subtitle="Audited CLI detection & capability switches (auth/RBAC/PolicyEngine remain unaffected)"
          actions={<Terminal className="h-4 w-4 text-cyan-400" />}
        >
          <div className="space-y-2 text-xs text-slate-300">
            <div>
              Status:{" "}
              <span className="font-mono text-emerald-300">
                {cli.cli_binary_found
                  ? `Installed (${String(cli.cli_version || "flagsmith")})`
                  : cli.npx_available
                    ? "@flagsmith/cli available via npx"
                    : "Management API Mode"}
              </span>
            </div>
            <p className="text-[11px] text-slate-400">{String(cli.auth_note || "")}</p>
            <div className="flex flex-wrap gap-2 pt-1">
              {capabilityFlags.map((cf) => (
                <span
                  key={String(cf.flag_key)}
                  className="rounded border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-[11px]"
                >
                  {String(cf.flag_key)}: {cf.enabled ? "ON" : "OFF"} ({String(cf.source)})
                </span>
              ))}
            </div>
          </div>
        </SectionCard>

        {/* Identity & Trait Debugger */}
        <SectionCard
          title="Identity & Targeting Explainer"
          subtitle="Evaluate any shopper ID + traits directly against live Flagsmith Edge"
          actions={<UserCheck className="h-4 w-4 text-indigo-400" />}
        >
          <form onSubmit={(e) => void handleExplainIdentity(e)} className="grid grid-cols-2 gap-2 text-xs">
            <input
              type="text"
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
              placeholder="Identifier (e.g. shopper_42)"
              className="rounded-lg border border-slate-700 bg-[#090c12] px-2.5 py-1.5 font-mono text-slate-100"
            />
            <select
              value={envName}
              onChange={(e) => setEnvName(e.target.value)}
              className="rounded-lg border border-slate-700 bg-[#090c12] px-2.5 py-1.5 text-slate-100"
            >
              <option value="development">development</option>
              <option value="staging">staging</option>
              <option value="production">production</option>
            </select>
            <select
              value={userType}
              onChange={(e) => setUserType(e.target.value)}
              className="rounded-lg border border-slate-700 bg-[#090c12] px-2.5 py-1.5 text-slate-100"
            >
              <option value="internal">user_type: internal</option>
              <option value="qa">user_type: qa</option>
              <option value="beta">user_type: beta</option>
              <option value="consumer">user_type: consumer</option>
            </select>
            <div className="flex gap-2">
              <select
                value={plan}
                onChange={(e) => setPlan(e.target.value)}
                className="flex-1 rounded-lg border border-slate-700 bg-[#090c12] px-2.5 py-1.5 text-slate-100"
              >
                <option value="enterprise">plan: enterprise</option>
                <option value="pro">plan: pro</option>
                <option value="beta">plan: beta</option>
                <option value="free">plan: free</option>
              </select>
              <button
                type="submit"
                disabled={busy === "identity"}
                className="rounded-lg bg-indigo-500 px-3 py-1.5 font-semibold text-white hover:bg-indigo-400"
              >
                Explain
              </button>
            </div>
          </form>
          {identityResult && (
            <div className="mt-3 rounded-lg border border-slate-800 bg-[#090c12] p-2.5 text-[11px] space-y-1">
              <div className="text-emerald-300 font-medium">
                Consistent assignment verified:{" "}
                {identityResult.consistent_on_repeat_evaluation ? "YES" : "NO"}
              </div>
              {((identityResult.flags as Array<Record<string, unknown>>) || []).map((f) => (
                <div key={String(f.flag_key)} className="font-mono text-slate-300">
                  {String(f.flag_key)} = {String(f.value ?? f.enabled)} ({String(f.reason)})
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      </div>

      {/* Feature Flag Explorer & Lifecycle Catalog */}
      <SectionCard
        title="Governed Feature Flag Catalog & Lifecycle"
        subtitle="Every flag lists its owner, safe fallback default, linked code/tests, and cleanup recommendations"
      >
        {catalog.length === 0 ? (
          <EmptyState title="No flags in catalog" description="Sync or create a flag to begin." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-slate-800 text-slate-400">
                <tr>
                  <th className="py-2 pr-3">Flag Key</th>
                  <th className="py-2 pr-3">Category / Type</th>
                  <th className="py-2 pr-3">Owner</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3">Variants / Metric</th>
                  <th className="py-2">Lifecycle Notes</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {catalog.map((item) => (
                  <tr key={item.key}>
                    <td className="py-2 pr-3 font-mono font-semibold text-indigo-300">{item.key}</td>
                    <td className="py-2 pr-3 text-slate-300">
                      {item.category} · {item.flag_type}
                    </td>
                    <td className="py-2 pr-3 text-slate-300">{item.owner}</td>
                    <td className="py-2 pr-3">
                      <span
                        className={
                          item.registered_in_flagsmith ? "text-emerald-300" : "text-amber-300"
                        }
                      >
                        {item.registered_in_flagsmith
                          ? `Provisioned (#${item.flagsmith_feature_id})`
                          : "Unprovisioned"}
                      </span>
                    </td>
                    <td className="py-2 pr-3 font-mono text-[11px] text-slate-400">
                      {item.variants.length ? item.variants.join(", ") : "boolean"}
                      {item.primary_metric ? ` · ${item.primary_metric}` : ""}
                    </td>
                    <td className="py-2 text-[11px] text-slate-400">
                      {item.cleanup_recommendations.length
                        ? item.cleanup_recommendations.join(", ")
                        : "OK (linked code & tests)"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      {/* Experiments & Test Harness */}
      <SectionCard
        title="Multivariate Experiments & Synthetic Test Harness"
        subtitle="Trigger explicitly labelled synthetic test traffic in non-prod or queue human approval to promote winners"
        actions={
          <Link
            href="/experiments"
            className="text-xs font-medium text-indigo-300 hover:underline"
          >
            Open Full Experiments View
          </Link>
        }
      >
        {experiments.length === 0 ? (
          <EmptyState
            title="No experiments yet"
            description="Plan an experiment from /experiments or via natural-language agent."
          />
        ) : (
          <div className="space-y-3">
            {experiments.slice(0, 8).map((exp) => (
              <div
                key={exp.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-800 bg-[#0d1119] p-3 text-xs"
              >
                <div className="space-y-0.5">
                  <div className="font-semibold text-slate-100">
                    {exp.name}{" "}
                    <span className="font-mono text-[11px] text-slate-400">
                      ({exp.environment_name} · {exp.status}
                      {exp.flagsmith_experiment_id ? ` · FS #${exp.flagsmith_experiment_id}` : ""})
                    </span>
                  </div>
                  <div className="text-[11px] text-slate-400">
                    Metric: <span className="font-mono">{exp.primary_metric}</span> · Recommendation:{" "}
                    {exp.latest_recommendation ?? "Pending evaluation"}
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  {exp.environment_name !== "production" && (
                    <button
                      type="button"
                      disabled={busy !== null}
                      onClick={() => void handleRunHarness(exp)}
                      className="rounded-lg border border-amber-500/40 bg-amber-500/15 px-2.5 py-1 text-[11px] font-semibold text-amber-200 hover:bg-amber-500/25 disabled:opacity-50"
                    >
                      {busy === exp.id ? "Running Harness..." : "Run Synthetic Test Harness"}
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => void handlePromote(exp)}
                    className="rounded-lg bg-indigo-500 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
                  >
                    Promote Winner (Requires Approval)
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </SectionCard>

      {/* Approvals & Execution History */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SectionCard
          title="Pending Flagsmith Approvals"
          subtitle="Consequential mutations paused for human sign-off"
          actions={
            <Link href="/approvals" className="text-xs font-medium text-indigo-300 hover:underline">
              Open Approval Queue
            </Link>
          }
        >
          {approvals.length === 0 ? (
            <EmptyState title="No pending approvals" description="All Flagsmith approvals are resolved." />
          ) : (
            <div className="space-y-2 text-xs">
              {approvals.map((app) => (
                <div key={app.id} className="rounded-lg border border-slate-800 bg-[#0d1119] p-2.5">
                  <div className="font-semibold text-slate-100">{app.title}</div>
                  <div className="font-mono text-[11px] text-amber-300">
                    Cat {app.policy_category} · {app.environment_name} · {app.action_type}
                  </div>
                  <p className="mt-1 text-[11px] text-slate-400">{app.rationale}</p>
                </div>
              ))}
            </div>
          )}
        </SectionCard>

        <SectionCard
          title="Recent Flagsmith Execution History"
          subtitle="Audited mutations and verification records"
        >
          {executions.length === 0 ? (
            <EmptyState title="No recent executions" description="Mutations will appear here." />
          ) : (
            <div className="space-y-2 max-h-64 overflow-y-auto text-xs">
              {executions.map((ex) => (
                <div
                  key={ex.id}
                  className="flex items-center justify-between rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2"
                >
                  <div>
                    <div className="font-mono text-slate-200">{ex.event_action}</div>
                    <div className="text-[11px] text-slate-400">
                      {ex.resource_type} · {ex.environment_name ?? "global"} · Cat{" "}
                      {ex.policy_category}
                    </div>
                  </div>
                  <span className="font-mono text-[11px] text-emerald-300">{ex.status}</span>
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      </div>
    </div>
  );
}
