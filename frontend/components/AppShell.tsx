"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  BarChart3,
  Bell,
  Bot,
  Brain,
  Building2,
  CalendarClock,
  CheckSquare,
  ExternalLink,
  Flag,
  FlaskConical,
  FolderGit2,
  GitBranch,
  GitPullRequest,
  HeartPulse,
  LayoutDashboard,
  LogOut,
  Menu,
  Moon,
  Plug,
  Rocket,
  ScrollText,
  ServerCog,
  Settings,
  ShieldCheck,
  Sun,
  UserCheck,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { DEMO_APP_URL, logoutSession } from "@/lib/api";
import { useControlPlaneStatus } from "@/hooks/use-control-plane-status";
import { AuthGate } from "@/components/AuthGate";
import { NotificationBell } from "@/components/NotificationBell";

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  group: "overview" | "delivery" | "governance" | "system";
  showApprovalsBadge?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, group: "overview" },
  { href: "/metrics", label: "Metrics & Analytics", icon: BarChart3, group: "overview" },
  { href: "/intelligence", label: "Release Intelligence", icon: Brain, group: "overview" },
  { href: "/organization", label: "Organization Hub", icon: Building2, group: "overview" },
  { href: "/notifications", label: "Notifications", icon: Bell, group: "overview" },
  { href: "/command-center", label: "Command Center", icon: Activity, group: "delivery" },
  { href: "/risk-intelligence", label: "Risk Intelligence", icon: ShieldCheck, group: "delivery" },
  { href: "/progressive-delivery", label: "Progressive Delivery", icon: GitBranch, group: "delivery" },
  { href: "/agent", label: "AI Agent", icon: Bot, group: "delivery" },
  { href: "/repositories", label: "Repositories", icon: FolderGit2, group: "delivery" },
  { href: "/pull-requests", label: "Pull Requests", icon: GitPullRequest, group: "delivery" },
  { href: "/feature-flags", label: "Feature Flags", icon: Flag, group: "delivery" },
  { href: "/releases", label: "Releases", icon: Rocket, group: "delivery" },
  { href: "/experiments", label: "Experiments", icon: FlaskConical, group: "delivery" },
  { href: "/flag-health", label: "Flag Health", icon: HeartPulse, group: "delivery" },
  {
    href: "/approvals",
    label: "Approvals",
    icon: CheckSquare,
    group: "governance",
    showApprovalsBadge: true,
  },
  { href: "/schedules", label: "Schedules", icon: CalendarClock, group: "governance" },
  { href: "/integrations", label: "Integrations", icon: Plug, group: "governance" },
  { href: "/audit-logs", label: "Audit Logs", icon: ScrollText, group: "governance" },
  { href: "/system-health", label: "System Health", icon: ServerCog, group: "system" },
  { href: "/settings", label: "Settings", icon: Settings, group: "system" },
];

const GROUP_LABELS: Record<NavItem["group"], string> = {
  overview: "Overview",
  delivery: "Release Operations",
  governance: "Governance & Automation",
  system: "Platform & System",
};

function HealthPill({
  label,
  ok,
  detail,
}: {
  label: string;
  ok: boolean;
  detail?: string;
}) {
  return (
    <div
      title={detail ?? label}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium",
        ok
          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
          : "border-slate-700/80 bg-slate-900/90 text-slate-400"
      )}
    >
      <span
        className={cn(
          "h-1.5 w-1.5 rounded-full",
          ok ? "bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.7)]" : "bg-slate-500"
        )}
      />
      <span>{label}</span>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    if (typeof window !== "undefined") {
      return window.localStorage.getItem("rg_theme") === "light" ? "light" : "dark";
    }
    return "dark";
  });

  useEffect(() => {
    if (typeof document !== "undefined") {
      document.documentElement.classList.remove("dark", "light");
      document.documentElement.classList.add(theme);
    }
    if (typeof window !== "undefined") {
      window.localStorage.setItem("rg_theme", theme);
    }
  }, [theme]);

  const toggleTheme = () => {
    setTheme((prev) => (prev === "dark" ? "light" : "dark"));
  };
  const { health, authStatus, backendReachable, pendingApprovalsCount, refresh } =
    useControlPlaneStatus();

  const activeUser = authStatus?.user;

  const sidebarContent = (
    <div className="flex h-full flex-col bg-[#0d1119] border-r border-slate-800/90">
      {/* Brand header */}
      <div className="flex h-14 items-center justify-between border-b border-slate-800/80 px-4">
        <Link href="/dashboard" className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-indigo-500/40 bg-indigo-500/15 text-indigo-300">
            <ShieldCheck className="h-4 w-4" />
          </div>
          <div>
            <div className="text-sm font-semibold tracking-tight text-slate-100">
              ReleaseGuard AI
            </div>
            <div className="text-[10px] font-medium uppercase tracking-wider text-slate-500">
              Governance Control Plane
            </div>
          </div>
        </Link>
        <button
          type="button"
          onClick={() => setMobileOpen(false)}
          className="rounded-md p-1 text-slate-400 hover:bg-slate-800 lg:hidden"
          aria-label="Close navigation"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-4">
        {(["overview", "delivery", "governance", "system"] as const).map((group) => {
          const items = NAV_ITEMS.filter((item) => item.group === group);
          return (
            <div key={group}>
              <div className="mb-1.5 px-2.5 text-[10px] font-semibold uppercase tracking-widest text-slate-500">
                {GROUP_LABELS[group]}
              </div>
              <div className="space-y-0.5">
                {items.map((item) => {
                  const Icon = item.icon;
                  const active =
                    pathname === item.href || pathname.startsWith(`${item.href}/`);
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      onClick={() => setMobileOpen(false)}
                      className={cn(
                        "group flex items-center justify-between rounded-lg px-2.5 py-2 text-xs font-medium transition-colors",
                        active
                          ? "bg-indigo-500/15 text-indigo-200 border border-indigo-500/30"
                          : "text-slate-400 hover:bg-slate-800/60 hover:text-slate-100 border border-transparent"
                      )}
                    >
                      <span className="flex items-center gap-2.5">
                        <Icon
                          className={cn(
                            "h-4 w-4 transition-colors",
                            active
                              ? "text-indigo-400"
                              : "text-slate-500 group-hover:text-slate-300"
                          )}
                        />
                        <span>{item.label}</span>
                      </span>
                      {item.showApprovalsBadge && pendingApprovalsCount > 0 && (
                        <span className="inline-flex min-w-5 items-center justify-center rounded-full border border-amber-500/40 bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-bold text-amber-200 tabular-nums">
                          {pendingApprovalsCount}
                        </span>
                      )}
                    </Link>
                  );
                })}
              </div>
            </div>
          );
        })}
      </nav>

      {/* Sidebar footer: health pills + Demo Storefront link */}
      <div className="space-y-3 border-t border-slate-800/80 p-3.5">
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-[10px] font-semibold uppercase tracking-wider text-slate-500">
            <span>System Telemetry</span>
            <Activity className="h-3 w-3 text-slate-500" />
          </div>
          <div className="flex flex-wrap gap-1.5">
            <HealthPill
              label={backendReachable ? "API Online" : "API Offline"}
              ok={backendReachable}
              detail={health ? `DB: ${health.database}` : "Backend /health check"}
            />
            <HealthPill
              label="GitHub"
              ok={Boolean(health?.github_connected)}
              detail={
                health?.github_connected ? "GitHub connected" : "GitHub disconnected"
              }
            />
            <HealthPill
              label="Flagsmith"
              ok={Boolean(health?.flagsmith_connected)}
              detail={
                health?.flagsmith_connected
                  ? "Flagsmith connected"
                  : "Flagsmith disconnected"
              }
            />
          </div>
          {health?.analytics_mode && (
            <div className="rounded-md border border-slate-800 bg-slate-900/70 px-2 py-1 text-[10px] text-slate-400">
              Analytics:{" "}
              <span className="font-mono text-cyan-300">{health.analytics_mode}</span>
            </div>
          )}
        </div>

        <a
          href={DEMO_APP_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center justify-between rounded-lg border border-slate-800 bg-[#111622] px-3 py-2 text-xs font-medium text-slate-300 transition-colors hover:border-slate-700 hover:text-slate-100"
        >
          <span>Open Demo Storefront</span>
          <ExternalLink className="h-3.5 w-3.5 text-slate-500" />
        </a>

        {activeUser && (
          <div className="flex items-center justify-between gap-2 rounded-lg border border-slate-800 bg-[#0b0f17] px-2.5 py-2">
            <div className="min-w-0">
              <div className="flex items-center gap-1.5 text-[11px] font-medium text-slate-200 truncate">
                <UserCheck className="h-3 w-3 shrink-0 text-emerald-400" />
                <span className="truncate">{activeUser.display_name}</span>
              </div>
              <div className="font-mono text-[10px] uppercase text-indigo-300">
                {activeUser.role}
              </div>
            </div>
            <button
              type="button"
              onClick={() => void logoutSession().then(() => refresh())}
              title="Sign out"
              aria-label="Sign out"
              className="rounded-md border border-slate-800 p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100"
            >
              <LogOut className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
      </div>
    </div>
  );

  return (
    <div className="flex min-h-screen bg-[#090c12] text-slate-100">
      {/* Desktop fixed sidebar */}
      <aside className="hidden w-64 shrink-0 lg:block">
        <div className="sticky top-0 h-screen">{sidebarContent}</div>
      </aside>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 flex lg:hidden">
          <div
            className="fixed inset-0 bg-black/70 backdrop-blur-xs"
            onClick={() => setMobileOpen(false)}
          />
          <div className="relative z-10 w-72 max-w-[85vw]">{sidebarContent}</div>
        </div>
      )}

      {/* Main viewport */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Top bar */}
        <header className="sticky top-0 z-30 flex h-14 items-center justify-between gap-4 border-b border-slate-800/80 bg-[#090c12]/90 px-4 backdrop-blur-md sm:px-6">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setMobileOpen(true)}
              className="rounded-lg border border-slate-800 bg-slate-900 p-1.5 text-slate-300 hover:bg-slate-800 lg:hidden"
              aria-label="Open navigation"
            >
              <Menu className="h-4 w-4" />
            </button>
            <div className="hidden items-center gap-2 text-xs text-slate-400 sm:flex">
              <span className="font-mono text-slate-500">workspace</span>
              <span>/</span>
              <span className="font-medium text-slate-200">
                {NAV_ITEMS.find((i) => pathname.startsWith(i.href))?.label ?? "Control Plane"}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2.5">
            {pendingApprovalsCount > 0 && (
              <Link
                href="/approvals"
                className="inline-flex items-center gap-1.5 rounded-full border border-amber-500/40 bg-amber-500/15 px-3 py-1 text-xs font-semibold text-amber-200 hover:bg-amber-500/25 tabular-nums"
              >
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400" />
                {pendingApprovalsCount} Pending Approval
                {pendingApprovalsCount === 1 ? "" : "s"}
              </Link>
            )}
            <NotificationBell />
            <button
              type="button"
              onClick={toggleTheme}
              aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              className="inline-flex items-center justify-center rounded-lg border border-slate-800 bg-slate-900 p-1.5 text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-100"
            >
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
            <div className="hidden items-center gap-1.5 md:flex">
              <HealthPill
                label={backendReachable ? `Backend (${health?.database ?? "ok"})` : "Backend Offline"}
                ok={backendReachable}
              />
              <HealthPill
                label={health?.gemini_configured ? "Gemini Ready" : "Gemini Unconfigured"}
                ok={Boolean(health?.gemini_configured)}
              />
            </div>
          </div>
        </header>

        <main className="mx-auto w-full max-w-[1440px] flex-1 px-4 py-6 sm:px-6 lg:px-8">
          <AuthGate authStatus={authStatus} onAuthenticated={refresh}>
            {children}
          </AuthGate>
        </main>
      </div>
    </div>
  );
}
