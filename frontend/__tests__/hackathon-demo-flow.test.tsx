import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

let mockPathname = "/dashboard";

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
  useParams: () => ({ id: "rel-demo-1", executionId: "exe-demo-1" }),
  useSearchParams: () => new URLSearchParams(),
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

import { DemoPathBar } from "@/components/DemoPathBar";
import DashboardPage from "@/app/dashboard/page";
import ReleaseDetailPage from "@/app/releases/[id]/page";
import ExecutionDetailPage from "@/app/command-center/[executionId]/page";
import ExperimentsPage from "@/app/experiments/page";

function jsonResp(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Hackathon 5-Minute Demo Flow UI", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockPathname = "/dashboard";
  });

  it("renders 5-Minute Demo Path navigation with all 6 guided steps", () => {
    mockPathname = "/dashboard";
    render(<DemoPathBar pendingApprovalsCount={2} />);

    expect(screen.getByText("5-Minute Demo Path")).toBeInTheDocument();
    expect(screen.getByText(/Step 1 of 6/i)).toBeInTheDocument();

    const expectedSteps: Array<[string, string]> = [
      ["Dashboard", "/dashboard"],
      ["Command Center", "/command-center"],
      ["PR Analysis", "/pull-requests"],
      ["Flagsmith & Exp", "/flagsmith"],
      ["Approvals & Rollout", "/approvals"],
      ["Release Report", "/releases"],
    ];

    for (const [label, href] of expectedSteps) {
      const link = screen.getByRole("link", { name: new RegExp(label, "i") });
      expect(link).toHaveAttribute("href", href);
    }
  });

  it("renders Central Release Workspace and NovaShop Demo Mode synthetic labelling on Dashboard", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: RequestInfo | URL) => {
        const s = String(url);
        if (s.includes("/api/v1/health") || s.includes("/health")) {
          return jsonResp({
            status: "ok",
            database: "connected",
            github: "configured",
            flagsmith: "configured",
            gemini: "configured",
          });
        }
        if (s.includes("/api/v1/dashboard/overview")) {
          return jsonResp({
            kpis: {
              total_releases: 3,
              active_releases: 1,
              pending_approvals: 1,
              active_experiments: 1,
              active_flags: 2,
              failed_releases: 0,
              rollbacks: 0,
              average_readiness_score: 86,
            },
            charts: {
              filter: {
                range: "7d",
                start_at: "2026-11-19T00:00:00Z",
                end_at: "2026-11-26T00:00:00Z",
                event_source: "all",
              },
              release_activity: [],
              experiment_performance: [],
              flag_distribution: [],
              agent_activity: [],
              telemetry_activity: [],
              release_health: [],
              telemetry_totals: {
                total_events: 120,
                simulator_events: 85,
                application_telemetry_events: 35,
              },
            },
            pending_actions: [],
            recent_releases: [],
            recent_executions: [],
          });
        }
        if (s.includes("/api/v1/approvals")) {
          return jsonResp({ items: [], total: 0, limit: 5, offset: 0 });
        }
        if (s.includes("/api/v1/repositories")) {
          return jsonResp({
            items: [
              {
                id: "repo-1",
                full_name: "novashop/storefront",
                default_branch: "main",
                is_active: true,
              },
            ],
            total: 1,
            limit: 50,
            offset: 0,
          });
        }
        if (s.includes("/api/v1/pull-requests")) {
          return jsonResp({
            items: [
              {
                id: "pr-1",
                repository_id: "repo-1",
                github_pr_number: 42,
                title: "Add express checkout experiment",
                state: "open",
                head_sha: "abc1234",
                base_branch: "main",
                head_branch: "feat/express-checkout",
                author: "alice",
                created_at: "2026-11-26T08:00:00Z",
                updated_at: "2026-11-26T08:00:00Z",
              },
            ],
            total: 1,
            limit: 50,
            offset: 0,
          });
        }
        return jsonResp({ items: [], total: 0 });
      }),
    );

    render(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByText("Start an AI-powered release")).toBeInTheDocument();
      expect(screen.getByText("Demo Mode · NovaShop")).toBeInTheDocument();
    });

    expect(screen.getByText("Synthetic Data")).toBeInTheDocument();
    expect(screen.getByText("ENV: development only")).toBeInTheDocument();
    expect(screen.getByText("NEVER MUTATES PRODUCTION")).toBeInTheDocument();
    expect(screen.getAllByText(/85 events/i).length).toBeGreaterThan(0);

    const objectiveInput = screen.getByLabelText(/Natural-Language Release Objective/i) as HTMLTextAreaElement;
    const templateBtn = screen.getByRole("button", { name: /Use NovaShop checkout template/i });
    fireEvent.click(templateBtn);

    expect(objectiveInput.value.toLowerCase()).toContain("checkout");
  });

  it("renders 9-stage Visual Release Lifecycle and Final Release Report with fact/simulation/inference separation", async () => {
    mockPathname = "/releases/rel-demo-1";

    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: RequestInfo | URL) => {
        const s = String(url);
        if (s.includes("/api/v1/releases/rel-demo-1")) {
          return jsonResp({
            release: {
              id: "rel-demo-1",
              release_key: "REL-2026-0042",
              title: "NovaShop Express Checkout",
              release_type: "feature",
              target_environment: "development",
              status: "awaiting_approval",
              version: 2,
              risk_level: "medium",
              readiness_score: 88,
              readiness_status: "ready",
              readiness_summary: { blocking_issues: [] },
              target_rollout_percentage: 25,
              current_rollout_percentage: 10,
              repository_id: "repo-1",
              pull_request_id: "pr-1",
              feature_flag_id: "flag-1",
              experiment_id: "exp-1",
              metadata_json: {},
              created_at: "2026-11-26T08:00:00Z",
              updated_at: "2026-11-26T08:30:00Z",
            },
            stages: [
              {
                id: "st-1",
                release_id: "rel-demo-1",
                stage_key: "pr_analysis",
                stage_order: 1,
                title: "PR Analysis",
                status: "passed",
                attempt_count: 1,
                max_attempts: 3,
                details_json: {},
                created_at: "2026-11-26T08:00:00Z",
                updated_at: "2026-11-26T08:05:00Z",
              },
            ],
            checks: [],
            approvals: [],
            artifacts: [],
            audit_logs: [],
            events: [],
          });
        }
        if (s.includes("/api/v1/agent-executions")) {
          return jsonResp({ items: [] });
        }
        if (s.includes("/api/v1/risk-assessments")) {
          return jsonResp({ items: [] });
        }
        if (s.includes("/api/v1/progressive-rollouts")) {
          return jsonResp({ items: [] });
        }
        if (s.includes("/api/v1/repositories")) {
          return jsonResp({ items: [{ id: "repo-1", full_name: "novashop/storefront" }] });
        }
        if (s.includes("/api/v1/pull-requests/pr-1")) {
          return jsonResp({
            id: "pr-1",
            github_pr_number: 42,
            title: "Express Checkout",
            head_sha: "deadbeef1234",
            state: "open",
            base_branch: "main",
            head_branch: "feat/express",
          });
        }
        if (s.includes("/api/v1/feature-flags")) {
          return jsonResp({
            items: [
              {
                id: "flag-1",
                key: "express_checkout",
                name: "Express Checkout",
                flag_type: "release",
                health_score: 95,
                health_issues: [],
                env_states: { development: { enabled: true } },
              },
            ],
          });
        }
        if (s.includes("/api/v1/experiments/exp-1/analysis")) {
          return jsonResp(null);
        }
        if (s.includes("/api/v1/experiments/exp-1/recommendation")) {
          return jsonResp(null);
        }
        if (s.includes("/api/v1/experiments")) {
          return jsonResp({ items: [] });
        }
        return jsonResp({ items: [] });
      }),
    );

    render(<ReleaseDetailPage />);

    await waitFor(() => {
      expect(screen.getByText("9-Stage Visual Release Lifecycle")).toBeInTheDocument();
    });

    const stageTitles = [
      "PR Analysis",
      "Risk Assessment",
      "Code Patch Validation",
      "Feature Flag Setup",
      "Experimentation",
      "Release Recommendation",
      "Human Approval",
      "Progressive Rollout",
      "Post-Release Verification",
    ];
    for (const title of stageTitles) {
      expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    }

    const reportTabs = screen.getAllByRole("button", { name: /^Final Release Report$/i });
    fireEvent.click(reportTabs[reportTabs.length - 1]);

    await waitFor(() => {
      expect(screen.getByText("Structured Final Release Report")).toBeInTheDocument();
    });

    expect(screen.getAllByText("Verified Fact").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Observation").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Simulation").length).toBeGreaterThan(0);
    expect(screen.getAllByText("AI Inference").length).toBeGreaterThan(0);
  });

  it("renders Command Center supervisor decisions, policy decisions, and approval interrupts", async () => {
    mockPathname = "/command-center/exe-demo-1";

    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          execution: {
            id: "exe-demo-1",
            execution_key: "EXE-2026-0099",
            agent_type: "supervisor",
            agent_name: "ReleaseGuard Supervisor",
            task_name: "Orchestrate NovaShop Checkout Release",
            status: "waiting_for_approval",
            priority: 5,
            execution_mode: "interactive",
            actor_type: "user",
            current_stage: "approval_gate",
            current_activity: "Awaiting human sign-off for staging rollout",
            completed_steps: 3,
            total_steps: 5,
            retry_count: 0,
            max_retries: 3,
            pause_requested: false,
            cancel_requested: false,
            correlation_id: "corr_demo_99",
            execution_metadata: {
              user_instruction: "Safely roll out express checkout to staging with 25% traffic",
              specialist_selected: "flag_agent",
            },
            version: 4,
            created_at: "2026-11-26T08:00:00Z",
            updated_at: "2026-11-26T08:05:00Z",
          },
          steps: [],
          tool_calls: [
            {
              id: "tc-1",
              tool_name: "update_flagsmith_rollout",
              status: "waiting_for_approval",
              input_json: { flag_key: "express_checkout", percentage: 25 },
              output_json: null,
              created_at: "2026-11-26T08:04:00Z",
            },
          ],
          decisions: [
            {
              id: "dec-1",
              decision_type: "supervisor_routing",
              decision_source: "ai_recommendation",
              decision_status: "accepted",
              decision_summary: "Route to Flag Agent for staged rollout",
              created_at: "2026-11-26T08:02:00Z",
            },
            {
              id: "dec-2",
              decision_type: "policy_gate",
              decision_source: "deterministic_rule",
              decision_status: "requires_approval",
              decision_summary: "Category B Approval Required for Staging",
              created_at: "2026-11-26T08:03:00Z",
            },
          ],
          checkpoints: [],
          traces: [],
          approvals: [
            {
              id: "app-demo-1",
              action_type: "flag_rollout",
              target_resource: "express_checkout",
              environment_name: "staging",
              risk_level: "medium",
              status: "pending",
              requested_by: "flag_agent",
              reason: "Promote express_checkout to 25% in staging",
              payload_hash: "sha256:abc123",
              created_at: "2026-11-26T08:04:00Z",
            },
          ],
          artifacts: [],
        }),
      ),
    );

    render(<ExecutionDetailPage />);

    await waitFor(() => {
      expect(screen.getByText("Hidden CoT Redacted")).toBeInTheDocument();
      expect(screen.getByText("1. User Instruction")).toBeInTheDocument();
    });

    expect(screen.getByText(/2\. Supervisor Decisions/i)).toBeInTheDocument();
    expect(screen.getByText(/5\. Policy Decisions/i)).toBeInTheDocument();
    expect(screen.getByText(/7\. Approval Interrupts/i)).toBeInTheDocument();
    expect(screen.getAllByText("Route to Flag Agent for staged rollout").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Category B Approval Required for Staging").length).toBeGreaterThan(0);
  });

  it("renders Experiments insufficient-data and guardrail-breached states distinctly", async () => {
    mockPathname = "/experiments";

    const insufficientAnalysis = {
      experiment_id: "exp-insuff",
      experiment_outcome: "INSUFFICIENT_DATA",
      outcome_rationale: "Minimum sample size of 100 exposures per arm has not been reached.",
      analytics_mode: "tracked_event_fallback",
      environment_name: "development",
      primary_metric: "checkout_completed",
      sample_adequacy: {
        is_adequate: false,
        control_exposures: 18,
        treatment_exposures: 22,
        min_required: 100,
      },
      simulator_event_count: 40,
      telemetry_event_count: 0,
      control: {
        exposures: 18,
        unique_participants: 18,
        conversions: 3,
        mean_rate: 0.1667,
        conversion_rate: 0.1667,
        credible_interval_95: [0.05, 0.38],
      },
      treatment: {
        exposures: 22,
        unique_participants: 22,
        conversions: 6,
        mean_rate: 0.2727,
        conversion_rate: 0.2727,
        credible_interval_95: [0.12, 0.48],
      },
      comparison: {
        prob_treatment_beats_control: 0.78,
        expected_loss_choose_treatment: 0.025,
        relative_lift_mean: 0.635,
        absolute_improvement: 0.106,
      },
      guardrails: {
        payment_failed: {
          breached: false,
          severity: "none",
          control_rate: 0.0,
          treatment_rate: 0.0,
        },
      },
    };

    const breachedAnalysis = {
      ...insufficientAnalysis,
      experiment_id: "exp-breach",
      experiment_outcome: "GUARDRAIL_BREACHED",
      outcome_rationale: "payment_failed rate exceeded critical threshold in treatment.",
      sample_adequacy: {
        is_adequate: true,
        control_exposures: 150,
        treatment_exposures: 155,
        min_required: 100,
      },
      guardrails: {
        payment_failed: {
          breached: true,
          severity: "critical",
          control_rate: 0.01,
          treatment_rate: 0.12,
        },
      },
    };

    let currentMode: "insufficient" | "breached" = "insufficient";

    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: RequestInfo | URL) => {
        const s = String(url);
        if (s.includes("/api/v1/health") || s.includes("/health")) {
          return jsonResp({ status: "ok", flagsmith: "configured" });
        }
        if (s.includes("/api/v1/feature-flags")) {
          return jsonResp({ items: [], total: 0 });
        }
        if (s.includes("/analysis")) {
          return jsonResp(currentMode === "insufficient" ? insufficientAnalysis : breachedAnalysis);
        }
        if (s.includes("/recommendation")) {
          return jsonResp({
            experiment_id: currentMode === "insufficient" ? "exp-insuff" : "exp-breach",
            recommendation: currentMode === "insufficient" ? "KEEP_RUNNING" : "ROLLBACK",
            confidence: "low",
            rationale:
              currentMode === "insufficient"
                ? "Continue collecting exposures before deciding."
                : "Rollback treatment immediately due to payment_failed guardrail breach.",
            triggers:
              currentMode === "insufficient"
                ? ["sample_adequacy_not_met"]
                : ["payment_failed_guardrail_breached"],
            narrative: "AI suggests holding rollout until safety checks pass.",
          });
        }
        if (s.includes("/api/v1/experiments")) {
          return jsonResp({
            items: [
              {
                id: "exp-insuff",
                name: "NovaShop Express Checkout Test",
                flag_key: "express_checkout",
                environment_name: "development",
                status: "running",
                primary_metric: "checkout_completed",
                control_variation: "control",
                treatment_variation: "variant_b",
                traffic_allocation: { control: 50, treatment: 50 },
              },
            ],
            total: 1,
          });
        }
        return jsonResp({});
      }),
    );

    const { unmount } = render(<ExperimentsPage />);

    await waitFor(() => {
      expect(
        screen.getByText("INSUFFICIENT DATA — STATISTICAL POWER NOT MET (DO NOT SHIP)"),
      ).toBeInTheDocument();
    });
    expect(screen.getByText("Sample Adequacy: INADEQUATE")).toBeInTheDocument();
    expect(screen.getByText("Synthetic / Non-Prod Traffic")).toBeInTheDocument();

    unmount();
    currentMode = "breached";
    render(<ExperimentsPage />);

    await waitFor(() => {
      expect(
        screen.getByText("GUARDRAIL BREACHED — DO NOT PROMOTE / ROLLBACK RECOMMENDED"),
      ).toBeInTheDocument();
    });
    expect(screen.getByText("Deterministic Rule Triggers")).toBeInTheDocument();
  });
});
