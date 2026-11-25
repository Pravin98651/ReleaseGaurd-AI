"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Bell,
  Check,
  CheckCheck,
  CheckCircle2,
  ExternalLink,
  RefreshCw,
  SlidersHorizontal,
  Trash2,
} from "lucide-react";

import { AppShell } from "@/components/AppShell";
import { EmptyState, LoadingSkeleton, MetricCard, PageHeader, SectionCard } from "@/components/ui/StatePrimitives";
import {
  clearResolvedNotifications,
  getNotificationPreferences,
  getUnreadNotificationCount,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  resolveNotification,
  updateNotificationPreferences,
} from "@/lib/api";
import type { NotificationItem, NotificationPreference, NotificationSeverity } from "@/types/releaseguard";

const CATEGORIES = [
  "approval_required",
  "approval_decided",
  "guardrail_breach",
  "rollback_triggered",
  "pr_high_risk",
  "draft_pr_created",
  "agent_failed",
  "flag_health_alert",
  "schedule_completed",
  "schedule_failed",
  "integration_error",
  "system_alert",
];

const SEV_COLORS: Record<string, string> = {
  critical: "border-rose-500/40 bg-rose-500/15 text-rose-200",
  high: "border-amber-500/40 bg-amber-500/15 text-amber-200",
  warning: "border-yellow-500/40 bg-yellow-500/15 text-yellow-200",
  info: "border-slate-700 bg-slate-800 text-slate-300",
};

export default function NotificationsPage() {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [criticalUnread, setCriticalUnread] = useState(0);
  const [prefs, setPrefs] = useState<NotificationPreference>({
    in_app_enabled: true,
    severity_threshold: "info",
    muted_categories: [],
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const [categoryFilter, setCategoryFilter] = useState("");
  const [severityFilter, setSeverityFilter] = useState("");
  const [unreadOnly, setUnreadOnly] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [listRes, countRes, prefRes] = await Promise.all([
        listNotifications({
          category: categoryFilter || undefined,
          severity: severityFilter || undefined,
          unread_only: unreadOnly || undefined,
          limit: 100,
        }),
        getUnreadNotificationCount(),
        getNotificationPreferences(),
      ]);
      setItems(listRes.items);
      setUnreadCount(countRes.unread_count);
      setCriticalUnread(countRes.critical_unread_count);
      setPrefs(prefRes);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load notifications");
    } finally {
      setLoading(false);
    }
  }, [categoryFilter, severityFilter, unreadOnly]);

  useEffect(() => {
    const t = setTimeout(() => {
      void loadData();
    }, 0);
    const handler = () => void loadData();
    window.addEventListener("rg:workspace-event", handler);
    return () => {
      clearTimeout(t);
      window.removeEventListener("rg:workspace-event", handler);
    };
  }, [loadData]);

  const handleSavePrefs = async (next: NotificationPreference) => {
    setPrefs(next);
    try {
      const updated = await updateNotificationPreferences(next);
      setPrefs(updated);
      setBanner("Notification preferences saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save preferences");
    }
  };

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader
          title="Notification Centre"
          description="Persistent alerts across human approval queues, Bayesian guardrail breaches, PR risk findings, rollbacks, scheduled jobs, and integration errors."
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => void loadData()}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-200 hover:bg-slate-800"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} /> Refresh
              </button>
              <button
                type="button"
                onClick={async () => {
                  const res = await markAllNotificationsRead();
                  setBanner(`Marked ${res.marked_read} notification(s) as read.`);
                  await loadData();
                }}
                className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-500/40 bg-indigo-500/10 px-3 py-2 text-xs font-medium text-indigo-200 hover:bg-indigo-500/20"
              >
                <CheckCheck className="h-3.5 w-3.5" /> Mark All Read
              </button>
              <button
                type="button"
                onClick={async () => {
                  const res = await clearResolvedNotifications();
                  setBanner(`Cleared ${res.cleared_count} read/resolved notification(s).`);
                  await loadData();
                }}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-300 hover:bg-slate-800"
              >
                <Trash2 className="h-3.5 w-3.5" /> Clear Read/Resolved
              </button>
            </div>
          }
        />

        {error && (
          <div className="flex items-center gap-2 rounded-xl border border-rose-500/30 bg-rose-950/30 px-4 py-3 text-xs text-rose-200">
            <AlertTriangle className="h-4 w-4 text-rose-400" />
            <span>{error}</span>
          </div>
        )}
        {banner && (
          <div className="flex items-center gap-2 rounded-xl border border-emerald-500/30 bg-emerald-950/30 px-4 py-3 text-xs text-emerald-200">
            <CheckCircle2 className="h-4 w-4 text-emerald-400" />
            <span>{banner}</span>
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-3">
          <MetricCard label="Visible Notifications" value={items.length} icon={<Bell className="h-4 w-4" />} />
          <MetricCard label="Unread Alerts" value={unreadCount} tone={unreadCount > 0 ? "indigo" : "default"} />
          <MetricCard label="Critical Unread" value={criticalUnread} tone={criticalUnread > 0 ? "rose" : "emerald"} />
        </div>

        <div className="grid gap-6 xl:grid-cols-12">
          <SectionCard
            className="xl:col-span-8"
            title="Alert Stream"
            actions={
              <div className="flex flex-wrap items-center gap-2">
                <select
                  value={categoryFilter}
                  onChange={(e) => setCategoryFilter(e.target.value)}
                  className="rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200"
                >
                  <option value="">All Categories</option>
                  {CATEGORIES.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
                <select
                  value={severityFilter}
                  onChange={(e) => setSeverityFilter(e.target.value)}
                  className="rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200"
                >
                  <option value="">All Severities</option>
                  <option value="critical">critical</option>
                  <option value="high">high</option>
                  <option value="warning">warning</option>
                  <option value="info">info</option>
                </select>
                <label className="flex items-center gap-1.5 text-xs text-slate-300">
                  <input
                    type="checkbox"
                    checked={unreadOnly}
                    onChange={(e) => setUnreadOnly(e.target.checked)}
                  />
                  Unread only
                </label>
              </div>
            }
          >
            {loading ? (
              <LoadingSkeleton rows={5} />
            ) : items.length === 0 ? (
              <EmptyState
                icon={<Bell className="h-5 w-5" />}
                title="No matching notifications"
                description="Notifications appear here automatically when approvals wait for review, guardrails breach, high-risk PRs are flagged, or schedules finish."
              />
            ) : (
              <div className="divide-y divide-slate-800/80">
                {items.map((n) => (
                  <div
                    key={n.id}
                    className={`flex flex-col gap-2 py-3 sm:flex-row sm:items-start sm:justify-between ${
                      n.is_read ? "opacity-70" : ""
                    }`}
                  >
                    <div className="space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span
                          className={`rounded border px-1.5 py-0.5 text-[10px] font-bold uppercase ${
                            SEV_COLORS[n.severity] ?? SEV_COLORS.info
                          }`}
                        >
                          {n.severity}
                        </span>
                        <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-cyan-300">
                          {n.category}
                        </span>
                        {n.environment_name && (
                          <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-300">
                            {n.environment_name}
                          </span>
                        )}
                        <span className="text-xs font-semibold text-slate-100">{n.title}</span>
                      </div>
                      <p className="text-xs text-slate-400">{n.message}</p>
                      <div className="flex flex-wrap items-center gap-3 text-[11px] text-slate-500">
                        <span>{new Date(n.created_at).toLocaleString()}</span>
                        {n.is_resolved && <span className="text-emerald-400">Resolved</span>}
                        {n.action_href && (
                          <Link
                            href={n.action_href}
                            className="inline-flex items-center gap-1 text-indigo-300 hover:text-indigo-200"
                          >
                            Open Resource <ExternalLink className="h-3 w-3" />
                          </Link>
                        )}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      {!n.is_read && (
                        <button
                          type="button"
                          onClick={async () => {
                            await markNotificationRead(n.id);
                            await loadData();
                          }}
                          className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[11px] text-slate-200 hover:bg-slate-800"
                        >
                          <Check className="h-3 w-3" /> Read
                        </button>
                      )}
                      {!n.is_resolved && (
                        <button
                          type="button"
                          onClick={async () => {
                            await resolveNotification(n.id);
                            await loadData();
                          }}
                          className="inline-flex items-center gap-1 rounded border border-emerald-500/30 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-200 hover:bg-emerald-500/20"
                        >
                          Resolve
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </SectionCard>

          <SectionCard
            className="xl:col-span-4"
            title="Delivery Preferences"
            subtitle="Critical policy/guardrail alerts always bypass suppression"
            actions={<SlidersHorizontal className="h-4 w-4 text-slate-400" />}
          >
            <div className="space-y-4 text-xs">
              <label className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                <span className="font-medium text-slate-200">In-App Notifications Enabled</span>
                <input
                  type="checkbox"
                  checked={prefs.in_app_enabled}
                  onChange={(e) => void handleSavePrefs({ ...prefs, in_app_enabled: e.target.checked })}
                />
              </label>

              <div>
                <label className="mb-1 block font-medium text-slate-300">Minimum Severity Threshold</label>
                <select
                  value={prefs.severity_threshold}
                  onChange={(e) =>
                    void handleSavePrefs({
                      ...prefs,
                      severity_threshold: e.target.value as NotificationSeverity,
                    })
                  }
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100"
                >
                  <option value="info">info (all alerts)</option>
                  <option value="warning">warning+</option>
                  <option value="high">high+</option>
                  <option value="critical">critical only</option>
                </select>
              </div>

              <div>
                <div className="mb-1.5 font-medium text-slate-300">Muted Non-Critical Categories</div>
                <div className="space-y-1.5 max-h-60 overflow-y-auto pr-1">
                  {CATEGORIES.map((cat) => {
                    const checked = prefs.muted_categories.includes(cat);
                    return (
                      <label key={cat} className="flex items-center gap-2 text-[11px] text-slate-300">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(e) => {
                            const nextMuted = e.target.checked
                              ? [...prefs.muted_categories, cat]
                              : prefs.muted_categories.filter((m) => m !== cat);
                            void handleSavePrefs({ ...prefs, muted_categories: nextMuted });
                          }}
                        />
                        <span className="font-mono">{cat}</span>
                      </label>
                    );
                  })}
                </div>
              </div>
            </div>
          </SectionCard>
        </div>
      </div>
    </AppShell>
  );
}
