import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  usePathname: () => "/releases",
  useParams: () => ({ id: "rel-1" }),
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

import { ReleaseLifecycleList } from "@/components/ReleaseLifecycleList";
import ReleaseDetailPage from "@/app/releases/[id]/page";

function jsonResp(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Autonomous Release Lifecycle UI", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders ReleaseLifecycleList with release rows and readiness score", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          items: [
            {
              id: "rel-1",
              release_key: "REL-2026-0001",
              title: "Checkout v2 Release",
              release_type: "feature",
              target_environment: "staging",
              status: "awaiting_approval",
              version: 2,
              risk_level: "medium",
              readiness_score: 85,
              readiness_status: "ready",
              readiness_summary: {},
              target_rollout_percentage: 50,
              current_rollout_percentage: 0,
              metadata_json: {},
              created_at: "2026-11-25T10:00:00Z",
              updated_at: "2026-11-25T10:05:00Z",
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
    );

    render(<ReleaseLifecycleList />);

    await waitFor(() => {
      expect(screen.getByText("REL-2026-0001")).toBeInTheDocument();
      expect(screen.getByText("Checkout v2 Release")).toBeInTheDocument();
      expect(screen.getByText("85/100")).toBeInTheDocument();
    });
  });

  it("renders ReleaseDetailPage with stages, checks, and lifecycle controls", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          release: {
            id: "rel-1",
            release_key: "REL-2026-0001",
            title: "Checkout v2 Release",
            release_type: "feature",
            target_environment: "staging",
            status: "draft",
            version: 1,
            risk_level: "medium",
            readiness_score: 70,
            readiness_status: "needs_attention",
            readiness_summary: { blocking_issues: [] },
            target_rollout_percentage: 25,
            current_rollout_percentage: 0,
            metadata_json: {},
            created_at: "2026-11-25T10:00:00Z",
            updated_at: "2026-11-25T10:05:00Z",
          },
          stages: [
            {
              id: "st-1",
              release_id: "rel-1",
              stage_key: "draft",
              stage_order: 1,
              title: "Draft & Scope",
              status: "passed",
              attempt_count: 1,
              max_attempts: 3,
              details_json: {},
              created_at: "2026-11-25T10:00:00Z",
              updated_at: "2026-11-25T10:00:00Z",
            },
          ],
          checks: [
            {
              id: "chk-1",
              release_id: "rel-1",
              check_key: "approval_gate",
              category: "approval",
              title: "Human Approval Gate",
              status: "passed",
              is_blocking: false,
              weight: 25,
              message: "Ready",
              details_json: {},
            },
          ],
          events: [
            {
              id: "ev-1",
              release_id: "rel-1",
              event_type: "release.created",
              actor_type: "user",
              title: "Release REL-2026-0001 created",
              payload_json: {},
              created_at: "2026-11-25T10:00:00Z",
            },
          ],
          artifacts: [],
          approvals: [],
        }),
      ),
    );

    render(<ReleaseDetailPage />);

    await waitFor(() => {
      expect(screen.getByText(/REL-2026-0001: Checkout v2 Release/)).toBeInTheDocument();
      expect(screen.getByText("Draft & Scope")).toBeInTheDocument();
      expect(screen.getByText("Human Approval Gate")).toBeInTheDocument();
      expect(screen.getByText("Evaluate Readiness")).toBeInTheDocument();
    });
  });
});
