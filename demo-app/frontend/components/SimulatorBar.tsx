"use client";

import { AlertOctagon, BarChart3, Play, TrendingUp } from "lucide-react";
import type { SimulationReport } from "@/lib/types";

interface SimulatorBarProps {
  running: boolean;
  lastReport: SimulationReport | null;
  onRunSimulation: (scenario: "healthy_v2" | "buggy_v2" | "neutral", usersCount: number) => Promise<void>;
}

export function SimulatorBar({
  running,
  lastReport,
  onRunSimulation,
}: SimulatorBarProps) {
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/80 p-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-sky-400" />
            <h2 className="text-sm font-semibold text-white">
              Traffic &amp; Experiment Simulator
            </h2>
          </div>
          <p className="mt-0.5 text-xs text-slate-400">
            Generate deterministic shopper cohorts through Flagsmith evaluation and push real batches to ReleaseGuard AI.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={running}
            onClick={() => onRunSimulation("healthy_v2", 120)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50 transition"
          >
            <TrendingUp className="h-3.5 w-3.5" />
            {running ? "Running..." : "Run 120 Healthy Checkouts"}
          </button>

          <button
            type="button"
            disabled={running}
            onClick={() => onRunSimulation("buggy_v2", 120)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-rose-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-rose-500 disabled:opacity-50 transition"
          >
            <AlertOctagon className="h-3.5 w-3.5" />
            Simulate Payment Regression
          </button>

          <button
            type="button"
            disabled={running}
            onClick={() => onRunSimulation("neutral", 100)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs font-medium text-slate-200 hover:bg-slate-700 disabled:opacity-50 transition"
          >
            <Play className="h-3.5 w-3.5" />
            Run 100 Neutral Baseline
          </button>
        </div>
      </div>

      {lastReport && (
        <div className="mt-3 grid grid-cols-1 gap-2 border-t border-slate-800 pt-3 sm:grid-cols-3 text-xs">
          <div className="rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2">
            <div className="text-slate-400">Control ({lastReport.control.variation_key})</div>
            <div className="mt-0.5 font-mono text-sm font-semibold text-slate-100">
              {(lastReport.control.conversion_rate * 100).toFixed(1)}% conv ·{" "}
              {(lastReport.control.error_rate * 100).toFixed(1)}% err
            </div>
            <div className="font-mono text-[11px] text-slate-500">
              {lastReport.control.completed}/{lastReport.control.exposures} orders ·{" "}
              {lastReport.control.payment_failed} failed
            </div>
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2">
            <div className="text-slate-400">Treatment ({lastReport.treatment.variation_key})</div>
            <div className="mt-0.5 font-mono text-sm font-semibold text-slate-100">
              {(lastReport.treatment.conversion_rate * 100).toFixed(1)}% conv ·{" "}
              <span
                className={
                  lastReport.treatment.error_rate >= 0.05 ? "text-rose-400" : "text-emerald-400"
                }
              >
                {(lastReport.treatment.error_rate * 100).toFixed(1)}% err
              </span>
            </div>
            <div className="font-mono text-[11px] text-slate-500">
              {lastReport.treatment.completed}/{lastReport.treatment.exposures} orders ·{" "}
              {lastReport.treatment.payment_failed} failed
            </div>
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2">
            <div className="text-slate-400">
              ReleaseGuard Ingest ({lastReport.evaluation_mode})
            </div>
            <div className="mt-0.5 font-mono text-sm font-semibold text-slate-100">
              {lastReport.ingest_accepted} accepted / {lastReport.events_generated} sent
            </div>
            <div className="font-mono text-[11px] text-slate-500">
              {lastReport.ingest_error
                ? lastReport.ingest_error
                : `Batches: ${lastReport.batches_sent} · Deduplicated: ${lastReport.ingest_deduplicated}`}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
