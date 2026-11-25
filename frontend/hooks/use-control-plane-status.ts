"use client";

import { useCallback, useEffect, useState } from "react";
import { getSystemHealth, listApprovals } from "@/lib/api";
import type { SystemHealth } from "@/types/releaseguard";

export interface ControlPlaneStatus {
  health: SystemHealth | null;
  backendReachable: boolean;
  pendingApprovalsCount: number;
  lastCheckedAt: string | null;
  refresh: () => Promise<void>;
}

export function triggerControlPlaneRefresh() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("rg:refresh-status"));
  }
}

export function useControlPlaneStatus(): ControlPlaneStatus {
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [backendReachable, setBackendReachable] = useState(false);
  const [pendingApprovalsCount, setPendingApprovalsCount] = useState(0);
  const [lastCheckedAt, setLastCheckedAt] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [h, approvals] = await Promise.all([
        getSystemHealth(),
        listApprovals({ status: "pending", limit: 1 }).catch(() => null),
      ]);
      setHealth(h);
      setBackendReachable(true);
      if (approvals) {
        setPendingApprovalsCount(approvals.total ?? approvals.items.length);
      }
      setLastCheckedAt(new Date().toISOString());
    } catch {
      setBackendReachable(false);
      setHealth(null);
      setLastCheckedAt(new Date().toISOString());
    }
  }, []);

  useEffect(() => {
    let active = true;
    const run = async () => {
      if (!active) return;
      await refresh();
    };
    void run();

    const timer = setInterval(() => {
      void run();
    }, 15000);

    const onCustomRefresh = () => {
      void run();
    };
    window.addEventListener("rg:refresh-status", onCustomRefresh);

    return () => {
      active = false;
      clearInterval(timer);
      window.removeEventListener("rg:refresh-status", onCustomRefresh);
    };
  }, [refresh]);

  return {
    health,
    backendReachable,
    pendingApprovalsCount,
    lastCheckedAt,
    refresh,
  };
}
