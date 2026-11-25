"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Bell, Check, CheckCheck, ExternalLink, Radio } from "lucide-react";

import {
  getUnreadNotificationCount,
  getWorkspaceStreamUrl,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from "@/lib/api";
import type { NotificationItem } from "@/types/releaseguard";

const SEV_STYLE: Record<string, string> = {
  critical: "border-rose-500/40 bg-rose-500/15 text-rose-200",
  high: "border-amber-500/40 bg-amber-500/15 text-amber-200",
  warning: "border-yellow-500/40 bg-yellow-500/15 text-yellow-200",
  info: "border-slate-700 bg-slate-800 text-slate-300",
};

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const [unread, setUnread] = useState(0);
  const [criticalUnread, setCriticalUnread] = useState(0);
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [sseConnected, setSseConnected] = useState(false);
  const lastSeqRef = useRef<number>(0);

  const loadNotifications = useCallback(async () => {
    try {
      const [cnt, listRes] = await Promise.all([
        getUnreadNotificationCount(),
        listNotifications({ limit: 8 }),
      ]);
      setUnread(cnt.unread_count);
      setCriticalUnread(cnt.critical_unread_count);
      setItems(listRes.items);
    } catch {
      // Non-fatal: polling & SSE retry cleanly
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => {
      void loadNotifications();
    }, 0);
    const id = setInterval(() => {
      void loadNotifications();
    }, 20000);
    return () => {
      clearTimeout(t);
      clearInterval(id);
    };
  }, [loadNotifications]);

  useEffect(() => {
    if (typeof window === "undefined" || typeof EventSource === "undefined") {
      return;
    }
    let closed = false;
    let es: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (closed) return;
      try {
        es = new EventSource(getWorkspaceStreamUrl(lastSeqRef.current));
        es.onopen = () => setSseConnected(true);
        es.addEventListener("workspace", (evt: MessageEvent) => {
          try {
            const parsed = JSON.parse(String(evt.data)) as {
              seq?: number;
              event_type?: string;
              payload?: Record<string, unknown>;
            };
            if (typeof parsed.seq === "number") {
              lastSeqRef.current = parsed.seq;
            }
            void loadNotifications();
            window.dispatchEvent(new CustomEvent("rg:refresh-status"));
            window.dispatchEvent(new CustomEvent("rg:workspace-event", { detail: parsed }));
          } catch {
            // Ignore malformed SSE payload
          }
        });
        es.onerror = () => {
          setSseConnected(false);
          es?.close();
          es = null;
          if (!closed) {
            retryTimer = setTimeout(connect, 5000);
          }
        };
      } catch {
        setSseConnected(false);
      }
    };

    connect();
    return () => {
      closed = true;
      if (retryTimer) clearTimeout(retryTimer);
      es?.close();
    };
  }, [loadNotifications]);

  const handleMarkRead = async (id: string) => {
    try {
      await markNotificationRead(id);
      await loadNotifications();
    } catch {
      // ignore
    }
  };

  const handleMarkAllRead = async () => {
    try {
      await markAllNotificationsRead();
      await loadNotifications();
    } catch {
      // ignore
    }
  };

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Open notifications"
        className="relative inline-flex items-center gap-1.5 rounded-lg border border-slate-800 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-200 hover:bg-slate-800"
      >
        <Bell className={`h-4 w-4 ${criticalUnread > 0 ? "text-rose-400" : "text-slate-300"}`} />
        {unread > 0 && (
          <span
            className={`inline-flex min-w-[18px] items-center justify-center rounded-full px-1 text-[10px] font-bold text-white ${
              criticalUnread > 0 ? "bg-rose-500" : "bg-indigo-500"
            }`}
          >
            {unread > 99 ? "99+" : unread}
          </span>
        )}
        <span
          title={sseConnected ? "Real-time workspace SSE active" : "Polling fallback active"}
          className={`h-1.5 w-1.5 rounded-full ${sseConnected ? "bg-emerald-400" : "bg-slate-600"}`}
        />
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-80 sm:w-96 rounded-xl border border-slate-800 bg-[#0d121c] shadow-2xl z-50 overflow-hidden">
          <div className="flex items-center justify-between border-b border-slate-800 px-3.5 py-2.5">
            <div className="flex items-center gap-2 text-xs font-semibold text-slate-100">
              <span>Notifications</span>
              <span className="inline-flex items-center gap-1 text-[10px] font-normal text-slate-400">
                <Radio className={`h-3 w-3 ${sseConnected ? "text-emerald-400" : "text-slate-500"}`} />
                {sseConnected ? "Live SSE" : "Sync"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              {unread > 0 && (
                <button
                  type="button"
                  onClick={() => void handleMarkAllRead()}
                  className="inline-flex items-center gap-1 text-[11px] text-indigo-300 hover:text-indigo-200"
                >
                  <CheckCheck className="h-3 w-3" /> Read all
                </button>
              )}
              <Link
                href="/notifications"
                onClick={() => setOpen(false)}
                className="text-[11px] font-medium text-cyan-300 hover:text-cyan-200"
              >
                View all →
              </Link>
            </div>
          </div>

          <div className="max-h-96 divide-y divide-slate-800/80 overflow-y-auto">
            {items.length === 0 ? (
              <div className="px-4 py-8 text-center text-xs text-slate-400">
                No notifications recorded yet.
              </div>
            ) : (
              items.map((n) => (
                <div
                  key={n.id}
                  className={`px-3.5 py-2.5 text-xs ${n.is_read ? "opacity-65" : "bg-slate-900/40"}`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 space-y-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span
                          className={`rounded border px-1.5 py-0.5 text-[10px] font-bold uppercase ${
                            SEV_STYLE[n.severity] ?? SEV_STYLE.info
                          }`}
                        >
                          {n.severity}
                        </span>
                        <span className="font-medium text-slate-100">{n.title}</span>
                      </div>
                      <p className="text-[11px] text-slate-400 line-clamp-2">{n.message}</p>
                      <div className="flex items-center gap-3 text-[10px] text-slate-500">
                        <span>{new Date(n.created_at).toLocaleString()}</span>
                        {n.action_href && (
                          <Link
                            href={n.action_href}
                            onClick={() => setOpen(false)}
                            className="inline-flex items-center gap-0.5 text-indigo-300 hover:text-indigo-200"
                          >
                            Inspect <ExternalLink className="h-2.5 w-2.5" />
                          </Link>
                        )}
                      </div>
                    </div>
                    {!n.is_read && (
                      <button
                        type="button"
                        onClick={() => void handleMarkRead(n.id)}
                        title="Mark as read"
                        className="shrink-0 rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-100"
                      >
                        <Check className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
