import React from "react";
import { Lock, Shield, ShieldAlert, ShieldCheck } from "lucide-react";
import { cn } from "@/lib/utils";

export function PolicyGateBadge({
  category,
  compact = false,
}: {
  category: string;
  compact?: boolean;
}) {
  const norm = (category || "A").toUpperCase();

  if (norm === "A") {
    return (
      <span
        title="Category A: Read / Analyze / Plan (Auto-executed & logged)"
        className={cn(
          "inline-flex items-center gap-1 rounded-md border border-sky-500/30 bg-sky-500/10 px-2 py-0.5 text-[11px] font-semibold text-sky-300"
        )}
      >
        <ShieldCheck className="h-3 w-3" />
        {compact ? "Cat A" : "Category A · Read"}
      </span>
    );
  }

  if (norm === "B") {
    return (
      <span
        title="Category B: Reversible Non-Prod / Draft PR (Configurable Confirmation)"
        className="inline-flex items-center gap-1 rounded-md border border-amber-500/35 bg-amber-500/10 px-2 py-0.5 text-[11px] font-semibold text-amber-300"
      >
        <Shield className="h-3 w-3" />
        {compact ? "Cat B" : "Category B · Non-Prod"}
      </span>
    );
  }

  if (norm === "C") {
    return (
      <span
        title="Category C: Production or Destructive (Mandatory Single-Use Human Approval)"
        className="inline-flex items-center gap-1 rounded-md border border-rose-500/45 bg-rose-500/15 px-2 py-0.5 text-[11px] font-semibold text-rose-200"
      >
        <ShieldAlert className="h-3 w-3" />
        {compact ? "Cat C" : "Category C · Prod Gate"}
      </span>
    );
  }

  return (
    <span
      title="FORBIDDEN: Hard-blocked at Policy Gate (PR merge, force-push, auto-delete)"
      className="inline-flex items-center gap-1 rounded-md border border-red-500/50 bg-red-600/20 px-2 py-0.5 text-[11px] font-bold text-red-200"
    >
      <Lock className="h-3 w-3" />
      {compact ? "DENY" : "FORBIDDEN"}
    </span>
  );
}
