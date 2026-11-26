import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  usePathname: () => "/command-center",
  useParams: () => ({ executionId: "exe-1" }),
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

import CommandCenterPage from "@/app/command-center/page";
import ExecutionDetailPage from "@/app/command-center/[executionId]/page";

function jsonResp(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Autonomous Release Command Center UI", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders CommandCenterPage KPIs and execution list", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: RequestInfo | URL) => {
        const s = String(url);
        if (s.includes("/overview")) {
          return jsonResp({
            total_executions: 2,
            active_executions: 1,
            queued_executions: 0,
            waiting_for_approval: 1,
            paused_executions: 0,
            failed_executions: 0,
            completed_executions: 0,
            average_duration_ms: 1200,
            tool_failure_rate: 0,
            total_tool_calls: 2,
            failed_tool_calls: 0,
          });
        }
        return jsonResp({
          items: [
            {
              id: "exe-1",
              execution_key: "EXE-2026-0001",
              agent_type: "pr_agent",
              agent_name: "ReleaseGuard PR Agent",
              task_name: "Analyze PR #42",
              status: "waiting_for_approval",
              priority: 5,
              execution_mode: "interactive",
              actor_type: "user",
              current_stage: "approval_gate",
              current_activity: "Waiting for human approval",
              completed_steps: 2,
              total_steps: 3,
              retry_count: 0,
              max_retries: 3,
              pause_requested: false,
              cancel_requested: false,
              correlation_id: "corr_123",
              execution_metadata: {},
              version: 3,
              created_at: "2026-11-25T10:00:00Z",
              updated_at: "2026-11-25T10:02:00Z",
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        });
      }),
    );

    render(<CommandCenterPage />);

    await waitFor(() => {
      expect(screen.getByText("AI Command Center")).toBeInTheDocument();
      expect(screen.getAllByText("EXE-2026-0001").length).toBeGreaterThan(0);
      expect(screen.getAllByText("Analyze PR #42").length).toBeGreaterThan(0);
    });
  });

  it("renders ExecutionDetailPage with controls and timeline", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          execution: {
            id: "exe-1",
            execution_key: "EXE-2026-0001",
            agent_type: "pr_agent",
            agent_name: "ReleaseGuard PR Agent",
            task_name: "Analyze PR #42",
            status: "running",
            priority: 5,
            execution_mode: "interactive",
            actor_type: "user",
            current_stage: "analyze_pr",
            current_activity: "Inspecting changed files",
            completed_steps: 1,
            total_steps: 2,
            retry_count: 0,
            max_retries: 3,
            pause_requested: false,
            cancel_requested: false,
            correlation_id: "corr_123",
            execution_metadata: {},
            version: 2,
            created_at: "2026-11-25T10:00:00Z",
            updated_at: "2026-11-25T10:02:00Z",
          },
          steps: [],
          tool_calls: [],
          decisions: [],
          traces: [
            {
              id: "tr-1",
              execution_id: "exe-1",
              sequence_number: 1,
              event_type: "execution.created",
              category: "lifecycle",
              actor_type: "user",
              message: "ReleaseGuard PR Agent queued task",
              payload: {},
              correlation_id: "corr_123",
              created_at: "2026-11-25T10:00:00Z",
            },
          ],
          checkpoints: [],
          approvals: [],
        }),
      ),
    );

    render(<ExecutionDetailPage />);

    await waitFor(() => {
      expect(screen.getByText(/EXE-2026-0001: Analyze PR #42/)).toBeInTheDocument();
      expect(screen.getByText("Pause")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Live Timeline/i }));
    expect(screen.getByText("ReleaseGuard PR Agent queued task")).toBeInTheDocument();
  });
});
