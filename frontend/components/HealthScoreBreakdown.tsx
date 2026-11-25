import React from "react";
import { AlertCircle, CheckCircle2 } from "lucide-react";
import type { HealthIssue } from "@/types/releaseguard";
import { cn } from "@/lib/utils";

export function HealthScoreGauge({
  score,
  label = "Health Score",
}: {
  score: number;
  label?: string;
}) {
  const clamped = Math.max(0, Math.min(100, Math.round(score)));
  const radius = 38;
  const circumference = 2 * Math.PI * radius;
  const dashOffset = circumference - (clamped / 100) * circumference;

  const strokeColor =
    clamped >= 80
      ? "#10b981"
      : clamped >= 55
        ? "#f59e0b"
        : "#f43f5e";

  return (
    <div className="flex items-center gap-4">
      <div className="relative h-24 w-24 shrink-0">
        <svg className="h-24 w-24 -rotate-90" viewBox="0 0 96 96">
          <circle
            cx="48"
            cy="48"
            r={radius}
            fill="transparent"
            stroke="#1e2738"
            strokeWidth="8"
          />
          <circle
            cx="48"
            cy="48"
            r={radius}
            fill="transparent"
            stroke={strokeColor}
            strokeWidth="8"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={dashOffset}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-xl font-bold text-slate-50 tabular-nums">{clamped}</span>
          <span className="text-[10px] text-slate-400">/100</span>
        </div>
      </div>
      <div>
        <div className="text-xs font-medium uppercase tracking-wider text-slate-400">
          {label}
        </div>
        <div className="mt-1 text-sm font-semibold text-slate-100">
          {clamped >= 80
            ? "Healthy Flag Hygiene"
            : clamped >= 55
              ? "Cleanup Recommended"
              : "Critical Debt / Drift"}
        </div>
        <p className="mt-0.5 text-xs text-slate-400">
          Starts at 100; deterministic deductions per detector
        </p>
      </div>
    </div>
  );
}

export function HealthScoreBreakdown({
  score,
  issues,
}: {
  score: number;
  issues: HealthIssue[];
}) {
  return (
    <div className="space-y-2.5">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium text-slate-300">Explainable Score Deductions</span>
        <span
          className={cn(
            "font-mono font-semibold tabular-nums",
            score >= 80
              ? "text-emerald-300"
              : score >= 55
                ? "text-amber-300"
                : "text-rose-300"
          )}
        >
          Score: {score}/100
        </span>
      </div>

      {issues.length === 0 ? (
        <div className="flex items-center gap-2 rounded-lg border border-emerald-500/25 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" />
          <span>No health issues detected across all 7 deterministic checks.</span>
        </div>
      ) : (
        <div className="space-y-2">
          {issues.map((issue, idx) => (
            <div
              key={`${issue.issue_type}-${idx}`}
              className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 text-xs"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <AlertCircle className="h-3.5 w-3.5 text-amber-400" />
                  <span className="rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 font-mono text-[11px] font-semibold uppercase text-amber-200">
                    {issue.issue_type}
                  </span>
                </div>
                <span className="font-mono font-semibold text-rose-300 tabular-nums">
                  {issue.penalty > 0 ? `-${issue.penalty}` : issue.penalty} pts
                </span>
              </div>
              <p className="mt-1.5 text-slate-200">{issue.summary}</p>
              {issue.evidence && (
                <pre className="mt-2 max-h-28 overflow-auto rounded bg-[#090c12] p-2 font-mono text-[11px] text-slate-400">
                  {typeof issue.evidence === "string"
                    ? issue.evidence
                    : JSON.stringify(issue.evidence, null, 2)}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
