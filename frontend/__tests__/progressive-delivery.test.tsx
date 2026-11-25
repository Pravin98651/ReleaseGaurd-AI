import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  usePathname: () => "/progressive-delivery",
  useParams: () => ({ rolloutId: "rollout-1" }),
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

import ProgressiveDeliveryPage from "@/app/progressive-delivery/page";
import ProgressiveRolloutDetailPage from "@/app/progressive-delivery/[rolloutId]/page";

function jsonResp(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Progressive Delivery Engine UI", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders ProgressiveDeliveryPage overview and rollout list", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          items: [
            {
              id: "rollout-1",
              rollout_key: "PRO-2026-0001",
              name: "Checkout v2 Controlled Rollout",
              feature_flag_id: "flag-1",
              feature_flag_key: "checkout_v2_progressive",
              environment: "staging",
              status: "observing",
              current_percentage: 5,
              target_percentage: 100,
              primary_metric_key: "checkout_completed",
              rollout_configuration: {},
              observation_configuration: {},
              rollback_configuration: {},
              version: 1,
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

    render(<ProgressiveDeliveryPage />);
    await waitFor(() => {
      expect(screen.getByText("Checkout v2 Controlled Rollout")).toBeInTheDocument();
    });
    expect(screen.getByText("PRO-2026-0001")).toBeInTheDocument();
    expect(screen.getByText("5% / 100%")).toBeInTheDocument();
  });

  it("renders ProgressiveRolloutDetailPage with 1% -> 100% stages, guardrails, and decisions", async () => {
    const stages = [1, 5, 10, 25, 50, 100].map((pct, idx) => ({
      id: `stage-${idx + 1}`,
      rollout_id: "rollout-1",
      stage_number: idx + 1,
      target_percentage: pct,
      status: idx === 0 ? "passed" : idx === 1 ? "observing" : "pending",
      minimum_observation_seconds: 300,
      maximum_observation_seconds: 1800,
      minimum_sample_size: 50,
      actual_sample_size: idx === 1 ? 65 : 0,
      healthy_evaluations_count: idx === 1 ? 1 : 0,
      created_at: "2026-11-25T10:00:00Z",
      updated_at: "2026-11-25T10:05:00Z",
    }));

    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          rollout: {
            id: "rollout-1",
            rollout_key: "PRO-2026-0001",
            name: "Checkout v2 Controlled Rollout",
            feature_flag_id: "flag-1",
            feature_flag_key: "checkout_v2_progressive",
            environment: "staging",
            status: "observing",
            current_stage_id: "stage-2",
            current_percentage: 5,
            target_percentage: 100,
            primary_metric_key: "checkout_completed",
            rollout_configuration: {},
            observation_configuration: {},
            rollback_configuration: {},
            version: 2,
            created_at: "2026-11-25T10:00:00Z",
            updated_at: "2026-11-25T10:05:00Z",
          },
          stages,
          guardrails: [
            {
              id: "gr-1",
              rollout_id: "rollout-1",
              metric_key: "payment_failed",
              operator: "lt",
              threshold: 0.05,
              severity: "critical",
              evaluation_window_seconds: 300,
              minimum_sample_size: 20,
              consecutive_breach_count: 1,
              current_breach_streak: 0,
              action: "recommend_rollback",
              enabled: true,
              created_at: "2026-11-25T10:00:00Z",
            },
          ],
          metrics: [
            {
              id: "m-1",
              rollout_id: "rollout-1",
              stage_id: "stage-2",
              metric_key: "checkout_completed",
              metric_value: 0.92,
              sample_size: 65,
              unit: "ratio",
              window_start: "2026-11-25T10:00:00Z",
              window_end: "2026-11-25T10:05:00Z",
              data_source: "experiment_events",
              freshness_status: "fresh",
              created_at: "2026-11-25T10:05:00Z",
            },
          ],
          evaluations: [
            {
              id: "ev-1",
              rollout_id: "rollout-1",
              stage_id: "stage-2",
              guardrail_id: "gr-1",
              observed_value: 0.015,
              threshold: 0.05,
              sample_size: 65,
              status: "pass",
              severity: "critical",
              evidence: {},
              evaluated_at: "2026-11-25T10:05:00Z",
            },
          ],
          decisions: [
            {
              id: "dec-1",
              rollout_id: "rollout-1",
              stage_id: "stage-2",
              decision_type: "continue_observation",
              decision_summary: "Observing stage 2 (5%): waiting for minimum observation window.",
              supporting_evidence: ["payment_failed=0.0150 < 0.05 (n=65)"],
              missing_evidence: ["Observation window elapsed 120s / 300s"],
              recommended_action: "continue_observation",
              decision_source: "guardrail_engine",
              created_at: "2026-11-25T10:05:00Z",
            },
          ],
          events: [
            {
              id: "evt-1",
              rollout_id: "rollout-1",
              stage_id: "stage-2",
              event_type: "rollout.stage_activated",
              actor_type: "system",
              message: "Activated stage 2 at 5%",
              structured_data: {},
              created_at: "2026-11-25T10:02:00Z",
            },
          ],
          rollbacks: [],
        }),
      ),
    );

    render(<ProgressiveRolloutDetailPage />);
    await waitFor(() => {
      expect(screen.getByText("Checkout v2 Controlled Rollout")).toBeInTheDocument();
    });
    for (const pct of ["1%", "5%", "10%", "25%", "50%", "100%"]) {
      expect(screen.getByText(pct)).toBeInTheDocument();
    }
    expect(screen.getByText("payment_failed")).toBeInTheDocument();
    expect(
      screen.getByText("Observing stage 2 (5%): waiting for minimum observation window."),
    ).toBeInTheDocument();
  });
});
