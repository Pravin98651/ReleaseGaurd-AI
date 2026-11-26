import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
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

import {
  EVIDENCE_CLASSES,
  EvidenceBadge,
  normalizeEvidenceClass,
} from "@/components/EvidenceBadge";
import { AppShell } from "@/components/AppShell";
import FlagsmithPage from "@/app/flagsmith/page";
import DashboardPage from "@/app/dashboard/page";

function jsonResp(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Phase 3 UI Shell, EvidenceBadge, and Non-Release Page Fixes", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("renders all 6 EvidenceBadge classes and normalizes legacy/unknown values", () => {
    const { rerender } = render(<EvidenceBadge evidenceClass="VERIFIED" />);
    for (const cls of EVIDENCE_CLASSES) {
      rerender(<EvidenceBadge evidenceClass={cls} />);
      const el = document.querySelector(`[data-evidence-class="${cls}"]`);
      expect(el).toBeInTheDocument();
    }

    expect(normalizeEvidenceClass("VERIFIED_MEASURED")).toBe("VERIFIED");
    expect(normalizeEvidenceClass("INCOMPLETE")).toBe("UNKNOWN");
    expect(normalizeEvidenceClass("ai_inferred")).toBe("AI_INFERRED");
    expect(normalizeEvidenceClass(null)).toBe("UNKNOWN");

    rerender(<EvidenceBadge evidenceClass="OBSERVED" label="Live Telemetry" compact />);
    expect(screen.getByText("Live Telemetry")).toBeInTheDocument();
  });

  it("collapses Advanced Navigation by default in AppShell", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResp({
          status: "ok",
          github_connected: false,
          flagsmith_connected: false,
          gemini_configured: false,
        })
      )
    );

    render(
      <AppShell>
        <div>Shell Child</div>
      </AppShell>
    );

    expect(screen.getByText("Shell Child")).toBeInTheDocument();
    expect(screen.queryByText("Repositories")).not.toBeInTheDocument();
  });

  it("shows non-emerald disconnected state on FlagsmithPage when project_id is absent", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResp({
          project_id: null,
          environments: ["development", "staging", "production"],
          catalog: [],
          experiments: [],
          pending_approvals: [],
          recent_executions: [],
          capability_flags: [],
          targeting_presets: [],
          metrics: {},
        })
      )
    );

    render(<FlagsmithPage />);

    const badge = await screen.findByText("Project Not Connected");
    expect(badge.className).not.toContain("emerald");
    expect(badge.className).toContain("amber");

    const disconnectedLabels = await screen.findAllByText("Disconnected");
    expect(disconnectedLabels.length).toBeGreaterThan(0);
    for (const el of disconnectedLabels) {
      expect(el.className).not.toContain("emerald");
    }
  });

  it("surfaces error banner on DashboardPage when repository or approval fetch fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/health")) {
          return Promise.resolve(
            jsonResp({
              status: "ok",
              github_connected: true,
              flagsmith_connected: true,
              gemini_configured: true,
            })
          );
        }
        if (url.includes("/api/dashboard/overview")) {
          return Promise.resolve(
            jsonResp({
              metrics: {
                repositories: { total: 0 },
                pull_requests: { total: 0, high_risk: 0 },
                feature_flags: { total: 0, average_health_score: null },
                experiments: { total: 0, active: 0 },
                releases: { total: 0, active: 0, completed: 0, rolled_back: 0 },
                approvals: { pending: 0 },
              },
              pending_actions: [],
              recent_activity: [],
              upcoming_schedules: [],
              charts: {
                filter: { range: "7d", start_at: "", end_at: "", event_source: "all" },
                release_activity: [],
                experiment_performance: [],
                flag_distribution: [],
                agent_activity: [],
                telemetry_activity: [],
                release_health: [],
                telemetry_totals: {
                  total_events: 0,
                  simulator_events: 0,
                  application_telemetry_events: 0,
                },
              },
            })
          );
        }
        return Promise.resolve(
          jsonResp({ detail: "Database unavailable" }, 503)
        );
      })
    );

    render(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByText(/Pending approvals:/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/Repositories:/i)).toBeInTheDocument();
  });
});
