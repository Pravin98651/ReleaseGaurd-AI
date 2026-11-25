"use client";

import React, { useMemo, useSyncExternalStore } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ExperimentAnalysis } from "@/types/releaseguard";

const emptySubscribe = () => () => {};

function useIsHydrated(): boolean {
  return useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false
  );
}

// Lanczos log-gamma approximation for exact Beta PDF plotting of backend alpha/beta posteriors
function logGamma(z: number): number {
  const g = 7;
  const p = [
    0.99999999999980993,
    676.5203681218851,
    -1259.1392167224028,
    771.32342877765313,
    -176.61502916214059,
    12.507343278686905,
    -0.13857109526572012,
    9.9843695780195716e-6,
    1.5056327351493116e-7,
  ];
  if (z < 0.5) {
    return Math.log(Math.PI) - Math.log(Math.sin(Math.PI * z)) - logGamma(1 - z);
  }
  const shifted = z - 1;
  let x = p[0];
  for (let i = 1; i < g + 2; i++) {
    x += p[i] / (shifted + i);
  }
  const t = shifted + g + 0.5;
  return 0.5 * Math.log(2 * Math.PI) + (shifted + 0.5) * Math.log(t) - t + Math.log(x);
}

function betaPdf(x: number, alpha: number, beta: number): number {
  if (x <= 0 || x >= 1 || alpha <= 0 || beta <= 0) return 0;
  const logBeta = logGamma(alpha) + logGamma(beta) - logGamma(alpha + beta);
  const logDensity = (alpha - 1) * Math.log(x) + (beta - 1) * Math.log(1 - x) - logBeta;
  const val = Math.exp(logDensity);
  return Number.isFinite(val) ? val : 0;
}

export function BayesianPosteriorChart({
  analysis,
  controlLabel = "Control",
  treatmentLabel = "Treatment",
}: {
  analysis: ExperimentAnalysis;
  controlLabel?: string;
  treatmentLabel?: string;
}) {
  const hydrated = useIsHydrated();

  const data = useMemo(() => {
    const cMin = analysis.control.credible_interval_95[0];
    const cMax = analysis.control.credible_interval_95[1];
    const tMin = analysis.treatment.credible_interval_95[0];
    const tMax = analysis.treatment.credible_interval_95[1];

    const minX = Math.max(0.001, Math.min(cMin, tMin) * 0.65);
    const maxX = Math.min(0.999, Math.max(cMax, tMax) * 1.35);
    const span = Math.max(0.05, maxX - minX);
    const steps = 64;

    const points = [];
    for (let i = 0; i <= steps; i++) {
      const p = minX + (span * i) / steps;
      points.push({
        ratePct: Number((p * 100).toFixed(2)),
        controlDensity: Number(
          betaPdf(p, analysis.control.posterior_alpha, analysis.control.posterior_beta).toFixed(2)
        ),
        treatmentDensity: Number(
          betaPdf(
            p,
            analysis.treatment.posterior_alpha,
            analysis.treatment.posterior_beta
          ).toFixed(2)
        ),
      });
    }
    return points;
  }, [analysis]);

  return (
    <div className="rounded-xl border border-slate-800/90 bg-[#0d1119] p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-200">
            Beta Posterior Conversion Density
          </h4>
          <p className="text-[11px] text-slate-400">
            Exact Beta(α, β) posteriors from backend exposures &amp; conversions
          </p>
        </div>
        <div className="flex items-center gap-3 text-[11px] tabular-nums">
          <span className="text-sky-300">
            {controlLabel}: {(analysis.control.mean_rate * 100).toFixed(2)}%
          </span>
          <span className="text-emerald-300">
            {treatmentLabel}: {(analysis.treatment.mean_rate * 100).toFixed(2)}%
          </span>
        </div>
      </div>

      <div className="h-[250px] w-full min-w-0">
        {hydrated ? (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
              <defs>
                <linearGradient id="controlFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#38bdf8" stopOpacity={0.35} />
                  <stop offset="95%" stopColor="#38bdf8" stopOpacity={0.02} />
                </linearGradient>
                <linearGradient id="treatmentFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#10b981" stopOpacity={0.35} />
                  <stop offset="95%" stopColor="#10b981" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2738" />
              <XAxis
                dataKey="ratePct"
                tickFormatter={(v) => `${v}%`}
                stroke="#64748b"
                tick={{ fontSize: 11, fill: "#94a3b8" }}
              />
              <YAxis
                stroke="#64748b"
                tick={{ fontSize: 11, fill: "#94a3b8" }}
                width={42}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: "#111622",
                  borderColor: "#2d3a52",
                  borderRadius: "8px",
                  fontSize: "12px",
                }}
                labelFormatter={(val) => `Conversion Rate: ${val}%`}
              />
              <Legend wrapperStyle={{ fontSize: "11px" }} />
              <Area
                type="monotone"
                dataKey="controlDensity"
                name={`${controlLabel} (α=${analysis.control.posterior_alpha}, β=${analysis.control.posterior_beta})`}
                stroke="#38bdf8"
                strokeWidth={2}
                fillOpacity={1}
                fill="url(#controlFill)"
              />
              <Area
                type="monotone"
                dataKey="treatmentDensity"
                name={`${treatmentLabel} (α=${analysis.treatment.posterior_alpha}, β=${analysis.treatment.posterior_beta})`}
                stroke="#10b981"
                strokeWidth={2}
                fillOpacity={1}
                fill="url(#treatmentFill)"
              />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-full w-full animate-pulse rounded-lg bg-slate-900/50" />
        )}
      </div>
    </div>
  );
}

export function ConversionGuardrailChart({
  analysis,
  controlLabel = "Control",
  treatmentLabel = "Treatment",
  maxErrorCeilingPct = 5.0,
}: {
  analysis: ExperimentAnalysis;
  controlLabel?: string;
  treatmentLabel?: string;
  maxErrorCeilingPct?: number;
}) {
  const hydrated = useIsHydrated();

  const chartData = useMemo(() => {
    const pf = analysis.guardrails?.payment_failed;
    const ca = analysis.guardrails?.checkout_abandoned;
    const cAbandonRate =
      ca && analysis.control.exposures > 0
        ? (ca.control_count / analysis.control.exposures) * 100
        : 0;
    const tAbandonRate =
      ca && analysis.treatment.exposures > 0
        ? (ca.treatment_count / analysis.treatment.exposures) * 100
        : 0;

    return [
      {
        metric: "Conversion %",
        control: Number((analysis.control.mean_rate * 100).toFixed(2)),
        treatment: Number((analysis.treatment.mean_rate * 100).toFixed(2)),
      },
      {
        metric: "Payment Error %",
        control: Number(((pf?.control_rate ?? 0) * 100).toFixed(2)),
        treatment: Number(((pf?.treatment_rate ?? 0) * 100).toFixed(2)),
      },
      {
        metric: "Abandoned %",
        control: Number(cAbandonRate.toFixed(2)),
        treatment: Number(tAbandonRate.toFixed(2)),
      },
    ];
  }, [analysis]);

  return (
    <div className="rounded-xl border border-slate-800/90 bg-[#0d1119] p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-200">
            Conversion vs Guardrail Rates
          </h4>
          <p className="text-[11px] text-slate-400">
            Primary checkout completion alongside payment failure &amp; abandonment guardrails
          </p>
        </div>
        <span className="rounded border border-rose-500/30 bg-rose-500/10 px-2 py-0.5 font-mono text-[10px] text-rose-300 tabular-nums">
          Error Ceiling: {maxErrorCeilingPct.toFixed(1)}%
        </span>
      </div>

      <div className="h-[250px] w-full min-w-0">
        {hydrated ? (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2738" />
              <XAxis
                dataKey="metric"
                stroke="#64748b"
                tick={{ fontSize: 11, fill: "#94a3b8" }}
              />
              <YAxis
                tickFormatter={(v) => `${v}%`}
                stroke="#64748b"
                tick={{ fontSize: 11, fill: "#94a3b8" }}
                width={44}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: "#111622",
                  borderColor: "#2d3a52",
                  borderRadius: "8px",
                  fontSize: "12px",
                }}
                formatter={(val) => `${val}%`}
              />
              <Legend wrapperStyle={{ fontSize: "11px" }} />
              <ReferenceLine
                y={maxErrorCeilingPct}
                stroke="#f43f5e"
                strokeDasharray="4 4"
                label={{
                  value: "Max Error",
                  position: "insideTopRight",
                  fill: "#fda4af",
                  fontSize: 10,
                }}
              />
              <Bar dataKey="control" name={controlLabel} fill="#38bdf8" radius={[4, 4, 0, 0]} />
              <Bar dataKey="treatment" name={treatmentLabel} fill="#10b981" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-full w-full animate-pulse rounded-lg bg-slate-900/50" />
        )}
      </div>
    </div>
  );
}
