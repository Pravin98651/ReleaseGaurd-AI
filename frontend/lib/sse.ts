import { API_BASE_URL, getAgentTask } from "@/lib/api";
import type { AgentStepEvent, AgentTask, AgentTaskStatus } from "@/types/releaseguard";

const SSE_EVENT_TYPES = [
  "task.started",
  "agent.thinking",
  "agent.tool_proposed",
  "policy.evaluated",
  "approval.required",
  "tool.executed",
  "agent.completed",
  "task.failed",
  "heartbeat",
] as const;

const TERMINAL_STATUSES: ReadonlySet<AgentTaskStatus> = new Set([
  "completed",
  "failed",
  "cancelled",
]);

export interface TaskStreamCallbacks {
  onStep: (step: AgentStepEvent) => void;
  onTaskUpdate: (task: Partial<AgentTask>) => void;
  onModeChange?: (mode: "sse" | "polling" | "closed") => void;
  onError?: (message: string) => void;
}

/**
 * Subscribes to `/api/v1/agent/tasks/{taskId}/stream` via Server-Sent Events
 * with automatic `?after_seq=` polling fallback when EventSource drops or fails.
 */
export function subscribeToAgentTask(
  taskId: string,
  initialAfterSeq: number,
  callbacks: TaskStreamCallbacks
): () => void {
  let closed = false;
  let lastSeq = initialAfterSeq;
  let eventSource: EventSource | null = null;
  let pollTimer: ReturnType<typeof setTimeout> | null = null;
  let sawTerminal = false;

  const stopAll = () => {
    closed = true;
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
    callbacks.onModeChange?.("closed");
  };

  const handleParsedEvent = (eventType: string, rawData: string) => {
    if (closed) return;
    let payload: Record<string, unknown> = {};
    try {
      payload = rawData ? (JSON.parse(rawData) as Record<string, unknown>) : {};
    } catch {
      payload = { message: rawData };
    }

    if (eventType === "heartbeat") return;

    const seq = typeof payload.seq === "number" ? payload.seq : lastSeq + 1;
    if (seq > lastSeq) {
      lastSeq = seq;
    }

    callbacks.onStep({
      seq,
      event_type: eventType,
      timestamp:
        typeof payload.timestamp === "string"
          ? payload.timestamp
          : new Date().toISOString(),
      payload,
    });

    if (eventType === "approval.required") {
      callbacks.onTaskUpdate({ status: "awaiting_approval" });
    } else if (eventType === "agent.completed") {
      sawTerminal = true;
      callbacks.onTaskUpdate({
        status: "completed",
        final_response:
          typeof payload.final_response === "string" ? payload.final_response : undefined,
        token_usage:
          payload.token_usage && typeof payload.token_usage === "object"
            ? (payload.token_usage as AgentTask["token_usage"])
            : undefined,
      });
      stopAll();
    } else if (eventType === "task.failed") {
      sawTerminal = true;
      callbacks.onTaskUpdate({
        status: "failed",
        error_message:
          typeof payload.error === "string" ? payload.error : "Task execution failed",
      });
      stopAll();
    }
  };

  const startPollingFallback = () => {
    if (closed || sawTerminal) return;
    callbacks.onModeChange?.("polling");

    const pollOnce = async () => {
      if (closed || sawTerminal) return;
      try {
        const task = await getAgentTask(taskId, lastSeq);
        if (closed) return;

        if (Array.isArray(task.steps_log)) {
          for (const step of task.steps_log) {
            if (step.seq > lastSeq) {
              lastSeq = step.seq;
              callbacks.onStep(step);
            }
          }
        }

        callbacks.onTaskUpdate({
          status: task.status,
          final_response: task.final_response,
          error_message: task.error_message,
          token_usage: task.token_usage,
          pending_approvals: task.pending_approvals,
        });

        if (TERMINAL_STATUSES.has(task.status)) {
          sawTerminal = true;
          stopAll();
          return;
        }
      } catch (err) {
        callbacks.onError?.(err instanceof Error ? err.message : "Task poll failed");
      }

      if (!closed && !sawTerminal) {
        pollTimer = setTimeout(pollOnce, 1500);
      }
    };

    pollTimer = setTimeout(pollOnce, 400);
  };

  if (typeof window !== "undefined" && typeof EventSource !== "undefined") {
    try {
      const storedToken = window.localStorage.getItem("rg_auth_token");
      const tokenParam = storedToken ? `&token=${encodeURIComponent(storedToken)}` : "";
      const url = `${API_BASE_URL}/api/v1/agent/tasks/${taskId}/stream?after_seq=${lastSeq}${tokenParam}`;
      eventSource = new EventSource(url, { withCredentials: true });
      callbacks.onModeChange?.("sse");

      for (const evtName of SSE_EVENT_TYPES) {
        eventSource.addEventListener(evtName, (ev: Event) => {
          const msgEv = ev as MessageEvent<string>;
          handleParsedEvent(evtName, msgEv.data);
        });
      }

      eventSource.onmessage = (ev: MessageEvent<string>) => {
        try {
          const parsed = JSON.parse(ev.data) as { event_type?: string };
          handleParsedEvent(parsed.event_type || "agent.thinking", ev.data);
        } catch {
          handleParsedEvent("agent.thinking", ev.data);
        }
      };

      eventSource.onerror = () => {
        if (closed || sawTerminal) return;
        if (eventSource) {
          eventSource.close();
          eventSource = null;
        }
        startPollingFallback();
      };
    } catch {
      startPollingFallback();
    }
  } else {
    startPollingFallback();
  }

  return stopAll;
}
