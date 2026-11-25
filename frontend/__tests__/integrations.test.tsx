import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  usePathname: () => "/integrations",
  useParams: () => ({ token: "test-share-token" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}));

import IntegrationsCenterPage from "@/app/integrations/page";
import SharedReadinessReportPage from "@/app/shared/[token]/page";

function jsonResp(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Phase 6 Developer Experience Integration UI", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders IntegrationsCenterPage with GitHub installations, webhooks, and Slack/Discord", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/v1/integrations/github/installations")) {
          return jsonResp([
            {
              id: "inst-1",
              installation_id: 77001,
              account_login: "novashop",
              account_type: "Organization",
              repository_selection: "selected",
              repositories: [{ full_name: "novashop/checkout-dx" }],
              permissions: { checks: "write" },
              status: "connected",
              created_at: "2026-11-26T00:00:00Z",
              updated_at: "2026-11-26T00:00:00Z",
            },
          ]);
        }
        if (url.includes("/api/v1/integrations/github/events")) {
          return jsonResp([
            {
              id: "ev-1",
              delivery_id: "deliv-1234567890",
              event_type: "pull_request",
              action: "opened",
              pr_number: 42,
              signature_verified: true,
              processing_status: "processed",
              retry_count: 0,
              correlation_id: "corr-1",
              payload_summary: {},
              received_at: "2026-11-26T00:00:00Z",
            },
          ]);
        }
        return jsonResp({
          connections: [
            { id: "c-1", provider: "slack", status: "connected", account_label: "#releases" },
          ],
          deliveries: [],
        });
      }),
    );

    render(<IntegrationsCenterPage />);
    await waitFor(() => {
      expect(screen.getByText("Developer Integrations Center")).toBeInTheDocument();
    });
    expect(screen.getByText("novashop (#77001)")).toBeInTheDocument();
    expect(screen.getByText("pull_request.opened")).toBeInTheDocument();
  });

  it("renders SharedReadinessReportPage from share token", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          id: "rep-1",
          release_id: "rel-1",
          report_key: "RPT-REL-1",
          title: "Readiness Report: REL-1",
          readiness_outcome: "ready",
          report_data: {},
          markdown_content: "# Release Readiness Report: REL-1",
          created_at: "2026-11-26T00:00:00Z",
        }),
      ),
    );

    render(<SharedReadinessReportPage />);
    await waitFor(() => {
      expect(screen.getByText("Readiness Report: REL-1")).toBeInTheDocument();
    });
    expect(screen.getByText("# Release Readiness Report: REL-1")).toBeInTheDocument();
  });
});
