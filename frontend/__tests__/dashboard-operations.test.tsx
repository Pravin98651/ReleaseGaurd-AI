import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
    [key: string]: unknown;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { DashboardChartGrid } from "@/components/DashboardCharts";
import { NotificationBell } from "@/components/NotificationBell";
import SchedulesPage from "@/app/schedules/page";
import NotificationsPage from "@/app/notifications/page";
import SystemHealthPage from "@/app/system-health/page";
import MetricsPage from "@/app/metrics/page";
import type { DashboardCharts } from "@/types/releaseguard";

function jsonResp(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ReleaseGuard AI Operational Visibility Modules", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("renders DashboardChartGrid empty states and populated Chart A-F titles", () => {
    const emptyCharts: DashboardCharts = {
      filter: {
        range: "7d",
        start_at: "2026-11-18T00:00:00Z",
        end_at: "2026-11-25T00:00:00Z",
        event_source: "all",
      },
      release_activity: [],
      experiment_performance: [],
      flag_distribution: [],
      agent_activity: [],
      telemetry_activity: [],
      release_health: [],
      telemetry_totals: { total_events: 0, simulator_events: 0, application_telemetry_events: 0 },
    };

    const { rerender } = render(<DashboardChartGrid charts={emptyCharts} />);
    expect(screen.getByText(/No rollout transitions in selected window/i)).toBeInTheDocument();
    expect(screen.getByText(/No experiments recorded/i)).toBeInTheDocument();

    const populatedCharts: DashboardCharts = {
      ...emptyCharts,
      release_activity: [
        { bucket: "2026-11-24", label: "Nov 24", releases_created: 2, releases_completed: 2, releases_failed: 0, rollbacks: 0 },
      ],
      experiment_performance: [
        {
          experiment_id: "exp-1",
          name: "Checkout Promo",
          flag_key: "checkout_promo",
          environment_name: "staging",
          status: "running",
          analytics_mode: "tracked_event_fallback",
          primary_metric: "checkout_completed",
          recommendation: "Expand Rollout",
          hypothesis: "Lift checkout",
          traffic_allocation: { control: 50, treatment: 50 },
          control_exposures: 120,
          control_conversions: 12,
          control_rate: 0.1,
          control_ci_95: [0.05, 0.16],
          treatment_exposures: 125,
          treatment_conversions: 25,
          treatment_rate: 0.2,
          treatment_ci_95: [0.13, 0.28],
          absolute_improvement: 0.1,
          relative_improvement: 1.0,
          prob_treatment_beats_control: 0.98,
          expected_loss_treatment: 0.001,
          payment_failed_breached: false,
          payment_failed_severity: "none",
          control_error_rate: 0.01,
          treatment_error_rate: 0.01,
          sample_adequate: true,
          simulator_event_count: 100,
          telemetry_event_count: 145,
        },
      ],
      flag_distribution: [{ key: "enabled", name: "Enabled", count: 3 }],
      agent_activity: [
        { bucket: "2026-11-24", label: "Nov 24", successful: 4, failed: 1, running: 0, awaiting_approval: 1, avg_duration_seconds: 2.1 },
      ],
      telemetry_activity: [
        {
          bucket: "2026-11-24",
          label: "Nov 24",
          total_events: 50,
          checkout_started: 20,
          checkout_completed: 15,
          payment_failed: 2,
          checkout_abandoned: 3,
          simulator_events: 10,
          telemetry_events: 40,
        },
      ],
      release_health: [{ key: "healthy", name: "Healthy", count: 3 }],
    };
    rerender(<DashboardChartGrid charts={populatedCharts} />);
    expect(screen.getByText(/Chart A — Release Activity Over Time/i)).toBeInTheDocument();
    expect(screen.getByText(/Chart B — Experiment Conversion Performance/i)).toBeInTheDocument();
    expect(screen.getByText(/Chart C — Feature Flag Rollout Distribution/i)).toBeInTheDocument();
  });

  it("renders NotificationBell unread count, opens dropdown, and marks notification read", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/v1/notifications/unread-count")) {
        return jsonResp({ unread_count: 2, critical_unread_count: 1 });
      }
      if (url.includes("/api/v1/notifications?") || url.endsWith("/api/v1/notifications")) {
        return jsonResp({
          items: [
            {
              id: "n-1",
              category: "guardrail_breach",
              severity: "critical",
              title: "Checkout Guardrail Breached",
              message: "payment_failed rate spiked in staging",
              action_href: "/experiments",
              metadata_json: {},
              is_read: false,
              is_resolved: false,
              created_at: "2026-11-25T10:00:00Z",
            },
          ],
          total: 1,
          limit: 8,
          offset: 0,
        });
      }
      if (url.includes("/api/v1/notifications/n-1/read") && init?.method === "PATCH") {
        return jsonResp({ id: "n-1", is_read: true });
      }
      return jsonResp({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<NotificationBell />);
    await waitFor(() => expect(screen.getByText("2")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Open notifications"));
    await waitFor(() => expect(screen.getByText("Checkout Guardrail Breached")).toBeInTheDocument());

    fireEvent.click(screen.getByTitle("Mark as read"));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/v1/notifications/n-1/read"),
        expect.objectContaining({ method: "PATCH" })
      );
    });
  });

  it("renders SchedulesPage, previews schedule, and triggers Run Now", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/health")) return jsonResp({ status: "ok", database: "ok", gemini_configured: true });
      if (url.includes("/auth/status")) return jsonResp({ require_auth: false, authenticated: true });
      if (url.includes("/approvals")) return jsonResp({ items: [], total: 0 });
      if (url.includes("/api/v1/schedules/executions")) {
        return jsonResp({
          items: [
            {
              id: "exec-1",
              scheduled_job_id: null,
              schedule_name: "Archived Repo Sync",
              job_type: "repo_sync",
              execution_status: "completed",
              trigger_type: "manual",
              retry_count: 0,
              input_payload: {},
              result_summary: { pull_requests_synced: 2 },
              created_at: "2026-11-25T10:00:00Z",
            },
          ],
          total: 1,
        });
      }
      if (url.includes("/api/v1/schedules/sched-1/run") && init?.method === "POST") {
        return jsonResp({
          id: "exec-2",
          scheduled_job_id: "sched-1",
          schedule_name: "Nightly PR Scan",
          job_type: "pr_analysis",
          execution_status: "completed",
          trigger_type: "manual",
          retry_count: 0,
          input_payload: {},
          result_summary: { prs_analyzed: 1 },
          created_at: "2026-11-25T11:00:00Z",
        });
      }
      if (url.includes("/api/v1/schedules")) {
        return jsonResp({
          items: [
            {
              id: "sched-1",
              name: "Nightly PR Scan",
              job_type: "pr_analysis",
              frequency: "daily",
              cron_expression: "0 9 * * *",
              timezone: "Asia/Kolkata",
              configuration: {},
              retry_policy: { max_retries: 2, retry_delay_seconds: 30, timeout_seconds: 120 },
              notification_preference: { on_success: false, on_failure: true },
              enabled: true,
              consecutive_failures: 0,
              missed_runs_count: 0,
              human_description: "Every day at 09:00 Asia/Kolkata",
              next_runs_preview: [],
              next_run_at: "2026-11-26T03:30:00Z",
              created_at: "2026-11-25T08:00:00Z",
              updated_at: "2026-11-25T08:00:00Z",
            },
          ],
          total: 1,
        });
      }
      if (url.includes("/api/v1/repositories")) return jsonResp({ items: [], total: 0 });
      if (url.includes("/api/v1/experiments")) return jsonResp({ items: [], total: 0 });
      return jsonResp({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<SchedulesPage />);
    await waitFor(() => expect(screen.getByText("Nightly PR Scan")).toBeInTheDocument());
    expect(screen.getByText(/Archived Repo Sync/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Run Now/i }));
    await waitFor(() => {
      expect(screen.getByText(/Manual run finished \(completed\)/i)).toBeInTheDocument();
    });
  });

  it("renders NotificationsPage, SystemHealthPage, and MetricsPage cleanly", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/system/health")) {
        return jsonResp({
          status: "degraded",
          checked_at: "2026-11-25T12:00:00Z",
          database: { status: "ok", latency_ms: 1.4 },
          backend_api: { status: "ok", requests_total: 42, errors_5xx: 0, avg_latency_ms: 8.2 },
          scheduler: {
            instance_id: "sched-test",
            running: true,
            poll_interval_seconds: 10,
            ticks_completed: 12,
            attention_schedules: 0,
          },
          workers: { active_agent_tasks: 0, failed_agent_tasks: 0, running_job_executions: 0, failed_job_executions: 0 },
          integrations: {
            github: { status: "connected", configured: true, account: "acme-bot" },
            flagsmith: { status: "connected", configured: true, analytics_mode: "tracked_event_fallback" },
            gemini: { status: "configured", configured: true, model: "gemini-2.5-flash" },
            recent_errors: [],
          },
          demo_app: {
            backend: { status: "unreachable", latency_ms: 10, error: "ConnectError" },
            frontend: { status: "ok", latency_ms: 4 },
          },
        });
      }
      if (url.includes("/api/v1/system/workers")) return jsonResp({ agent_tasks: [], job_executions: [] });
      if (url.includes("/api/v1/system/scheduler")) return jsonResp({ worker: {}, counts: { total: 1, enabled: 1, paused: 0, failing: 0 }, upcoming: [] });
      if (url.includes("/api/v1/notifications/unread-count")) return jsonResp({ unread_count: 1, critical_unread_count: 0 });
      if (url.includes("/api/v1/notifications/preferences")) return jsonResp({ in_app_enabled: true, severity_threshold: "info", muted_categories: [] });
      if (url.includes("/api/v1/notifications")) return jsonResp({ items: [], total: 0 });
      if (url.includes("/api/v1/metrics/overview")) {
        return jsonResp({
          summary: {
            engineering: { connected_repositories: 1, synchronized_prs: 2, prs_analyzed: 2, pending_pr_analyses: 0, draft_prs_created: 1, failed_analysis_tasks: 0 },
            releases: { total_releases: 3, releases_in_progress: 1, successful_releases: 2, failed_releases: 0, rollbacks_executed: 0, releases_awaiting_approval: 0, release_success_rate: 100 },
            feature_flags: { total_flags: 4, enabled_flags: 2, disabled_flags: 1, gradual_rollout_flags: 1, flags_with_health_issues: 0, pending_flag_changes: 0, average_health_score: 98 },
            experiments: { total_experiments: 1, running_experiments: 1, completed_experiments: 0, paused_experiments: 0, positive_recommendation_experiments: 1, breached_guardrail_experiments: 0 },
            approvals: { pending_approvals: 0, approved_operations: 2, rejected_operations: 0, expired_approvals: 0 },
            agents: { total_agent_executions: 3, successful_executions: 3, failed_executions: 0, running_executions: 0, cancelled_executions: 0, awaiting_approval_executions: 0, average_execution_duration_seconds: 1.5, agent_tool_calls: 6, total_tokens: 1200 },
          },
          metric_definitions: [
            { metric_name: "Rollback Rate", formula: "rollbacks / total_rollouts", source_of_truth: "rollout_history", availability: "available", scope: "windowed" },
          ],
        });
      }
      if (url.includes("/api/v1/metrics/")) {
        return jsonResp({
          items: [],
          rows: [],
          charts: [],
          telemetry_summary: {},
          kpis: { cd_deployment_frequency: "Unavailable", cd_lead_time_for_changes: "Unavailable" },
        });
      }
      if (url.includes("/health")) return jsonResp({ status: "ok", database: "ok", gemini_configured: true });
      if (url.includes("/auth/status")) return jsonResp({ require_auth: false, authenticated: true });
      if (url.includes("/approvals")) return jsonResp({ items: [], total: 0 });
      return jsonResp({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const r1 = render(<SystemHealthPage />);
    await waitFor(() => expect(screen.getByText(/OVERALL STATUS: DEGRADED/i)).toBeInTheDocument());
    expect(screen.getByText("1.4 ms")).toBeInTheDocument();
    r1.unmount();

    const r2 = render(<NotificationsPage />);
    await waitFor(() => expect(screen.getByText("Notification Centre")).toBeInTheDocument());
    expect(screen.getByText(/Delivery Preferences/i)).toBeInTheDocument();
    r2.unmount();

    const r3 = render(<MetricsPage />);
    await waitFor(() => expect(screen.getByText("Metrics & Analytics")).toBeInTheDocument());
    expect(screen.getByText(/Export CSV/i)).toBeInTheDocument();
    r3.unmount();
  });
});
