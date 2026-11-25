"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Download, RefreshCw, Rocket, ScrollText } from "lucide-react";
import {
  getAuditLogsExportUrl,
  getErrorMessage,
  listAuditLogs,
  listRolloutHistory,
} from "@/lib/api";
import type { AuditLogEntry, RolloutHistoryEntry } from "@/types/releaseguard";
import { cn, formatTimestamp, truncateHash } from "@/lib/utils";
import {
  EmptyState,
  ErrorBanner,
  LoadingSkeleton,
  PageHeader,
  SectionCard,
} from "@/components/ui/StatePrimitives";
import { PolicyGateBadge } from "@/components/PolicyGateBadge";
import { EnvironmentBadge, StatusBadge } from "@/components/StatusBadges";

export default function AuditLogsPage() {
  const [tab, setTab] = useState<"audit" | "rollouts">("audit");

  // Audit filters
  const [policyCategory, setPolicyCategory] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [eventAction, setEventAction] = useState("");
  const [resourceType, setResourceType] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");

  // Rollout filters
  const [rolloutEnv, setRolloutEnv] = useState("");

  const [audits, setAudits] = useState<AuditLogEntry[]>([]);
  const [rollouts, setRollouts] = useState<RolloutHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (tab === "audit") {
        const res = await listAuditLogs({
          policy_category: policyCategory || undefined,
          status: statusFilter || undefined,
          event_action: eventAction.trim() || undefined,
          resource_type: resourceType.trim() || undefined,
          from: fromDate || undefined,
          to: toDate || undefined,
          limit: 100,
        });
        setAudits(res.items);
      } else {
        const res = await listRolloutHistory({
          environment_name: rolloutEnv.trim() || undefined,
          limit: 100,
        });
        setRollouts(res.items);
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [
    eventAction,
    fromDate,
    policyCategory,
    resourceType,
    rolloutEnv,
    statusFilter,
    tab,
    toDate,
  ]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void loadData();
    }, 0);
    return () => clearTimeout(timer);
  }, [loadData]);

  const jsonExportUrl = getAuditLogsExportUrl({
    format: "json",
    policy_category: policyCategory || undefined,
    status: statusFilter || undefined,
    event_action: eventAction.trim() || undefined,
    resource_type: resourceType.trim() || undefined,
    from: fromDate || undefined,
    to: toDate || undefined,
  });

  const csvExportUrl = getAuditLogsExportUrl({
    format: "csv",
    policy_category: policyCategory || undefined,
    status: statusFilter || undefined,
    event_action: eventAction.trim() || undefined,
    resource_type: resourceType.trim() || undefined,
    from: fromDate || undefined,
    to: toDate || undefined,
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Audit Logs & Rollout History"
        description="Append-only compliance trail of every Category A/B/C/DENY policy evaluation, tool invocation, approval decision, and Flagsmith verification."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <a
              href={jsonExportUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-medium text-slate-200 hover:bg-slate-800"
            >
              <Download className="h-3.5 w-3.5" />
              Export JSON
            </a>
            <a
              href={csvExportUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-medium text-slate-200 hover:bg-slate-800"
            >
              <Download className="h-3.5 w-3.5" />
              Export CSV
            </a>
            <button
              type="button"
              onClick={() => void loadData()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-400"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Refresh
            </button>
          </div>
        }
      />

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {/* Tabs */}
      <div className="flex items-center gap-2 border-b border-slate-800 pb-3">
        <button
          type="button"
          onClick={() => setTab("audit")}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-lg px-3.5 py-2 text-xs font-semibold transition-colors",
            tab === "audit"
              ? "bg-indigo-500/20 text-indigo-200 border border-indigo-500/40"
              : "text-slate-400 hover:bg-slate-800/60 hover:text-slate-200"
          )}
        >
          <ScrollText className="h-3.5 w-3.5" />
          Audit Logs ({audits.length})
        </button>
        <button
          type="button"
          onClick={() => setTab("rollouts")}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-lg px-3.5 py-2 text-xs font-semibold transition-colors",
            tab === "rollouts"
              ? "bg-indigo-500/20 text-indigo-200 border border-indigo-500/40"
              : "text-slate-400 hover:bg-slate-800/60 hover:text-slate-200"
          )}
        >
          <Rocket className="h-3.5 w-3.5" />
          Rollout History ({rollouts.length})
        </button>
      </div>

      {tab === "audit" ? (
        <>
          {/* Audit Filters */}
          <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <select
              value={policyCategory}
              onChange={(e) => setPolicyCategory(e.target.value)}
              className="rounded-lg border border-slate-700 bg-[#111622] px-3 py-2 text-xs text-slate-200"
            >
              <option value="">All Categories</option>
              <option value="A">Category A</option>
              <option value="B">Category B</option>
              <option value="C">Category C</option>
              <option value="DENY">DENY (Forbidden)</option>
            </select>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="rounded-lg border border-slate-700 bg-[#111622] px-3 py-2 text-xs text-slate-200"
            >
              <option value="">All Statuses</option>
              <option value="success">success</option>
              <option value="blocked">blocked</option>
              <option value="failed">failed</option>
              <option value="pending">pending</option>
            </select>
            <input
              type="text"
              value={eventAction}
              onChange={(e) => setEventAction(e.target.value)}
              placeholder="event_action (e.g. approval.approved)"
              className="rounded-lg border border-slate-700 bg-[#111622] px-3 py-2 font-mono text-xs text-slate-100 placeholder:text-slate-500"
            />
            <input
              type="text"
              value={resourceType}
              onChange={(e) => setResourceType(e.target.value)}
              placeholder="resource_type (e.g. feature_flag)"
              className="rounded-lg border border-slate-700 bg-[#111622] px-3 py-2 font-mono text-xs text-slate-100 placeholder:text-slate-500"
            />
            <input
              type="date"
              value={fromDate}
              onChange={(e) => setFromDate(e.target.value)}
              aria-label="From date"
              className="rounded-lg border border-slate-700 bg-[#111622] px-3 py-2 text-xs text-slate-200"
            />
            <input
              type="date"
              value={toDate}
              onChange={(e) => setToDate(e.target.value)}
              aria-label="To date"
              className="rounded-lg border border-slate-700 bg-[#111622] px-3 py-2 text-xs text-slate-200"
            />
          </div>

          <SectionCard title="Chronological Audit Events">
            {loading ? (
              <LoadingSkeleton rows={5} />
            ) : audits.length === 0 ? (
              <EmptyState
                icon={<ScrollText className="h-5 w-5" />}
                title="No audit entries found"
                description="Audit events are automatically appended for every policy decision, tool execution, and integration mutation."
              />
            ) : (
              <div className="space-y-2.5">
                {audits.map((log) => (
                  <details
                    key={log.id}
                    className="rounded-lg border border-slate-800 bg-[#0d1119] text-xs"
                  >
                    <summary className="flex cursor-pointer flex-wrap items-center justify-between gap-2 px-3.5 py-2.5 hover:bg-slate-800/40">
                      <div className="flex flex-wrap items-center gap-2">
                        <PolicyGateBadge category={log.policy_category} compact />
                        <span className="font-mono font-semibold text-slate-100">
                          {log.event_action}
                        </span>
                        <span className="text-slate-400">
                          {log.actor_type}
                          {log.actor_id ? ` (${log.actor_id.slice(0, 12)})` : ""}
                        </span>
                        <span className="font-mono text-cyan-300">
                          {log.resource_type}
                          {log.resource_id ? `/${log.resource_id.slice(0, 12)}` : ""}
                        </span>
                        {log.environment_name && (
                          <EnvironmentBadge environment={log.environment_name} />
                        )}
                      </div>
                      <div className="flex items-center gap-2.5">
                        {log.action_hash && (
                          <span className="font-mono text-[11px] text-slate-500">
                            {truncateHash(log.action_hash, 10)}
                          </span>
                        )}
                        <StatusBadge status={log.status} />
                        <span className="font-mono text-[11px] text-slate-400 tabular-nums">
                          {formatTimestamp(log.created_at)}
                        </span>
                      </div>
                    </summary>
                    <pre className="max-h-56 overflow-auto border-t border-slate-800 bg-[#090c12] p-3 font-mono text-[11px] text-slate-300">
                      {JSON.stringify(log.details ?? {}, null, 2)}
                    </pre>
                  </details>
                ))}
              </div>
            )}
          </SectionCard>
        </>
      ) : (
        <>
          <div className="flex items-center gap-3">
            <input
              type="text"
              value={rolloutEnv}
              onChange={(e) => setRolloutEnv(e.target.value)}
              placeholder="Filter environment (e.g. production, staging)..."
              className="rounded-lg border border-slate-700 bg-[#111622] px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500"
            />
          </div>

          <SectionCard title="Immutable Rollout Timeline">
            {loading ? (
              <LoadingSkeleton rows={4} />
            ) : rollouts.length === 0 ? (
              <EmptyState
                icon={<Rocket className="h-5 w-5" />}
                title="No rollout history entries"
                description="Rollout transitions and post-mutation Flagsmith verification records appear here."
              />
            ) : (
              <div className="space-y-3">
                {rollouts.map((r) => (
                  <details
                    key={r.id}
                    className="rounded-lg border border-slate-800 bg-[#0d1119] text-xs"
                  >
                    <summary className="flex cursor-pointer flex-wrap items-center justify-between gap-2 px-3.5 py-2.5 hover:bg-slate-800/40">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono font-semibold text-slate-100">
                          {r.flag_key || r.feature_flag_id}
                        </span>
                        <EnvironmentBadge environment={r.environment_name} />
                        <span className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] text-cyan-200">
                          {r.action}
                        </span>
                        {r.reason && (
                          <span className="text-slate-400">— {r.reason}</span>
                        )}
                      </div>
                      <div className="flex items-center gap-2.5">
                        <StatusBadge
                          status={r.verified ? "verified" : "failed"}
                          label={r.verified ? "Verified" : "Verification Failed"}
                        />
                        <span className="font-mono text-[11px] text-slate-400 tabular-nums">
                          {formatTimestamp(r.created_at)}
                        </span>
                      </div>
                    </summary>
                    <div className="grid gap-3 border-t border-slate-800 bg-[#090c12] p-3 md:grid-cols-3 font-mono text-[11px]">
                      <div>
                        <div className="mb-1 text-slate-400">previous_state</div>
                        <pre className="overflow-auto text-slate-300">
                          {JSON.stringify(r.previous_state ?? {}, null, 2)}
                        </pre>
                      </div>
                      <div>
                        <div className="mb-1 text-slate-400">new_state</div>
                        <pre className="overflow-auto text-emerald-200">
                          {JSON.stringify(r.new_state ?? {}, null, 2)}
                        </pre>
                      </div>
                      <div>
                        <div className="mb-1 text-slate-400">verification_details</div>
                        <pre className="overflow-auto text-cyan-200">
                          {JSON.stringify(r.verification_details ?? {}, null, 2)}
                        </pre>
                      </div>
                    </div>
                  </details>
                ))}
              </div>
            )}
          </SectionCard>
        </>
      )}
    </div>
  );
}
