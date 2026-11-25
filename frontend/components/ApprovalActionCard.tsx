"use client";

import React, { useEffect, useState } from "react";
import { Check, Clock, Copy, Fingerprint, XCircle } from "lucide-react";
import type { Approval } from "@/types/releaseguard";
import { approveAction, getErrorMessage, rejectAction } from "@/lib/api";
import { cn, formatTimestamp, truncateHash } from "@/lib/utils";
import { PolicyGateBadge } from "@/components/PolicyGateBadge";
import { EnvironmentBadge, StatusBadge } from "@/components/StatusBadges";
import { triggerControlPlaneRefresh } from "@/hooks/use-control-plane-status";

function formatRemaining(ms: number): string {
  if (ms <= 0) return "Expired";
  const totalSec = Math.floor(ms / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${min}m ${String(sec).padStart(2, "0")}s`;
}

export function ApprovalActionCard({
  approval,
  onUpdated,
  highlighted = false,
  compact = false,
}: {
  approval: Approval;
  onUpdated?: (updated: Approval) => void;
  highlighted?: boolean;
  compact?: boolean;
}) {
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [nowMs, setNowMs] = useState<number | null>(null);

  useEffect(() => {
    const syncTimer = setTimeout(() => setNowMs(Date.now()), 0);
    const interval = setInterval(() => setNowMs(Date.now()), 1000);
    return () => {
      clearTimeout(syncTimer);
      clearInterval(interval);
    };
  }, []);

  const expiresAtMs = new Date(approval.expires_at).getTime();
  const remainingMs =
    nowMs !== null && !Number.isNaN(expiresAtMs) ? expiresAtMs - nowMs : null;
  const isExpiredByClock = remainingMs !== null && remainingMs <= 0;
  const isPending = approval.status === "pending" && !isExpiredByClock;

  const handleCopyHash = async () => {
    try {
      await navigator.clipboard.writeText(approval.action_hash);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard unavailable in restricted browser contexts
    }
  };

  const handleApprove = async () => {
    setError(null);
    setSubmitting("approve");
    try {
      const updated = await approveAction(approval.id, {
        comment: comment.trim() || undefined,
      });
      triggerControlPlaneRefresh();
      onUpdated?.(updated);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSubmitting(null);
    }
  };

  const handleReject = async () => {
    setError(null);
    const reason = comment.trim() || "Rejected by operator";
    setSubmitting("reject");
    try {
      const updated = await rejectAction(approval.id, { comment: reason });
      triggerControlPlaneRefresh();
      onUpdated?.(updated);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <div
      className={cn(
        "rounded-xl border bg-[#111622] p-4 transition-colors space-y-3.5",
        highlighted
          ? "border-amber-500/60 ring-1 ring-amber-500/30"
          : isPending
            ? "border-slate-700/90"
            : "border-slate-800/80"
      )}
    >
      {/* Top Row */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <PolicyGateBadge category={approval.policy_category} />
            <StatusBadge
              status={
                approval.status === "pending" && isExpiredByClock
                  ? "expired"
                  : approval.status
              }
            />
            {approval.environment_name && (
              <EnvironmentBadge environment={approval.environment_name} />
            )}
          </div>
          <h3 className="text-sm font-semibold text-slate-100">{approval.title}</h3>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-400">
            <span className="font-mono text-slate-300">{approval.action_type}</span>
            <span>
              Target:{" "}
              <span className="font-mono text-slate-300">
                {approval.target_resource_type}/{approval.target_resource_id}
              </span>
            </span>
            <span className="tabular-nums">
              Created: {formatTimestamp(approval.created_at)}
            </span>
          </div>
        </div>

        {/* TTL countdown */}
        <div className="flex flex-col items-end gap-1">
          {approval.status === "pending" ? (
            <div
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 font-mono text-xs tabular-nums",
                isExpiredByClock
                  ? "border-rose-500/40 bg-rose-500/10 text-rose-300"
                  : "border-amber-500/35 bg-amber-500/10 text-amber-200"
              )}
            >
              <Clock className="h-3.5 w-3.5" />
              <span>
                {remainingMs === null ? "TTL…" : `TTL ${formatRemaining(remainingMs)}`}
              </span>
            </div>
          ) : (
            <span className="font-mono text-[11px] text-slate-500 tabular-nums">
              {approval.decided_at
                ? `Decided ${formatTimestamp(approval.decided_at)}`
                : `Expires ${formatTimestamp(approval.expires_at)}`}
            </span>
          )}
          <button
            type="button"
            onClick={handleCopyHash}
            title={`Full SHA-256 action_hash: ${approval.action_hash}`}
            className="inline-flex items-center gap-1 rounded border border-slate-800 bg-[#0d1119] px-2 py-0.5 font-mono text-[11px] text-slate-400 hover:border-slate-700 hover:text-slate-200"
          >
            <Fingerprint className="h-3 w-3 text-indigo-400" />
            <span>{truncateHash(approval.action_hash, 12)}</span>
            {copied ? (
              <Check className="h-3 w-3 text-emerald-400" />
            ) : (
              <Copy className="h-3 w-3" />
            )}
          </button>
        </div>
      </div>

      {approval.rationale && (
        <p className="rounded-lg border border-slate-800/80 bg-[#0d1119] px-3 py-2 text-xs leading-relaxed text-slate-300">
          {approval.rationale}
        </p>
      )}

      {/* Precondition + Payload */}
      {!compact && (
        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-lg border border-slate-800/80 bg-[#0b0f17] p-3">
            <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              Expected Precondition (Live Revalidated)
            </div>
            <pre className="max-h-36 overflow-auto font-mono text-[11px] text-cyan-200/90">
              {JSON.stringify(approval.expected_precondition ?? {}, null, 2)}
            </pre>
          </div>
          <div className="rounded-lg border border-slate-800/80 bg-[#0b0f17] p-3">
            <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              Normalized Action Payload
            </div>
            <pre className="max-h-36 overflow-auto font-mono text-[11px] text-slate-300">
              {JSON.stringify(approval.action_payload ?? {}, null, 2)}
            </pre>
          </div>
        </div>
      )}

      {approval.execution_result && (
        <details className="rounded-lg border border-slate-800 bg-[#0b0f17] text-xs">
          <summary className="cursor-pointer px-3 py-2 font-medium text-slate-300">
            Execution & Verification Result
          </summary>
          <pre className="max-h-48 overflow-auto border-t border-slate-800 p-3 font-mono text-[11px] text-emerald-200">
            {JSON.stringify(approval.execution_result, null, 2)}
          </pre>
        </details>
      )}

      {approval.decision_comment && approval.status !== "pending" && (
        <div className="text-xs text-slate-400">
          <span className="font-medium text-slate-300">Reviewer comment:</span>{" "}
          {approval.decision_comment}
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
          {error}
        </div>
      )}

      {isPending && (
        <div className="flex flex-col gap-2.5 border-t border-slate-800/80 pt-3 sm:flex-row sm:items-center">
          <input
            type="text"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Optional approval note or required rejection rationale..."
            className="flex-1 rounded-lg border border-slate-700 bg-[#090c12] px-3 py-1.5 text-xs text-slate-100 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
          />
          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              disabled={submitting !== null}
              onClick={handleReject}
              className="inline-flex items-center gap-1.5 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 text-xs font-semibold text-rose-200 hover:bg-rose-500/20 disabled:opacity-50"
            >
              <XCircle className="h-3.5 w-3.5" />
              {submitting === "reject" ? "Rejecting..." : "Reject"}
            </button>
            <button
              type="button"
              disabled={submitting !== null}
              onClick={handleApprove}
              className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-500 px-3.5 py-1.5 text-xs font-semibold text-slate-950 hover:bg-emerald-400 disabled:opacity-50"
            >
              <Check className="h-3.5 w-3.5" />
              {submitting === "approve" ? "Executing..." : "Approve & Execute"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
