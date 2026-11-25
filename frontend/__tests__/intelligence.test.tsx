import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  usePathname: () => "/intelligence",
  useParams: () => ({ repositoryId: "repo-1" }),
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

import ReleaseIntelligencePage from "@/app/intelligence/page";
import RepositoryIntelligenceDetailPage from "@/app/intelligence/repositories/[repositoryId]/page";

function jsonResp(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Release Intelligence & Learning Engine UI", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders ReleaseIntelligencePage overview KPIs, tabs, and insights", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/v1/intelligence/overview")) {
          return jsonResp({
            performance: {
              window_days: 30,
              total_records: 4,
              qualifying_releases: 3,
              successful_releases: 2,
              failed_releases: 1,
              rolled_back_releases: 1,
              verified_outcomes: 3,
              incomplete_outcomes: 0,
              deployment_frequency_per_week: 0.47,
              change_failure_rate: 0.3333,
              change_failure_rate_ci: { lower: 0.06, upper: 0.79, sample_size: 3 },
              rollback_rate: 0.3333,
              release_success_rate: 0.6667,
              mean_time_to_recovery_seconds: 120,
              guardrail_breach_rate: 0.1,
              guardrail_evaluations_count: 10,
              data_sufficiency: "insufficient_sample",
              definitions: {
                qualifying_denominator: "Excludes drafts and pre-deploy cancellations.",
                change_failure_rate: "failed_or_rolled_back / qualifying_releases",
              },
            },
            trend_direction: "improving",
            previous_period: {},
            rollback_summary: { total_rollbacks: 1, top_causes: [{ category: "payment_failure", count: 1 }] },
            recommendation_accuracy: {
              total_recommendations: 2,
              responded_count: 2,
              accepted_count: 2,
              rejected_count: 0,
              implemented_count: 2,
              verified_count: 1,
              evaluable_count: 2,
              positive_outcome_count: 1,
              negative_outcome_count: 1,
              inconclusive_or_not_evaluable_count: 0,
              acceptance_rate: 1.0,
              positive_outcome_rate: 0.5,
              by_category: {},
              by_model: {},
              by_agent: {},
              methodology_note: "Evaluated against verified outcomes.",
            },
            top_patterns: [],
            insights: [
              {
                id: "ins-1",
                insight_key: "insight:perf:workspace",
                title: "30-Day Release Reliability Summary",
                description: "Across 3 qualifying releases, 2 succeeded and 1 failed.",
                insight_category: "historical_performance",
                evidence: {},
                evidence_classification: "observed",
                affected_repositories: [],
                affected_releases: [],
                sample_size: 3,
                confidence_data: {},
                severity: "high",
                status: "active",
                generated_by: "learning_engine",
                created_at: "2026-11-25T10:00:00Z",
                updated_at: "2026-11-25T10:00:00Z",
              },
            ],
            pending_policy_recommendations: 1,
          });
        }
        if (url.includes("/api/v1/intelligence/rollback-analysis")) {
          return jsonResp({
            total_rollbacks: 1,
            top_causes: [{ category: "payment_failure", count: 1 }],
            by_environment: { production: 1 },
            by_rollout_percentage: { "5": 1 },
            rollbacks: [],
          });
        }
        return jsonResp({ items: [], total: 0, limit: 50, offset: 0 });
      }),
    );

    render(<ReleaseIntelligencePage />);
    await waitFor(() => {
      expect(screen.getByText("Release Intelligence & Learning Engine")).toBeInTheDocument();
    });
    expect(screen.getByText("33.3%")).toBeInTheDocument();
    expect(screen.getByText("30-Day Release Reliability Summary")).toBeInTheDocument();
    expect(screen.getByText("Knowledge Assistant")).toBeInTheDocument();
  });

  it("renders RepositoryIntelligenceDetailPage with modules and recommendations", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          id: "prof-1",
          repository_id: "repo-1",
          repository_name: "novashop/checkout-service",
          release_count: 5,
          failure_count: 2,
          rollback_count: 1,
          historical_failure_rate: 0.4,
          common_failure_categories: [{ category: "payment_failure", count: 2 }],
          high_risk_modules: [
            { module: "checkout", release_count: 4, failure_count: 2, failure_rate: 0.5 },
          ],
          dependency_patterns: [],
          risk_indicators: {},
          recommendations: [
            {
              title: "Mandate payment_failed guardrail",
              rationale: "Historical payment failures observed.",
              policy_area: "guardrail_thresholds",
            },
          ],
          last_analyzed_at: "2026-11-25T10:00:00Z",
          version: 1,
        }),
      ),
    );

    render(<RepositoryIntelligenceDetailPage />);
    await waitFor(() => {
      expect(screen.getByText("Repository Intelligence: novashop/checkout-service")).toBeInTheDocument();
    });
    expect(screen.getByText("checkout/")).toBeInTheDocument();
    expect(screen.getByText("Mandate payment_failed guardrail")).toBeInTheDocument();
  });
});
