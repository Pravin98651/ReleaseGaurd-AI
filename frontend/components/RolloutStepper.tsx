"use client";

import React from "react";
import { ArrowRight, RotateCcw } from "lucide-react";
import type { RolloutStage } from "@/types/releaseguard";
import { cn, isProductionEnv } from "@/lib/utils";
import { PolicyGateBadge } from "@/components/PolicyGateBadge";

const STAGES: RolloutStage[] = [10, 25, 50, 75, 100];

export function RolloutStepper({
  currentPercentage,
  environmentName,
  isProduction,
  busy = false,
  onSelectStage,
  onRollback,
}: {
  currentPercentage: number;
  environmentName: string;
  isProduction?: boolean;
  busy?: boolean;
  onSelectStage: (stage: RolloutStage) => void;
  onRollback?: () => void;
}) {
  const isProd = isProductionEnv(environmentName, isProduction);
  const nextStage = STAGES.find((s) => s > currentPercentage) ?? null;

  return (
    <div className="space-y-3 rounded-xl border border-slate-800 bg-[#0d1119] p-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-slate-200">
            Gradual Rollout Progression
          </span>
          <PolicyGateBadge category={isProd ? "C" : "B"} compact />
        </div>
        <div className="flex items-center gap-2">
          {nextStage !== null && (
            <button
              type="button"
              disabled={busy}
              onClick={() => onSelectStage(nextStage)}
              className={cn(
                "inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-semibold disabled:opacity-50",
                isProd
                  ? "border border-rose-500/40 bg-rose-500/15 text-rose-200 hover:bg-rose-500/25"
                  : "bg-indigo-500 text-white hover:bg-indigo-400"
              )}
            >
              <span>Advance to {nextStage}%</span>
              <ArrowRight className="h-3 w-3" />
            </button>
          )}
          {onRollback && currentPercentage > 0 && (
            <button
              type="button"
              disabled={busy}
              onClick={onRollback}
              className="inline-flex items-center gap-1 rounded-md border border-rose-500/40 bg-rose-500/10 px-2.5 py-1 text-xs font-semibold text-rose-200 hover:bg-rose-500/20 disabled:opacity-50"
            >
              <RotateCcw className="h-3 w-3" />
              <span>Rollback 0%</span>
            </button>
          )}
        </div>
      </div>

      {/* Stage ladder */}
      <div className="grid grid-cols-5 gap-2">
        {STAGES.map((stage) => {
          const reached = currentPercentage >= stage;
          const isCurrent = currentPercentage === stage;
          return (
            <button
              key={stage}
              type="button"
              disabled={busy}
              onClick={() => onSelectStage(stage)}
              className={cn(
                "flex flex-col items-center justify-center rounded-lg border py-2 px-1 text-center transition-colors disabled:opacity-50",
                isCurrent
                  ? "border-indigo-400 bg-indigo-500/20 text-indigo-100"
                  : reached
                    ? "border-emerald-500/35 bg-emerald-500/10 text-emerald-200 hover:bg-emerald-500/15"
                    : "border-slate-800 bg-[#111622] text-slate-400 hover:border-slate-700 hover:text-slate-200"
              )}
            >
              <span className="font-mono text-xs font-semibold tabular-nums">{stage}%</span>
              <span className="mt-0.5 text-[10px] opacity-80">
                {isCurrent ? "Current" : reached ? "Cleared" : "Target"}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
