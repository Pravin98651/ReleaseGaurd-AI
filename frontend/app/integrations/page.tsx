"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Bell, GitPullRequest, Plug, RefreshCw, RotateCcw } from "lucide-react";
import {
  connectDiscordWebhook,
  connectSlackWebhook,
  getErrorMessage,
  listGitHubInstallations,
  listGitHubWebhookEvents,
  listNotificationIntegrations,
  retryGitHubWebhookEvent,
} from "@/lib/api";
import type {
  GitHubInstallationRecord,
  GitHubWebhookEventRecord,
  NotificationIntegrationSummary,
} from "@/types/releaseguard";
import { formatTimestamp } from "@/lib/utils";
import {
  EmptyState,
  ErrorBanner,
  LoadingSkeleton,
  MetricCard,
  PageHeader,
  SectionCard,
  SuccessBanner,
} from "@/components/ui/StatePrimitives";
import { StatusBadge } from "@/components/StatusBadges";

export default function IntegrationsCenterPage() {
  const [installations, setInstallations] = useState<GitHubInstallationRecord[]>([]);
  const [events, setEvents] = useState<GitHubWebhookEventRecord[]>([]);
  const [notifications, setNotifications] = useState<NotificationIntegrationSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [slackUrl, setSlackUrl] = useState("");
  const [slackChannel, setSlackChannel] = useState("#releases");
  const [discordUrl, setDiscordUrl] = useState("");
  const [discordChannel, setDiscordChannel] = useState("#release-alerts");

  const loadAll = useCallback(async () => {
    const [insts, evs, notifs] = await Promise.all([
      listGitHubInstallations(),
      listGitHubWebhookEvents(),
      listNotificationIntegrations(),
    ]);
    setInstallations(Array.isArray(insts) ? insts : []);
    setEvents(Array.isArray(evs) ? evs : []);
    setNotifications(notifs);
  }, []);

  useEffect(() => {
    let active = true;
    Promise.all([
      listGitHubInstallations(),
      listGitHubWebhookEvents(),
      listNotificationIntegrations(),
    ])
      .then(([insts, evs, notifs]) => {
        if (!active) return;
        setInstallations(Array.isArray(insts) ? insts : []);
        setEvents(Array.isArray(evs) ? evs : []);
        setNotifications(notifs);
        setError(null);
      })
      .catch((err) => {
        if (!active) return;
        setError(getErrorMessage(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function handleConnectSlack(e: React.FormEvent) {
    e.preventDefault();
    setBusy("slack");
    setError(null);
    setNotice(null);
    try {
      await connectSlackWebhook({ webhook_url: slackUrl, channel_label: slackChannel });
      setSlackUrl("");
      await loadAll();
      setNotice("Slack webhook connected.");
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function handleConnectDiscord(e: React.FormEvent) {
    e.preventDefault();
    setBusy("discord");
    setError(null);
    setNotice(null);
    try {
      await connectDiscordWebhook({ webhook_url: discordUrl, channel_label: discordChannel });
      setDiscordUrl("");
      await loadAll();
      setNotice("Discord webhook connected.");
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function handleRetryWebhook(eventId: string) {
    setBusy(eventId);
    setError(null);
    try {
      await retryGitHubWebhookEvent(eventId);
      await loadAll();
      setNotice("Webhook event retried.");
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Developer Integrations Center"
        description="GitHub App installations, HMAC-verified webhook deliveries, Slack & Discord notifications, and delivery health."
        actions={
          <button
            type="button"
            onClick={() => void loadAll()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-200 hover:bg-slate-800"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh
          </button>
        }
      />

      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <SuccessBanner message={notice} onDismiss={() => setNotice(null)} />

      {loading ? (
        <LoadingSkeleton rows={5} />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <MetricCard label="GitHub Installations" value={installations.length} />
            <MetricCard label="Webhook Events" value={events.length} />
            <MetricCard
              label="Notification Channels"
              value={notifications?.connections.length ?? 0}
            />
            <MetricCard
              label="Outbound Deliveries"
              value={notifications?.deliveries.length ?? 0}
            />
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            {/* GitHub App Installations */}
            <SectionCard
              title="GitHub App Installations"
              subtitle="Connected GitHub App installations and mapped repositories"
            >
              {installations.length === 0 ? (
                <EmptyState
                  icon={<Plug className="h-5 w-5" />}
                  title="No GitHub App installations"
                  description="Connect a GitHub App installation via API or settings to enable PR status checks and comments."
                />
              ) : (
                <div className="space-y-2.5">
                  {installations.map((inst) => (
                    <div
                      key={inst.id}
                      className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/50 p-3 text-xs"
                    >
                      <div>
                        <div className="font-semibold text-slate-100">
                          {inst.account_login} (#{inst.installation_id})
                        </div>
                        <div className="text-slate-400">
                          {inst.account_type} | {inst.repositories.length} repo(s) mapped
                        </div>
                      </div>
                      <StatusBadge status={inst.status} />
                    </div>
                  ))}
                </div>
              )}
            </SectionCard>

            {/* Slack & Discord Webhooks */}
            <SectionCard
              title="Slack & Discord Notifications"
              subtitle="Encrypted webhook connections for PR, rollout, and release intelligence alerts"
            >
              <div className="space-y-4">
                <form onSubmit={(e) => void handleConnectSlack(e)} className="space-y-2">
                  <div className="text-xs font-semibold text-slate-200">Connect Slack Webhook</div>
                  <div className="flex flex-wrap gap-2">
                    <input
                      type="url"
                      required
                      value={slackUrl}
                      onChange={(e) => setSlackUrl(e.target.value)}
                      placeholder="https://hooks.slack.com/services/..."
                      className="min-w-[200px] flex-1 rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-100"
                    />
                    <input
                      type="text"
                      value={slackChannel}
                      onChange={(e) => setSlackChannel(e.target.value)}
                      className="w-28 rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-100"
                    />
                    <button
                      type="submit"
                      disabled={busy === "slack"}
                      className="rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400"
                    >
                      Save Slack
                    </button>
                  </div>
                </form>

                <form onSubmit={(e) => void handleConnectDiscord(e)} className="space-y-2">
                  <div className="text-xs font-semibold text-slate-200">Connect Discord Webhook</div>
                  <div className="flex flex-wrap gap-2">
                    <input
                      type="url"
                      required
                      value={discordUrl}
                      onChange={(e) => setDiscordUrl(e.target.value)}
                      placeholder="https://discord.com/api/webhooks/..."
                      className="min-w-[200px] flex-1 rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-100"
                    />
                    <input
                      type="text"
                      value={discordChannel}
                      onChange={(e) => setDiscordChannel(e.target.value)}
                      className="w-28 rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-100"
                    />
                    <button
                      type="submit"
                      disabled={busy === "discord"}
                      className="rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400"
                    >
                      Save Discord
                    </button>
                  </div>
                </form>

                {notifications && notifications.connections.length > 0 && (
                  <div className="space-y-1.5 border-t border-slate-800 pt-3">
                    {notifications.connections.map((c) => (
                      <div
                        key={c.id}
                        className="flex items-center justify-between text-xs text-slate-300"
                      >
                        <span>
                          <strong className="uppercase">{c.provider}</strong> ({c.account_label})
                        </span>
                        <StatusBadge status={c.status} />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </SectionCard>
          </div>

          {/* Webhook Event History */}
          <SectionCard
            title="GitHub Webhook Event History"
            subtitle="Signature-verified deliveries with deduplication and retry controls"
          >
            {events.length === 0 ? (
              <EmptyState
                icon={<GitPullRequest className="h-5 w-5" />}
                title="No webhook deliveries received"
                description="Incoming GitHub webhook events appear here with processing status."
              />
            ) : (
              <div className="space-y-2">
                {events.map((ev) => (
                  <div
                    key={ev.id}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-900/50 p-3 text-xs"
                  >
                    <div>
                      <span className="font-mono text-indigo-300">
                        {ev.event_type}
                        {ev.action ? `.${ev.action}` : ""}
                      </span>
                      <span className="mx-2 text-slate-600">|</span>
                      <span className="text-slate-300">
                        Delivery: {ev.delivery_id.slice(0, 12)}
                        {ev.pr_number ? ` | PR #${ev.pr_number}` : ""}
                      </span>
                      {ev.error_message && (
                        <div className="mt-1 text-rose-300">{ev.error_message}</div>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      <StatusBadge status={ev.processing_status} />
                      <span className="text-slate-400">{formatTimestamp(ev.received_at)}</span>
                      {["failed", "dead_letter"].includes(ev.processing_status) && (
                        <button
                          type="button"
                          disabled={busy === ev.id}
                          onClick={() => void handleRetryWebhook(ev.id)}
                          className="inline-flex items-center gap-1 rounded border border-slate-700 px-2 py-1 text-[11px] text-slate-200 hover:bg-slate-800"
                        >
                          <RotateCcw className="h-3 w-3" />
                          Retry
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </SectionCard>

          {/* Notification Delivery Log */}
          <SectionCard
            title="Outbound Notification Deliveries"
            subtitle="Recent Slack and Discord delivery attempts"
          >
            {!notifications || notifications.deliveries.length === 0 ? (
              <EmptyState
                icon={<Bell className="h-5 w-5" />}
                title="No outbound deliveries yet"
                description="Slack and Discord notifications sent by ReleaseGuard AI will appear here."
              />
            ) : (
              <div className="space-y-2">
                {notifications.deliveries.map((d) => (
                  <div
                    key={d.id}
                    className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/50 p-2.5 text-xs"
                  >
                    <div>
                      <span className="font-semibold uppercase text-slate-100">{d.provider}</span>{" "}
                      <span className="text-slate-400">({d.channel_target})</span> — {d.event_type}
                    </div>
                    <div className="flex items-center gap-2">
                      <StatusBadge status={d.status} />
                      <span className="text-slate-400">{formatTimestamp(d.created_at)}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </SectionCard>
        </>
      )}
    </div>
  );
}
