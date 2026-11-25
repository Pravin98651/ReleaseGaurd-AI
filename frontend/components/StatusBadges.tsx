import React from "react";
import { cn, isProductionEnv } from "@/lib/utils";

const STATUS_TONES: Record<string, string> = {
  // Positive / Terminal Success
  ok: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  connected: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  passed: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  executed: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  completed: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  success: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  verified: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  healthy: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  verified_benefit: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  observed_positive_outcome: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  implemented: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  improving: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  pass: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  sent: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  processed: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  ready_with_warnings: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  insufficient_evidence: "border-slate-700 bg-slate-800/70 text-slate-400",
  dead_letter: "border-rose-500/35 bg-rose-500/10 text-rose-300",
  neutral: "border-slate-600/60 bg-slate-800/80 text-slate-300",
  advance_recommended: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  recommend_advance: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  complete_rollout: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  pr_opened: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  validated: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  enabled: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  ready: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  approved: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  evaluated: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  accepted: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  low: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  moderate: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  elevated: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  partial: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  outdated: "border-slate-600/60 bg-slate-800/80 text-slate-400",
  unavailable: "border-slate-700 bg-slate-800/70 text-slate-400",

  // Active / In Progress
  running: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  tool_running: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  planning: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  resuming: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  retrying: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  recovering: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  waiting_for_approval: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  waiting_for_input: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  pause_requested: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  cancellation_requested: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  timed_out: "border-rose-500/35 bg-rose-500/10 text-rose-300",
  interrupted: "border-rose-500/35 bg-rose-500/10 text-rose-300",
  analyzing: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  initializing: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  validating: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  flag_provisioning: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  experiment_running: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  rolling_out: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  observing: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  evaluating: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  continue_observation: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  monitoring: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  in_progress: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  executing: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  queued: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  open: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  active: "border-sky-500/30 bg-sky-500/10 text-sky-300",

  // Pending / Paused / Warning
  pending: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  pending_review: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  modified: "border-indigo-500/35 bg-indigo-500/10 text-indigo-300",
  inconclusive: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  not_evaluable: "border-slate-700 bg-slate-800/70 text-slate-400",
  insufficient_history: "border-slate-700 bg-slate-800/70 text-slate-400",
  stable: "border-sky-500/30 bg-sky-500/10 text-sky-300",
  degrading: "border-rose-500/35 bg-rose-500/10 text-rose-300",
  negative_outcome: "border-rose-500/35 bg-rose-500/10 text-rose-300",
  waiting: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  warning: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  needs_attention: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  not_ready: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  awaiting_patch: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  ready_for_flag: "border-indigo-500/35 bg-indigo-500/10 text-indigo-300",
  awaiting_approval: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  awaiting_rollback_approval: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  insufficient_data: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  stale_data: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  hold_stage: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  paused_approval: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  paused: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  draft: "border-slate-600/60 bg-slate-800/80 text-slate-300",

  // Negative / Failed / Blocked
  failed: "border-rose-500/35 bg-rose-500/10 text-rose-300",
  validation_failed: "border-rose-500/35 bg-rose-500/10 text-rose-300",
  revalidation_failed: "border-rose-500/35 bg-rose-500/10 text-rose-300",
  rejected: "border-rose-500/35 bg-rose-500/10 text-rose-300",
  blocked: "border-rose-500/35 bg-rose-500/10 text-rose-300",
  error: "border-rose-500/35 bg-rose-500/10 text-rose-300",
  breach: "border-rose-500/35 bg-rose-500/10 text-rose-300",
  rollback_recommended: "border-rose-500/35 bg-rose-500/10 text-rose-300",
  recommend_rollback: "border-rose-500/35 bg-rose-500/10 text-rose-300",
  rolling_back: "border-rose-500/35 bg-rose-500/10 text-rose-300",
  rolled_back: "border-rose-500/35 bg-rose-500/10 text-rose-300",

  // Neutral / Expired / Cancelled
  expired: "border-slate-700 bg-slate-800/70 text-slate-400",
  cancelled: "border-slate-700 bg-slate-800/70 text-slate-400",
  disconnected: "border-slate-700 bg-slate-800/70 text-slate-400",
  closed: "border-slate-700 bg-slate-800/70 text-slate-400",
  disabled: "border-slate-700 bg-slate-800/70 text-slate-400",
  skipped: "border-slate-700 bg-slate-800/70 text-slate-400",
  merged: "border-purple-500/30 bg-purple-500/10 text-purple-300",
};

export function StatusBadge({
  status,
  label,
  className,
}: {
  status: string;
  label?: string;
  className?: string;
}) {
  const key = (status || "unknown").toLowerCase();
  const tone = STATUS_TONES[key] ?? "border-slate-700 bg-slate-800/80 text-slate-300";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium capitalize tracking-tight",
        tone,
        className
      )}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current opacity-85" />
      {label ?? status.replaceAll("_", " ")}
    </span>
  );
}

export function RiskLevelBadge({
  level,
  score,
}: {
  level?: string | null;
  score?: number | null;
}) {
  if (!level) {
    return (
      <span className="inline-flex items-center rounded-md border border-slate-700 bg-slate-800/60 px-2 py-0.5 text-[11px] text-slate-400">
        Unanalyzed
      </span>
    );
  }
  const lower = level.toLowerCase();
  const styles: Record<string, string> = {
    low: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
    medium: "border-amber-500/35 bg-amber-500/10 text-amber-300",
    high: "border-orange-500/40 bg-orange-500/15 text-orange-300",
    critical: "border-rose-500/45 bg-rose-500/15 text-rose-300",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider tabular-nums",
        styles[lower] ?? "border-slate-700 bg-slate-800 text-slate-300"
      )}
    >
      {level}
      {typeof score === "number" ? ` (${score})` : ""}
    </span>
  );
}

export function RecommendationBadge({
  recommendation,
}: {
  recommendation?: string | null;
}) {
  if (!recommendation) {
    return (
      <span className="inline-flex items-center rounded-md border border-slate-700 bg-slate-800/70 px-2 py-0.5 text-[11px] text-slate-400">
        Pending Eval
      </span>
    );
  }
  const styles: Record<string, string> = {
    "Expand Rollout": "border-emerald-500/40 bg-emerald-500/15 text-emerald-200",
    "Maintain Current Rollout": "border-sky-500/35 bg-sky-500/10 text-sky-200",
    "Continue Monitoring": "border-slate-600 bg-slate-800 text-slate-200",
    "Reduce Rollout": "border-amber-500/45 bg-amber-500/15 text-amber-200",
    "Recommend Rollback": "border-rose-500/50 bg-rose-500/20 text-rose-200",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-semibold",
        styles[recommendation] ?? "border-slate-700 bg-slate-800 text-slate-200"
      )}
    >
      {recommendation}
    </span>
  );
}

export function EnvironmentBadge({
  environment,
  isProduction,
}: {
  environment?: string | null;
  isProduction?: boolean;
}) {
  if (!environment) return null;
  const prod = isProductionEnv(environment, isProduction);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[11px] font-medium",
        prod
          ? "border-rose-500/40 bg-rose-500/10 text-rose-300"
          : "border-slate-700 bg-slate-800/80 text-slate-300"
      )}
    >
      {prod ? "PROD" : "NON-PROD"} · {environment}
    </span>
  );
}
