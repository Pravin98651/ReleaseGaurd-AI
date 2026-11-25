import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  usePathname: () => "/risk-intelligence",
  useParams: () => ({ assessmentId: "risk-1" }),
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

import RiskIntelligencePage from "@/app/risk-intelligence/page";
import RiskAssessmentDetailPage from "@/app/risk-intelligence/[assessmentId]/page";

function jsonResp(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Intelligent Release Risk Engine UI", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders RiskIntelligencePage overview and assessment table", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          items: [
            {
              id: "risk-1",
              assessment_key: "RSK-2026-0001",
              target_environment: "production",
              assessment_status: "partial",
              is_current: true,
              risk_score: 76,
              risk_level: "high",
              data_completeness: 0.86,
              flag_suitability: "highly_suitable",
              scoring_version: 1,
              analyzer_version: "1.0.0",
              evaluated_dimensions: ["code_impact"],
              unavailable_dimensions: ["historical_failures"],
              limitations: [],
              critical_overrides: [],
              evidence_summary: {},
              dependency_graph: {},
              fallback_mode: true,
              created_at: "2026-11-25T10:00:00Z",
              updated_at: "2026-11-25T10:00:00Z",
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
    );

    render(<RiskIntelligencePage />);

    await waitFor(() => {
      expect(screen.getByText("Intelligent Release Risk Engine")).toBeInTheDocument();
      expect(screen.getByText("RSK-2026-0001")).toBeInTheDocument();
      expect(screen.getByText("76/100")).toBeInTheDocument();
    });
  });

  it("renders RiskAssessmentDetailPage with dimensions and limitations", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          assessment: {
            id: "risk-1",
            assessment_key: "RSK-2026-0001",
            target_environment: "production",
            assessment_status: "partial",
            is_current: true,
            risk_score: 76,
            risk_level: "high",
            assessment_confidence: 0.82,
            data_completeness: 0.86,
            flag_suitability: "highly_suitable",
            scoring_version: 1,
            analyzer_version: "1.0.0",
            evaluated_dimensions: ["code_impact"],
            unavailable_dimensions: ["historical_failures"],
            limitations: ["Historical data insufficient."],
            critical_overrides: [],
            evidence_summary: {},
            dependency_graph: { direct_modules: ["checkout.py"], edges: [] },
            fallback_mode: true,
            created_at: "2026-11-25T10:00:00Z",
            updated_at: "2026-11-25T10:00:00Z",
          },
          dimensions: [
            {
              id: "d-1",
              assessment_id: "risk-1",
              dimension_key: "code_impact",
              dimension_name: "Code-Change Impact",
              score: 76,
              weight: 0.2,
              weighted_contribution: 15.2,
              severity: "high",
              status: "evaluated",
              data_sufficiency: "sufficient",
              evidence_quality: "high",
              explanations: ["Payment module changed."],
              limitations: [],
              details_json: {},
              created_at: "2026-11-25T10:00:00Z",
            },
          ],
          findings: [],
          evidence: [],
          mitigations: [],
          scenarios: [],
        }),
      ),
    );

    render(<RiskAssessmentDetailPage />);

    await waitFor(() => {
      expect(screen.getByText(/RSK-2026-0001 — Risk Score: 76\/100/)).toBeInTheDocument();
      expect(screen.getByText("Code-Change Impact")).toBeInTheDocument();
      expect(screen.getByText("Historical data insufficient.")).toBeInTheDocument();
    });
  });
});
