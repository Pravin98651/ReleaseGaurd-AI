"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  CheckSquare,
  ChevronRight,
  Flag,
  GitPullRequest,
  LayoutDashboard,
  Rocket,
} from "lucide-react";
import { cn } from "@/lib/utils";

export interface DemoPathStep {
  step: number;
  label: string;
  shortLabel: string;
  href: string;
  matchPrefixes: string[];
  description: string;
  icon: React.ComponentType<{ className?: string }>;
}

export const DEMO_PATH_STEPS: DemoPathStep[] = [
  {
    step: 1,
    label: "Dashboard",
    shortLabel: "Dashboard",
    href: "/dashboard",
    matchPrefixes: ["/dashboard"],
    description: "Central Release Workspace & NovaShop Demo Mode",
    icon: LayoutDashboard,
  },
  {
    step: 2,
    label: "AI Command Center",
    shortLabel: "Command Center",
    href: "/command-center",
    matchPrefixes: ["/command-center", "/agent"],
    description: "Supervisor decisions, policy gates & tool results",
    icon: Activity,
  },
  {
    step: 3,
    label: "GitHub PR Analysis",
    shortLabel: "PR Analysis",
    href: "/pull-requests",
    matchPrefixes: ["/pull-requests"],
    description: "7-dim risk assessment & Flagsmith Draft PR",
    icon: GitPullRequest,
  },
  {
    step: 4,
    label: "Flagsmith Experimentation",
    shortLabel: "Flagsmith & Exp",
    href: "/flagsmith",
    matchPrefixes: ["/flagsmith", "/experiments"],
    description: "Live Flagsmith catalog & Bayesian analysis",
    icon: Flag,
  },
  {
    step: 5,
    label: "Approval & Progressive Delivery",
    shortLabel: "Approvals & Rollout",
    href: "/approvals",
    matchPrefixes: ["/approvals", "/progressive-delivery"],
    description: "Hash-bound human sign-off & staged rollout",
    icon: CheckSquare,
  },
  {
    step: 6,
    label: "Final Release Report",
    shortLabel: "Release Report",
    href: "/releases",
    matchPrefixes: ["/releases"],
    description: "9-stage lifecycle & verified audit report",
    icon: Rocket,
  },
];

export function isDemoStepActive(pathname: string, step: DemoPathStep): boolean {
  return step.matchPrefixes.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`)
  );
}

export function DemoPathBar({
  pendingApprovalsCount = 0,
}: {
  pendingApprovalsCount?: number;
}) {
  const pathname = usePathname();
  const activeStepIndex = DEMO_PATH_STEPS.findIndex((s) =>
    isDemoStepActive(pathname, s)
  );

  return (
    <nav
      aria-label="5-Minute Demo Path"
      className="mb-6 rounded-xl border border-slate-800/90 bg-[#111622] px-3 py-2.5"
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 px-1">
        <div className="flex items-center gap-2">
          <span className="rounded-full border border-indigo-500/40 bg-indigo-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-indigo-200">
            5-Minute Demo Path
          </span>
          <span className="text-[11px] text-slate-400">
            {activeStepIndex >= 0
              ? `Step ${DEMO_PATH_STEPS[activeStepIndex].step} of ${DEMO_PATH_STEPS.length}: ${DEMO_PATH_STEPS[activeStepIndex].description}`
              : "Guided 6-screen end-to-end release governance workflow"}
          </span>
        </div>
        <span className="hidden text-[10px] font-mono text-slate-500 sm:inline">
          6-Screen Guided Flow
        </span>
      </div>

      <ol className="flex items-center gap-1 overflow-x-auto pb-1">
        {DEMO_PATH_STEPS.map((step, idx) => {
          const active = isDemoStepActive(pathname, step);
          const completed = activeStepIndex > idx;
          const Icon = step.icon;
          const showBadge = step.step === 5 && pendingApprovalsCount > 0;

          return (
            <React.Fragment key={step.step}>
              <li className="shrink-0">
                <Link
                  href={step.href}
                  title={`${step.step}. ${step.label} — ${step.description}`}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors",
                    active
                      ? "border-indigo-500/50 bg-indigo-500/15 text-indigo-200"
                      : completed
                        ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200 hover:bg-emerald-500/15"
                        : "border-slate-800 bg-[#0d1119] text-slate-400 hover:border-slate-700 hover:text-slate-200"
                  )}
                >
                  <span
                    className={cn(
                      "inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-bold tabular-nums",
                      active
                        ? "bg-indigo-500 text-white"
                        : completed
                          ? "bg-emerald-500/25 text-emerald-300"
                          : "bg-slate-800 text-slate-300"
                    )}
                  >
                    {step.step}
                  </span>
                  <Icon className="h-3.5 w-3.5 shrink-0" />
                  <span className="whitespace-nowrap">{step.shortLabel}</span>
                  {showBadge && (
                    <span className="ml-0.5 inline-flex min-w-4 items-center justify-center rounded-full bg-amber-500/25 px-1 text-[10px] font-bold text-amber-200 tabular-nums">
                      {pendingApprovalsCount}
                    </span>
                  )}
                </Link>
              </li>
              {idx < DEMO_PATH_STEPS.length - 1 && (
                <li aria-hidden="true" className="shrink-0 text-slate-600">
                  <ChevronRight className="h-3.5 w-3.5" />
                </li>
              )}
            </React.Fragment>
          );
        })}
      </ol>
    </nav>
  );
}
