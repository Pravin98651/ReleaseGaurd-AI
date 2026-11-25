"use client";

import React, { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { CheckSquare, RefreshCw } from "lucide-react";
import { getErrorMessage, listApprovals } from "@/lib/api";
import type { Approval, ApprovalStatus } from "@/types/releaseguard";
import { cn } from "@/lib/utils";
import {
  EmptyState,
  ErrorBanner,
  LoadingSkeleton,
  PageHeader,
} from "@/components/ui/StatePrimitives";
import { ApprovalActionCard } from "@/components/ApprovalActionCard";

const STATUS_FILTERS: Array<{ value: "" | ApprovalStatus; label: string }> = [
  { value: "", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "executing", label: "Executing" },
  { value: "executed", label: "Executed" },
  { value: "rejected", label: "Rejected" },
  { value: "expired", label: "Expired" },
  { value: "revalidation_failed", label: "Revalidation Failed" },
  { value: "failed", label: "Failed" },
  { value: "cancelled", label: "Cancelled" },
];

function ApprovalsInner() {
  const searchParams = useSearchParams();
  const highlightId = searchParams.get("highlight") ?? "";

  const [statusFilter, setStatusFilter] = useState<"" | ApprovalStatus>("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [envFilter, setEnvFilter] = useState("");
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadApprovals = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listApprovals({
        status: statusFilter || undefined,
        policy_category: categoryFilter || undefined,
        environment_name: envFilter || undefined,
        limit: 100,
      });
      setApprovals(res.items);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [categoryFilter, envFilter, statusFilter]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void loadApprovals();
    }, 0);
    return () => clearTimeout(timer);
  }, [loadApprovals]);

  const handleUpdated = (updated: Approval) => {
    setApprovals((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Approval Center"
        description="Single-use, SHA-256 hash-bound authorization queue for Category B/C actions with TTL enforcement and live-state precondition revalidation."
        actions={
          <button
            type="button"
            onClick={() => void loadApprovals()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-medium text-slate-200 hover:bg-slate-800"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh Queue
          </button>
        }
      />

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {/* Status Filter Pills */}
      <div className="space-y-3">
        <div className="flex flex-wrap gap-1.5">
          {STATUS_FILTERS.map((st) => (
            <button
              key={st.label}
              type="button"
              onClick={() => setStatusFilter(st.value)}
              className={cn(
                "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                statusFilter === st.value
                  ? "border-indigo-500/50 bg-indigo-500/20 text-indigo-200"
                  : "border-slate-800 bg-[#111622] text-slate-400 hover:border-slate-700 hover:text-slate-200"
              )}
            >
              {st.label}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap gap-3">
          <select
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
            className="rounded-lg border border-slate-700 bg-[#111622] px-3 py-1.5 text-xs text-slate-200"
          >
            <option value="">All Policy Categories</option>
            <option value="C">Category C (Production / Destructive)</option>
            <option value="B">Category B (Non-Prod / Draft PR)</option>
          </select>
          <input
            type="text"
            value={envFilter}
            onChange={(e) => setEnvFilter(e.target.value)}
            placeholder="Filter by environment (e.g. production)..."
            className="rounded-lg border border-slate-700 bg-[#111622] px-3 py-1.5 text-xs text-slate-200 placeholder:text-slate-500"
          />
        </div>
      </div>

      {loading ? (
        <LoadingSkeleton rows={4} />
      ) : approvals.length === 0 ? (
        <EmptyState
          icon={<CheckSquare className="h-5 w-5" />}
          title="No matching approvals"
          description="Approval requests appear here whenever a Category B (if confirmation enabled) or Category C action is proposed."
        />
      ) : (
        <div className="space-y-4">
          {approvals.map((app) => (
            <ApprovalActionCard
              key={app.id}
              approval={app}
              highlighted={highlightId === app.id}
              onUpdated={handleUpdated}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function ApprovalsPage() {
  return (
    <Suspense fallback={<LoadingSkeleton rows={4} />}>
      <ApprovalsInner />
    </Suspense>
  );
}
