"use client";

import { useState } from "react";
import { Activity, ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import type { TelemetryEventRecord } from "@/lib/types";

interface TelemetryInspectorProps {
  events: TelemetryEventRecord[];
  totalBuffered: number;
  releaseGuardApiUrl: string;
  onRefresh: () => void;
}

const EVENT_COLORS: Record<string, string> = {
  feature_variation_assigned: "border-sky-500/40 bg-sky-500/10 text-sky-300",
  checkout_started: "border-indigo-500/40 bg-indigo-500/10 text-indigo-300",
  checkout_completed: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  checkout_abandoned: "border-amber-500/40 bg-amber-500/10 text-amber-300",
  payment_failed: "border-rose-500/40 bg-rose-500/10 text-rose-300",
};

const DELIVERY_COLORS: Record<string, string> = {
  delivered: "text-emerald-400",
  offline: "text-amber-400",
  error: "text-rose-400",
};

export function TelemetryInspector({
  events,
  totalBuffered,
  releaseGuardApiUrl,
  onRefresh,
}: TelemetryInspectorProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/75 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-3">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-emerald-400" />
          <h3 className="text-sm font-semibold text-white">Live Event Telemetry Inspector</h3>
          <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-[11px] text-slate-300">
            {totalBuffered} buffered
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="font-mono text-[11px] text-slate-400">
            Target: {releaseGuardApiUrl}/api/v1/events/ingest
          </span>
          <button
            type="button"
            onClick={onRefresh}
            className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-800 px-2.5 py-1 text-xs text-slate-200 hover:bg-slate-700"
          >
            <RefreshCw className="h-3 w-3" />
            Refresh
          </button>
        </div>
      </div>

      {events.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-800 py-8 text-center text-xs text-slate-400">
          No checkout events emitted yet. Switch user ID, complete a checkout, or click a Simulator preset above.
        </div>
      ) : (
        <div className="max-h-80 overflow-y-auto space-y-1.5 pr-1">
          {events.map((ev) => {
            const isOpen = expandedId === ev.event_id;
            return (
              <div
                key={ev.event_id}
                className="rounded-lg border border-slate-800/90 bg-slate-950/70 text-xs"
              >
                <button
                  type="button"
                  onClick={() => setExpandedId(isOpen ? null : ev.event_id)}
                  className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left hover:bg-slate-900/70"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    {isOpen ? (
                      <ChevronDown className="h-3.5 w-3.5 text-slate-500" />
                    ) : (
                      <ChevronRight className="h-3.5 w-3.5 text-slate-500" />
                    )}
                    <span
                      className={`rounded border px-1.5 py-0.5 font-mono text-[11px] ${
                        EVENT_COLORS[ev.event_type] ?? "border-slate-700 text-slate-300"
                      }`}
                    >
                      {ev.event_type}
                    </span>
                    <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[11px] text-slate-300">
                      {ev.variation_key}
                    </span>
                    <span className="font-mono text-slate-400">{ev.user_identifier}</span>
                  </div>
                  <div className="flex items-center gap-3 font-mono text-[11px]">
                    {ev.numeric_value !== null && ev.numeric_value !== undefined && (
                      <span className="text-slate-300">${ev.numeric_value.toFixed(2)}</span>
                    )}
                    <span className={DELIVERY_COLORS[ev.delivery_status] ?? "text-slate-400"}>
                      {ev.delivery_status}
                    </span>
                    <span className="text-slate-500">
                      {new Date(ev.occurred_at).toLocaleTimeString()}
                    </span>
                  </div>
                </button>
                {isOpen && (
                  <pre className="border-t border-slate-800/80 bg-slate-950 px-3 py-2 font-mono text-[11px] text-slate-300 overflow-x-auto">
                    {JSON.stringify(ev, null, 2)}
                  </pre>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
