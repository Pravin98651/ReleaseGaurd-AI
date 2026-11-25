import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AuthGate } from "@/components/AuthGate";
import { ApprovalActionCard } from "@/components/ApprovalActionCard";
import { CodeDiffViewer } from "@/components/CodeDiffViewer";
import { RolloutStepper } from "@/components/RolloutStepper";
import { BayesianPosteriorChart } from "@/components/BayesianCharts";
import { ApiError, getErrorMessage, setStoredAuthToken, AUTH_TOKEN_STORAGE_KEY } from "@/lib/api";
import { subscribeToAgentTask } from "@/lib/sse";
import type { Approval, ExperimentAnalysis } from "@/types/releaseguard";

describe("ReleaseGuard AI Control Plane Components", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("renders AuthGate sign-in form when require_auth=true and unauthenticated, and submits login", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          access_token: "signed.test.token",
          token_type: "bearer",
          expires_in_seconds: 86400,
          user: {
            id: "u-1",
            email: "operator@releaseguard.local",
            display_name: "Operator",
            role: "admin",
            is_active: true,
            created_at: "2026-11-25T00:00:00Z",
            updated_at: "2026-11-25T00:00:00Z",
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const onAuthenticated = vi.fn();
    render(
      <AuthGate
        authStatus={{
          require_auth: true,
          authenticated: false,
          github_oauth_enabled: true,
          user: null,
        }}
        onAuthenticated={onAuthenticated}
      >
        <div>Protected Dashboard Content</div>
      </AuthGate>
    );

    expect(screen.getByText("ReleaseGuard AI Sign In")).toBeInTheDocument();
    expect(screen.queryByText("Protected Dashboard Content")).not.toBeInTheDocument();
    expect(screen.getByText("Continue with GitHub OAuth")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("Configured AUTH_OPERATOR_PASSWORD"), {
      target: { value: "StrongPass123" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Sign In to Control Plane/i }));

    await waitFor(() => {
      expect(onAuthenticated).toHaveBeenCalledTimes(1);
    });
    expect(window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY)).toBe("signed.test.token");
  });

  it("renders protected children immediately when authenticated=true", () => {
    render(
      <AuthGate
        authStatus={{
          require_auth: true,
          authenticated: true,
          github_oauth_enabled: false,
        }}
        onAuthenticated={vi.fn()}
      >
        <div>Protected Dashboard Content</div>
      </AuthGate>
    );
    expect(screen.getByText("Protected Dashboard Content")).toBeInTheDocument();
  });

  it("renders ApprovalActionCard details and triggers approve endpoint", async () => {
    const sampleApproval: Approval = {
      id: "app-123",
      policy_category: "C",
      action_type: "flagsmith_set_gradual_rollout_stage",
      target_resource_type: "feature_flag",
      target_resource_id: "flag-1",
      environment_name: "production",
      title: "Roll out checkout_v2 to 10% in production",
      rationale: "Staged rollout with guardrail protection",
      action_payload: { flag_key: "checkout_v2", target_percentage: 10 },
      expected_precondition: { enabled: false, rollout_percentage: 0 },
      action_hash: "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
      idempotency_key: "idem-1",
      status: "pending",
      expires_at: new Date(Date.now() + 600_000).toISOString(),
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

    const executedApproval: Approval = { ...sampleApproval, status: "executed" };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ approval: executedApproval }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);
    const onUpdated = vi.fn();

    render(<ApprovalActionCard approval={sampleApproval} onUpdated={onUpdated} />);
    expect(screen.getByText("Roll out checkout_v2 to 10% in production")).toBeInTheDocument();
    expect(screen.getByText(/Expected Precondition/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Approve & Execute/i }));
    await waitFor(() => {
      expect(onUpdated).toHaveBeenCalledWith(executedApproval);
    });
  });

  it("renders CodeDiffViewer tabs and validation checklist", () => {
    render(
      <CodeDiffViewer
        filesDiff={[
          {
            file_path: "app/checkout.py",
            action: "modify",
            before_code: "def run(): return 'v1'",
            after_code: "def run(flags): return 'v2' if flags.is_feature_enabled('checkout_v2') else 'v1'",
            unified_diff: "--- a/app/checkout.py\n+++ b/app/checkout.py\n@@ -1 +1 @@\n-v1\n+v2",
          },
        ]}
        generatedTests={[
          {
            file_path: "tests/test_checkout_flag.py",
            content: "def test_flag(): assert True",
          },
        ]}
        validationStatus="passed"
        validationReport={{ ast_ok: true, path_safety_ok: true, overwrite_ok: true }}
      />
    );

    expect(screen.getByText("app/checkout.py")).toBeInTheDocument();
    expect(screen.getByText("Before")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Unified Diff/i }));
    expect(screen.getByText("--- a/app/checkout.py")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Generated Tests/i }));
    expect(screen.getByText("tests/test_checkout_flag.py")).toBeInTheDocument();
  });

  it("renders RolloutStepper progression stages and handles stage click", () => {
    const onSelectStage = vi.fn();
    render(
      <RolloutStepper
        currentPercentage={10}
        environmentName="production"
        isProduction={true}
        onSelectStage={onSelectStage}
      />
    );
    const btn = screen.getByRole("button", { name: /Advance to 25%/i });
    fireEvent.click(btn);
    expect(onSelectStage).toHaveBeenCalledWith(25);
  });

  it("renders BayesianPosteriorChart and formats ApiError messages", () => {
    const analysis: ExperimentAnalysis = {
      experiment_id: "exp-1",
      analytics_mode: "tracked_event_fallback",
      sample_adequacy: {
        control_exposures: 100,
        treatment_exposures: 100,
        min_required: 100,
        is_adequate: true,
      },
      control: {
        exposures: 100,
        conversions: 12,
        posterior_alpha: 13,
        posterior_beta: 89,
        mean_rate: 0.1275,
        credible_interval_95: [0.07, 0.19],
      },
      treatment: {
        exposures: 100,
        conversions: 22,
        posterior_alpha: 23,
        posterior_beta: 79,
        mean_rate: 0.2255,
        credible_interval_95: [0.15, 0.31],
      },
      comparison: {
        prob_treatment_beats_control: 0.96,
        absolute_lift_mean: 0.098,
        absolute_lift_cri_95: [0.01, 0.19],
        relative_lift_mean: 0.77,
        relative_lift_cri_95: [0.05, 1.5],
        expected_loss_choose_treatment: 0.001,
        expected_loss_choose_control: 0.099,
      },
      guardrails: {},
    };
    render(<BayesianPosteriorChart analysis={analysis} />);
    expect(screen.getByText(/Posterior Conversion Density/i)).toBeInTheDocument();

    const apiErr = new ApiError({ status: 403, code: "FORBIDDEN_ROLE", message: "Denied" });
    expect(getErrorMessage(apiErr)).toBe("[FORBIDDEN_ROLE] Denied");
  });

  it("falls back to polling in subscribeToAgentTask when EventSource is unavailable", async () => {
    setStoredAuthToken(null);
    const origES = globalThis.EventSource;
    // @ts-expect-error testing missing EventSource fallback
    delete globalThis.EventSource;

    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "task-1",
          session_id: "sess-1",
          specialist: "pr_agent",
          user_prompt: "Analyze PR",
          status: "completed",
          steps_log: [
            { seq: 1, event_type: "agent.completed", timestamp: "2026-11-25T00:00:00Z", payload: {} },
          ],
          final_response: "All safe.",
          created_at: "2026-11-25T00:00:00Z",
          updated_at: "2026-11-25T00:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const onModeChange = vi.fn();
    const onStep = vi.fn();
    const onTaskUpdate = vi.fn();
    const stop = subscribeToAgentTask("task-1", 0, {
      onStep,
      onTaskUpdate,
      onModeChange,
    });

    await waitFor(() => {
      expect(onStep).toHaveBeenCalled();
      expect(onTaskUpdate).toHaveBeenCalledWith(expect.objectContaining({ status: "completed" }));
    });
    expect(onModeChange).toHaveBeenCalledWith("polling");
    stop();
    globalThis.EventSource = origES;
  });
});
