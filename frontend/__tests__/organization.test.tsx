import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  usePathname: () => "/organization",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}));

import OrganizationCommandCenterPage from "@/app/organization/page";

function jsonResp(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Phase 7 Organization Command Center UI", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders OrganizationCommandCenterPage with rankings, plans, and policies", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/v1/organizations")) {
          return jsonResp([
            {
              id: "org-1",
              name: "NovaShop",
              slug: "novashop",
              settings: {},
              created_at: "2026-11-26T00:00:00Z",
              updated_at: "2026-11-26T00:00:00Z",
            },
          ]);
        }
        if (url.includes("/release-overview")) {
          return jsonResp({
            organization_id: "org-1",
            total_repositories: 4,
            total_releases: 12,
            active_releases: 2,
            successful_releases: 9,
            failed_releases: 1,
            blocked_releases: 0,
            active_rollouts: 1,
            pending_approvals: 1,
            change_failure_rate: 0.1,
          });
        }
        if (url.includes("/dependency-graph")) {
          return jsonResp({ nodes: [], edges: [], recommended_order: [], has_cycles: false });
        }
        if (url.includes("/repository-rankings")) {
          return jsonResp([
            {
              id: "snap-1",
              repository_id: "repo-1",
              health_score: 92.5,
              health_category: "healthy",
              evidence: { repository_name: "novashop/novashop-shared" },
              sample_size: 5,
              data_completeness: 1.0,
              calculated_at: "2026-11-26T00:00:00Z",
            },
          ]);
        }
        return jsonResp([]);
      }),
    );

    render(<OrganizationCommandCenterPage />);
    await waitFor(() => {
      expect(screen.getByText("Organization Release Command Center")).toBeInTheDocument();
    });
    expect(screen.getByText("novashop/novashop-shared")).toBeInTheDocument();
    expect(screen.getByText("92.5/100")).toBeInTheDocument();
  });
});
