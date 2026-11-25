"use client";

import React from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, Inbox, RefreshCw, ShieldAlert, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Approval } from "@/types/releaseguard";
import { PolicyGateBadge } from "@/components/PolicyGateBadge";

export function PageHeader({
  title,
  description,
  actions,
  badge,
}: {
  title: string;
  description: string;
  actions?: React.ReactNode;
  badge?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 border-b border-slate-800/80 pb-5 sm:flex-row sm:items-center sm:justify-between">
      <div className="space-y-1">
        <div className="flex flex-wrap items-center gap-2.5">
          <h1 className="text-xl font-semibold tracking-tight text-slate-50 sm:text-2xl">
            {title}
          </h1>
          {badge}
        </div>
        <p className="text-sm text-slate-400">{description}</p>
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function SectionCard({
  title,
  subtitle,
  actions,
  children,
  className,
}: {
  title?: string;
  subtitle?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn(
        "rounded-xl border border-slate-800/90 bg-[#111622] shadow-sm",
        className
      )}
    >
      {(title || actions) && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/80 px-5 py-3.5">
          <div>
            {title && (
              <h2 className="text-sm font-semibold tracking-tight text-slate-100">{title}</h2>
            )}
            {subtitle && <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p>}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

export function MetricCard({
  label,
  value,
  subtext,
  icon,
  tone = "default",
  href,
}: {
  label: string;
  value: string | number;
  subtext?: string;
  icon?: React.ReactNode;
  tone?: "default" | "emerald" | "amber" | "rose" | "sky" | "indigo";
  href?: string;
}) {
  const toneBorder: Record<string, string> = {
    default: "border-slate-800/90",
    emerald: "border-emerald-500/25",
    amber: "border-amber-500/30",
    rose: "border-rose-500/30",
    sky: "border-sky-500/25",
    indigo: "border-indigo-500/30",
  };
  const content = (
    <div
      className={cn(
        "rounded-xl border bg-[#111622] p-4 transition-colors",
        toneBorder[tone],
        href && "hover:border-slate-700 hover:bg-[#151c2b]"
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium uppercase tracking-wider text-slate-400">
          {label}
        </span>
        {icon ? <span className="text-slate-400">{icon}</span> : null}
      </div>
      <div className="mt-2 text-2xl font-semibold tracking-tight text-slate-50 tabular-nums">
        {value}
      </div>
      {subtext ? <p className="mt-1 text-xs text-slate-400">{subtext}</p> : null}
    </div>
  );

  if (href) {
    return <Link href={href}>{content}</Link>;
  }
  return content;
}

export function LoadingSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3 animate-pulse">
      {Array.from({ length: rows }).map((_, idx) => (
        <div
          key={idx}
          className="h-14 rounded-lg border border-slate-800/60 bg-slate-900/60"
        />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
  icon?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-slate-800 bg-[#0d1119]/70 px-6 py-10 text-center">
      <div className="mb-3 rounded-full border border-slate-800 bg-slate-900/90 p-3 text-slate-400">
        {icon ?? <Inbox className="h-5 w-5" />}
      </div>
      <h3 className="text-sm font-medium text-slate-200">{title}</h3>
      <p className="mt-1 max-w-md text-xs leading-relaxed text-slate-400">{description}</p>
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export function ErrorBanner({
  error,
  onRetry,
  onDismiss,
}: {
  error: string | null;
  onRetry?: () => void;
  onDismiss?: () => void;
}) {
  if (!error) return null;
  return (
    <div className="flex items-start justify-between gap-3 rounded-lg border border-rose-500/35 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
      <div className="flex items-start gap-2.5">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-400" />
        <div className="text-xs leading-relaxed">
          <span className="font-semibold text-rose-100">API Error: </span>
          {error}
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="inline-flex items-center gap-1 rounded-md border border-rose-500/40 bg-rose-500/15 px-2.5 py-1 text-xs font-medium text-rose-100 hover:bg-rose-500/25"
          >
            <RefreshCw className="h-3 w-3" />
            Retry
          </button>
        )}
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="rounded p-1 text-rose-300 hover:bg-rose-500/20"
            aria-label="Dismiss error"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}

export function SuccessBanner({
  message,
  onDismiss,
}: {
  message: string | null;
  onDismiss?: () => void;
}) {
  if (!message) return null;
  return (
    <div className="flex items-start justify-between gap-3 rounded-lg border border-emerald-500/35 bg-emerald-500/10 px-4 py-3 text-xs text-emerald-200">
      <div className="flex items-start gap-2.5">
        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
        <span>{message}</span>
      </div>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          className="rounded p-0.5 text-emerald-300 hover:bg-emerald-500/20"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}

export function ApprovalQueuedBanner({
  approval,
  onDismiss,
}: {
  approval: Approval | null;
  onDismiss?: () => void;
}) {
  if (!approval) return null;
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-amber-500/40 bg-amber-500/10 p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-start gap-3">
        <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-amber-200">
              202 Accepted — Human Approval Required
            </span>
            <PolicyGateBadge category={approval.policy_category} />
            {approval.environment_name && (
              <span className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-mono text-[11px] text-slate-300">
                {approval.environment_name}
              </span>
            )}
          </div>
          <p className="text-sm font-medium text-slate-100">{approval.title}</p>
          <p className="font-mono text-[11px] text-amber-200/80">
            action_hash: {approval.action_hash.slice(0, 16)}… • id: {approval.id}
          </p>
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <Link
          href={`/approvals?highlight=${encodeURIComponent(approval.id)}`}
          className="inline-flex items-center rounded-lg bg-amber-500 px-3 py-1.5 text-xs font-semibold text-slate-950 hover:bg-amber-400"
        >
          Review in Approval Center
        </Link>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="rounded-lg border border-amber-500/30 p-1.5 text-amber-200 hover:bg-amber-500/20"
            aria-label="Dismiss"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}

export function Modal({
  open,
  onClose,
  title,
  subtitle,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-xs">
      <div className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl border border-slate-700 bg-[#111622] shadow-2xl">
        <div className="flex items-start justify-between gap-4 border-b border-slate-800 px-5 py-4">
          <div>
            <h3 className="text-base font-semibold text-slate-100">{title}</h3>
            {subtitle && <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-100"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}
