import type {
  AgentSession,
  AgentSpecialist,
  AgentTask,
  Approval,
  AuditLogEntry,
  AuthStatus,
  AuthTokenResponse,
  AuthUser,
  BranchInfo,
  CodeChange,
  DashboardSummary,
  EventsSummaryResponse,
  Experiment,
  ExperimentAnalysis,
  ExperimentRecommendation,
  FeatureFlag,
  FlagHealthResponse,
  IncidentInvestigationResult,
  IntegrationConnection,
  MutationOutcome,
  PaginatedResponse,
  PullRequest,
  ReleasesOverview,
  RepoScanResult,
  Repository,
  RolloutHistoryEntry,
  RolloutStage,
  SystemHealth,
  WorkspaceSettings,
} from "@/types/releaseguard";

export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000"
).replace(/\/+$/, "");

export const DEMO_APP_URL = (
  process.env.NEXT_PUBLIC_DEMO_APP_URL || "http://localhost:3001"
).replace(/\/+$/, "");

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown> | null;
  requestId: string | null;

  constructor(params: {
    status: number;
    code: string;
    message: string;
    details?: Record<string, unknown> | null;
    requestId?: string | null;
  }) {
    super(params.message);
    this.name = "ApiError";
    this.status = params.status;
    this.code = params.code;
    this.details = params.details ?? null;
    this.requestId = params.requestId ?? null;
  }
}

export function getErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return err.code ? `[${err.code}] ${err.message}` : err.message;
  }
  if (err instanceof Error) return err.message;
  return "Unexpected network or server error";
}

function buildQuery(params?: Record<string, string | number | boolean | undefined | null>): string {
  if (!params) return "";
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") {
      search.set(k, String(v));
    }
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

export const AUTH_TOKEN_STORAGE_KEY = "rg_auth_token";

export function getStoredAuthToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
}

export function setStoredAuthToken(token: string | null): void {
  if (typeof window === "undefined") return;
  if (token) {
    window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
  } else {
    window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
  }
  window.dispatchEvent(new CustomEvent("rg:auth-changed"));
}

async function requestJson<T>(
  path: string,
  init: RequestInit = {}
): Promise<{ status: number; data: T }> {
  const url = `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const storedTok = getStoredAuthToken();
  if (storedTok && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${storedTok}`);
  }

  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      headers,
      credentials: "include",
      cache: "no-store",
    });
  } catch (networkErr) {
    throw new ApiError({
      status: 0,
      code: "BACKEND_UNREACHABLE",
      message:
        networkErr instanceof Error
          ? `Cannot reach ReleaseGuard API (${API_BASE_URL}): ${networkErr.message}`
          : `Cannot reach ReleaseGuard API (${API_BASE_URL})`,
    });
  }

  const text = await res.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = { raw: text };
    }
  }

  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/api/v1/auth/login")) {
      setStoredAuthToken(null);
    }
    if (parsed && typeof parsed === "object" && "error" in parsed) {
      const errObj = (parsed as { error: Record<string, unknown> }).error;
      throw new ApiError({
        status: res.status,
        code: String(errObj?.code || `HTTP_${res.status}`),
        message: String(errObj?.message || res.statusText || "Request failed"),
        details: (errObj?.details as Record<string, unknown>) ?? null,
        requestId: errObj?.request_id ? String(errObj.request_id) : null,
      });
    }
    if (parsed && typeof parsed === "object" && "detail" in parsed) {
      const detail = (parsed as { detail: unknown }).detail;
      throw new ApiError({
        status: res.status,
        code: `HTTP_${res.status}`,
        message: typeof detail === "string" ? detail : JSON.stringify(detail),
      });
    }
    throw new ApiError({
      status: res.status,
      code: `HTTP_${res.status}`,
      message: res.statusText || `HTTP ${res.status}`,
    });
  }

  return { status: res.status, data: parsed as T };
}

function normalizeList<T>(payload: PaginatedResponse<T> | T[]): PaginatedResponse<T> {
  if (Array.isArray(payload)) {
    return { items: payload, total: payload.length, limit: payload.length, offset: 0 };
  }
  return {
    items: Array.isArray(payload?.items) ? payload.items : [],
    total: typeof payload?.total === "number" ? payload.total : 0,
    limit: typeof payload?.limit === "number" ? payload.limit : 20,
    offset: typeof payload?.offset === "number" ? payload.offset : 0,
  };
}

function normalizeMutation<T>(status: number, payload: unknown): MutationOutcome<T> {
  if (payload && typeof payload === "object" && "execution_status" in payload) {
    return payload as MutationOutcome<T>;
  }
  if (status === 202 && payload && typeof payload === "object" && "approval" in payload) {
    return {
      execution_status: "approval_required",
      approval: (payload as { approval: Approval }).approval,
    };
  }
  return {
    execution_status: "executed",
    data: payload as T,
  };
}

// 1. System & Dashboard
export async function getSystemHealth(): Promise<SystemHealth> {
  const { data } = await requestJson<SystemHealth>("/health");
  return data;
}

export async function getDashboardSummary(): Promise<DashboardSummary> {
  const { data } = await requestJson<DashboardSummary>("/api/v1/dashboard/summary");
  return data;
}

// 2. Settings & Integrations
export async function getWorkspaceSettings(): Promise<WorkspaceSettings> {
  const { data } = await requestJson<WorkspaceSettings>("/api/v1/settings");
  return data;
}

export async function updateWorkspaceSettings(
  patch: Partial<
    Pick<
      WorkspaceSettings,
      | "category_b_requires_confirmation"
      | "approval_ttl_seconds"
      | "gemini_model"
      | "default_guardrails"
      | "health_thresholds"
    >
  >
): Promise<WorkspaceSettings> {
  const { data } = await requestJson<WorkspaceSettings>("/api/v1/settings", {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  return data;
}

export async function listIntegrations(): Promise<IntegrationConnection[]> {
  const { data } = await requestJson<IntegrationConnection[] | PaginatedResponse<IntegrationConnection>>(
    "/api/v1/integrations"
  );
  return Array.isArray(data) ? data : data.items ?? [];
}

export async function startGithubOAuth(includePrivate = false): Promise<{
  authorize_url: string;
  state: string;
}> {
  const { data } = await requestJson<{ authorize_url: string; state: string }>(
    `/api/v1/integrations/github/oauth/start${buildQuery({ include_private: includePrivate })}`
  );
  return data;
}

export async function completeGithubOAuthCallback(body: {
  code: string;
  state: string;
}): Promise<IntegrationConnection> {
  const { data } = await requestJson<IntegrationConnection>(
    "/api/v1/integrations/github/oauth/callback",
    {
      method: "POST",
      body: JSON.stringify(body),
    }
  );
  return data;
}

export async function connectGithubPat(body: {
  token: string;
  account_label?: string;
}): Promise<IntegrationConnection> {
  const { data } = await requestJson<IntegrationConnection>("/api/v1/integrations/github/pat", {
    method: "POST",
    body: JSON.stringify(body),
  });
  return data;
}

export async function connectFlagsmith(body: {
  org_token: string;
  project_id: number;
  environment_keys: Record<string, string>;
}): Promise<IntegrationConnection> {
  const { data } = await requestJson<IntegrationConnection>("/api/v1/integrations/flagsmith", {
    method: "POST",
    body: JSON.stringify(body),
  });
  return data;
}

export async function probeFlagsmithCapability(body?: {
  environment_key?: string;
}): Promise<{
  analytics_mode: string;
  probed_at?: string;
  details?: Record<string, unknown>;
  message?: string;
}> {
  const { data } = await requestJson<{
    analytics_mode: string;
    probed_at?: string;
    details?: Record<string, unknown>;
    message?: string;
  }>("/api/v1/integrations/flagsmith/probe", {
    method: "POST",
    body: JSON.stringify(body ?? {}),
  });
  return data;
}

export async function disconnectIntegration(provider: string): Promise<void> {
  await requestJson<unknown>(`/api/v1/integrations/${encodeURIComponent(provider)}`, {
    method: "DELETE",
  });
}

// 3. Repositories & Pull Requests
export async function listRepositories(params?: {
  limit?: number;
  offset?: number;
}): Promise<PaginatedResponse<Repository>> {
  const { data } = await requestJson<PaginatedResponse<Repository> | Repository[]>(
    `/api/v1/repositories${buildQuery(params)}`
  );
  return normalizeList(data);
}

export async function connectRepository(body: {
  owner: string;
  name: string;
  flagsmith_project_id?: number | null;
}): Promise<Repository> {
  const { data } = await requestJson<Repository>("/api/v1/repositories", {
    method: "POST",
    body: JSON.stringify(body),
  });
  return data;
}

export async function syncRepositories(): Promise<{
  synced_count?: number;
  repositories?: Repository[];
  items?: Repository[];
}> {
  const { data } = await requestJson<{
    synced_count?: number;
    repositories?: Repository[];
    items?: Repository[];
  }>("/api/v1/repositories/sync", { method: "POST" });
  return data;
}

export async function listRepositoryBranches(repoId: string): Promise<BranchInfo[]> {
  const { data } = await requestJson<BranchInfo[] | { branches?: BranchInfo[]; items?: BranchInfo[] }>(
    `/api/v1/repositories/${repoId}/branches`
  );
  if (Array.isArray(data)) return data;
  return data.branches ?? data.items ?? [];
}

export async function scanRepositoryFlags(repoId: string): Promise<RepoScanResult> {
  const { data } = await requestJson<RepoScanResult>(
    `/api/v1/repositories/${repoId}/scan-flags`,
    { method: "POST" }
  );
  return data;
}

export async function listPullRequests(params?: {
  repository_id?: string;
  state?: string;
  risk_level?: string;
  limit?: number;
  offset?: number;
}): Promise<PaginatedResponse<PullRequest>> {
  const { data } = await requestJson<PaginatedResponse<PullRequest> | PullRequest[]>(
    `/api/v1/pull-requests${buildQuery(params)}`
  );
  return normalizeList(data);
}

export async function syncRepoPullRequests(repoId: string): Promise<{
  synced_count?: number;
  items?: PullRequest[];
}> {
  const { data } = await requestJson<{ synced_count?: number; items?: PullRequest[] }>(
    `/api/v1/repositories/${repoId}/pull-requests/sync`,
    { method: "POST" }
  );
  return data;
}

export async function getPullRequest(prId: string): Promise<PullRequest> {
  const { data } = await requestJson<PullRequest>(`/api/v1/pull-requests/${prId}`);
  return data;
}

export async function analyzePullRequest(prId: string): Promise<PullRequest> {
  const { data } = await requestJson<
    PullRequest | { pull_request: PullRequest; static_signals?: string[] }
  >(`/api/v1/pull-requests/${prId}/analyze`, {
    method: "POST",
  });
  if (data && typeof data === "object" && "pull_request" in data) {
    return data.pull_request;
  }
  return data;
}

export async function generatePrPatch(
  prId: string,
  body: {
    flag_key: string;
    flag_type?: "STANDARD" | "MULTIVARIATE";
    target_files?: string[];
  }
): Promise<CodeChange> {
  const { data } = await requestJson<CodeChange>(`/api/v1/pull-requests/${prId}/generate-patch`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  return data;
}

export async function validateCodeChange(changeId: string): Promise<CodeChange> {
  const { data } = await requestJson<CodeChange>(`/api/v1/code-changes/${changeId}/validate`, {
    method: "POST",
  });
  return data;
}

export async function createDraftPrFromCodeChange(
  changeId: string,
  body?: { base_branch?: string; pr_title?: string; pr_body?: string }
): Promise<MutationOutcome<CodeChange>> {
  const { status, data } = await requestJson<unknown>(
    `/api/v1/code-changes/${changeId}/create-draft-pr`,
    {
      method: "POST",
      body: JSON.stringify(body ?? {}),
    }
  );
  return normalizeMutation<CodeChange>(status, data);
}

// 4. Agent Workspace
export async function listAgentSessions(): Promise<AgentSession[]> {
  const { data } = await requestJson<AgentSession[] | PaginatedResponse<AgentSession>>(
    "/api/v1/agent/sessions"
  );
  return Array.isArray(data) ? data : data.items ?? [];
}

export async function createAgentSession(body: {
  title?: string;
  context_scope?: {
    repository_id?: string | null;
    pull_request_id?: string | null;
    flagsmith_project_id?: number | null;
    environment?: string | null;
  };
}): Promise<AgentSession> {
  const { data } = await requestJson<AgentSession>("/api/v1/agent/sessions", {
    method: "POST",
    body: JSON.stringify(body),
  });
  return data;
}

export async function getAgentSession(sessionId: string): Promise<AgentSession> {
  const { data } = await requestJson<AgentSession>(`/api/v1/agent/sessions/${sessionId}`);
  return data;
}

export async function sendAgentMessage(
  sessionId: string,
  body: {
    content: string;
    specialist_hint?: AgentSpecialist;
  }
): Promise<{ task_id: string; session_id: string; stream_url: string }> {
  const { data } = await requestJson<{
    task_id: string;
    session_id: string;
    stream_url: string;
  }>(`/api/v1/agent/sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  return data;
}

export async function getAgentTask(taskId: string, afterSeq?: number): Promise<AgentTask> {
  const { data } = await requestJson<AgentTask>(
    `/api/v1/agent/tasks/${taskId}${buildQuery({ after_seq: afterSeq })}`
  );
  return data;
}

export async function cancelAgentTask(taskId: string): Promise<AgentTask> {
  const { data } = await requestJson<AgentTask>(`/api/v1/agent/tasks/${taskId}/cancel`, {
    method: "POST",
  });
  return data;
}

// 5. Feature Flags & Releases
export async function listFeatureFlags(params?: {
  project_id?: number;
  environment?: string;
  search?: string;
  health_issue?: string;
  limit?: number;
  offset?: number;
}): Promise<PaginatedResponse<FeatureFlag>> {
  const { data } = await requestJson<PaginatedResponse<FeatureFlag> | FeatureFlag[]>(
    `/api/v1/feature-flags${buildQuery(params)}`
  );
  return normalizeList(data);
}

export async function syncFeatureFlags(): Promise<{
  synced_count?: number;
  items?: FeatureFlag[];
}> {
  const { data } = await requestJson<{ synced_count?: number; items?: FeatureFlag[] }>(
    "/api/v1/feature-flags/sync",
    { method: "POST" }
  );
  return data;
}

export async function createFeatureFlag(body: {
  project_id?: number;
  key: string;
  description?: string;
  flag_type: "STANDARD" | "MULTIVARIATE";
  initial_value?: string;
  mv_options?: Array<{ value: string; default_percentage_allocation: number }>;
}): Promise<MutationOutcome<FeatureFlag>> {
  const { status, data } = await requestJson<unknown>("/api/v1/feature-flags", {
    method: "POST",
    body: JSON.stringify(body),
  });
  return normalizeMutation<FeatureFlag>(status, data);
}

export async function runFlagNlCommand(body: {
  command: string;
  project_id?: number;
  environment?: string;
}): Promise<MutationOutcome<Record<string, unknown>>> {
  const { status, data } = await requestJson<unknown>("/api/v1/feature-flags/nl-command", {
    method: "POST",
    body: JSON.stringify({
      command: body.command,
      project_id: body.project_id,
      environment_name: body.environment,
    }),
  });
  return normalizeMutation(status, data);
}

export async function toggleFeatureFlag(
  flagId: string,
  body: { environment_name: string; enabled: boolean; reason?: string }
): Promise<MutationOutcome<FeatureFlag>> {
  const { status, data } = await requestJson<unknown>(
    `/api/v1/feature-flags/${flagId}/toggle`,
    {
      method: "POST",
      body: JSON.stringify(body),
    }
  );
  return normalizeMutation<FeatureFlag>(status, data);
}

export async function rolloutFeatureFlag(
  flagId: string,
  body: {
    environment_name: string;
    target_percentage: RolloutStage;
    strategy?: "percentage_split_segment" | "multivariate";
    variation_key?: string;
    reason?: string;
  }
): Promise<MutationOutcome<FeatureFlag>> {
  const { status, data } = await requestJson<unknown>(
    `/api/v1/feature-flags/${flagId}/rollout`,
    {
      method: "POST",
      body: JSON.stringify(body),
    }
  );
  return normalizeMutation<FeatureFlag>(status, data);
}

export async function rollbackFeatureFlag(
  flagId: string,
  body: { environment_name: string; reason: string }
): Promise<MutationOutcome<FeatureFlag>> {
  const { status, data } = await requestJson<unknown>(
    `/api/v1/feature-flags/${flagId}/rollback`,
    {
      method: "POST",
      body: JSON.stringify(body),
    }
  );
  return normalizeMutation<FeatureFlag>(status, data);
}

export async function getReleasesOverview(): Promise<ReleasesOverview> {
  const { data } = await requestJson<ReleasesOverview>("/api/v1/releases/overview");
  return data;
}

export async function investigateReleaseIncident(body: {
  flag_key?: string;
  environment_name: string;
  window_minutes?: number;
}): Promise<IncidentInvestigationResult> {
  const { data } = await requestJson<IncidentInvestigationResult>("/api/v1/releases/investigate", {
    method: "POST",
    body: JSON.stringify(body),
  });
  return data;
}

function normalizeHealthFlagItems(rawList: unknown[]): FeatureFlag[] {
  return rawList.map((raw) => {
    const obj = raw as Record<string, unknown>;
    if ("id" in obj && typeof obj.id === "string") {
      return obj as unknown as FeatureFlag;
    }
    const flagId = String(obj.flag_id ?? "");
    return {
      id: flagId,
      flagsmith_feature_id: null,
      flagsmith_project_id: 0,
      repository_id: null,
      key: String(obj.key ?? ""),
      description: null,
      flag_type: String(obj.flag_type ?? "STANDARD"),
      is_archived: false,
      env_states: {},
      mv_options: [],
      code_references: [],
      health_score: Number(obj.health_score ?? 100),
      health_issues: Array.isArray(obj.health_issues)
        ? (obj.health_issues as FeatureFlag["health_issues"])
        : [],
      last_synced_at: null,
      last_health_scan_at: (obj.last_health_scan_at as string | null) ?? null,
      flagsmith_created_at: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
  });
}

// 6. Flag Health
export async function getFlagHealth(params?: {
  issue_type?: string;
}): Promise<FlagHealthResponse> {
  const { data } = await requestJson<Record<string, unknown> | unknown[]>(
    `/api/v1/flag-health${buildQuery(params)}`
  );
  if (Array.isArray(data)) {
    const items = normalizeHealthFlagItems(data);
    const avg =
      items.length > 0
        ? Math.round(items.reduce((s, f) => s + f.health_score, 0) / items.length)
        : 100;
    return { items, average_health_score: avg, total_flags: items.length };
  }
  const rawItems = Array.isArray(data.items)
    ? data.items
    : Array.isArray(data.flags)
      ? data.flags
      : [];
  const items = normalizeHealthFlagItems(rawItems);
  return {
    items,
    average_health_score:
      typeof data.average_health_score === "number"
        ? data.average_health_score
        : undefined,
    total_flags:
      typeof data.total_flags === "number"
        ? data.total_flags
        : typeof data.flags_scanned === "number"
          ? data.flags_scanned
          : items.length,
    unhealthy_count:
      typeof data.unhealthy_count === "number"
        ? data.unhealthy_count
        : typeof data.unhealthy_flags === "number"
          ? data.unhealthy_flags
          : undefined,
    detector_counts:
      (data.detector_counts as Record<string, number> | undefined) ??
      (data.issues_by_type as Record<string, number> | undefined),
  };
}

export async function scanFlagHealth(body?: {
  project_id?: number;
  repository_id?: string;
}): Promise<FlagHealthResponse & { scanned_count?: number }> {
  const { data } = await requestJson<Record<string, unknown>>(
    "/api/v1/flag-health/scan",
    {
      method: "POST",
      body: JSON.stringify(body ?? {}),
    }
  );
  const rawItems = Array.isArray(data.items)
    ? data.items
    : Array.isArray(data.flags)
      ? data.flags
      : [];
  const items = normalizeHealthFlagItems(rawItems);
  return {
    items,
    average_health_score:
      typeof data.average_health_score === "number"
        ? data.average_health_score
        : undefined,
    total_flags:
      typeof data.flags_scanned === "number" ? data.flags_scanned : items.length,
    scanned_count:
      typeof data.flags_scanned === "number"
        ? data.flags_scanned
        : typeof data.scanned_count === "number"
          ? data.scanned_count
          : items.length,
    unhealthy_count:
      typeof data.unhealthy_flags === "number" ? data.unhealthy_flags : undefined,
    detector_counts:
      (data.issues_by_type as Record<string, number> | undefined) ??
      (data.detector_counts as Record<string, number> | undefined),
  };
}

export async function generateCleanupPr(
  flagId: string,
  body: {
    repository_id: string;
    keep_branch?: "enabled" | "disabled";
    open_draft_pr?: boolean;
  }
): Promise<MutationOutcome<CodeChange>> {
  const { status, data } = await requestJson<unknown>(
    `/api/v1/flag-health/${flagId}/cleanup-pr`,
    {
      method: "POST",
      body: JSON.stringify(body),
    }
  );
  return normalizeMutation<CodeChange>(status, data);
}

export async function requestFlagDelete(
  flagId: string,
  body?: { action?: "archive" | "delete"; reason?: string }
): Promise<MutationOutcome<Record<string, unknown>>> {
  const { status, data } = await requestJson<unknown>(
    `/api/v1/flag-health/${flagId}/request-delete`,
    {
      method: "POST",
      body: JSON.stringify(body ?? { action: "archive", reason: "Requested via Flag Health Monitor" }),
    }
  );
  return normalizeMutation(status, data);
}

// 7. Experiments & Events
export async function listExperiments(): Promise<PaginatedResponse<Experiment>> {
  const { data } = await requestJson<PaginatedResponse<Experiment> | Experiment[]>(
    "/api/v1/experiments"
  );
  return normalizeList(data);
}

export async function planExperiment(body: {
  prompt: string;
  feature_flag_id?: string;
  environment_name?: string;
}): Promise<Experiment> {
  const { data } = await requestJson<Experiment>("/api/v1/experiments/plan", {
    method: "POST",
    body: JSON.stringify(body),
  });
  return data;
}

export async function activateExperiment(
  expId: string,
  body: { environment_name: string }
): Promise<MutationOutcome<Experiment>> {
  const { status, data } = await requestJson<unknown>(`/api/v1/experiments/${expId}/activate`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  return normalizeMutation<Experiment>(status, data);
}

export async function stopExperiment(
  expId: string,
  body?: { reason?: string; outcome?: string }
): Promise<MutationOutcome<Experiment>> {
  const { status, data } = await requestJson<unknown>(`/api/v1/experiments/${expId}/stop`, {
    method: "POST",
    body: JSON.stringify(body ?? {}),
  });
  return normalizeMutation<Experiment>(status, data);
}

export async function getExperimentAnalysis(expId: string): Promise<ExperimentAnalysis> {
  const { data } = await requestJson<ExperimentAnalysis>(`/api/v1/experiments/${expId}/analysis`);
  return data;
}

export async function getExperimentRecommendation(
  expId: string
): Promise<ExperimentRecommendation> {
  const { data } = await requestJson<ExperimentRecommendation>(
    `/api/v1/experiments/${expId}/recommendation`
  );
  return data;
}

export async function getEventsSummary(params?: {
  flag_key?: string;
  environment_name?: string;
  experiment_id?: string;
  window_minutes?: number;
}): Promise<EventsSummaryResponse> {
  const { data } = await requestJson<EventsSummaryResponse>(
    `/api/v1/events/summary${buildQuery(params)}`
  );
  return data;
}

// 8. Approvals, Rollout History & Audit Logs
export async function listApprovals(params?: {
  status?: string;
  policy_category?: string;
  environment_name?: string;
  limit?: number;
  offset?: number;
}): Promise<PaginatedResponse<Approval>> {
  const { data } = await requestJson<PaginatedResponse<Approval> | Approval[]>(
    `/api/v1/approvals${buildQuery(params)}`
  );
  return normalizeList(data);
}

export async function getApproval(approvalId: string): Promise<Approval> {
  const { data } = await requestJson<Approval>(`/api/v1/approvals/${approvalId}`);
  return data;
}

export async function approveAction(
  approvalId: string,
  body?: { comment?: string }
): Promise<Approval> {
  const { data } = await requestJson<Approval | { approval: Approval }>(
    `/api/v1/approvals/${approvalId}/approve`,
    {
      method: "POST",
      body: JSON.stringify(body ?? {}),
    }
  );
  return data && typeof data === "object" && "approval" in data ? data.approval : data;
}

export async function rejectAction(
  approvalId: string,
  body: { comment: string }
): Promise<Approval> {
  const { data } = await requestJson<Approval | { approval: Approval }>(
    `/api/v1/approvals/${approvalId}/reject`,
    {
      method: "POST",
      body: JSON.stringify(body),
    }
  );
  return data && typeof data === "object" && "approval" in data ? data.approval : data;
}

export async function listAuditLogs(params?: {
  policy_category?: string;
  event_action?: string;
  resource_type?: string;
  status?: string;
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}): Promise<PaginatedResponse<AuditLogEntry>> {
  const { data } = await requestJson<PaginatedResponse<AuditLogEntry> | AuditLogEntry[]>(
    `/api/v1/audit-logs${buildQuery(params)}`
  );
  return normalizeList(data);
}

export function getAuditLogsExportUrl(params: {
  format: "json" | "csv";
  policy_category?: string;
  event_action?: string;
  resource_type?: string;
  status?: string;
  from?: string;
  to?: string;
}): string {
  const tok = getStoredAuthToken();
  return `${API_BASE_URL}/api/v1/audit-logs/export${buildQuery({
    ...params,
    token: tok || undefined,
  })}`;
}

export async function getAuthStatus(): Promise<AuthStatus> {
  const { data } = await requestJson<AuthStatus>("/api/v1/auth/status");
  return data;
}

export async function loginWithPassword(payload: {
  email: string;
  password: string;
}): Promise<AuthTokenResponse> {
  const { data } = await requestJson<AuthTokenResponse>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  setStoredAuthToken(data.access_token);
  return data;
}

export async function logoutSession(): Promise<void> {
  try {
    await requestJson("/api/v1/auth/logout", { method: "POST" });
  } finally {
    setStoredAuthToken(null);
  }
}

export async function startGithubAuthLogin(): Promise<{
  authorize_url: string;
  state: string;
}> {
  const { data } = await requestJson<{ authorize_url: string; state: string }>(
    "/api/v1/auth/github/start"
  );
  return data;
}

export async function completeGithubAuthCallback(payload: {
  code: string;
  state: string;
}): Promise<AuthTokenResponse> {
  const { data } = await requestJson<AuthTokenResponse>("/api/v1/auth/github/callback", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  setStoredAuthToken(data.access_token);
  return data;
}

export async function listWorkspaceUsers(): Promise<AuthUser[]> {
  const { data } = await requestJson<AuthUser[]>("/api/v1/auth/users");
  return data;
}

export async function createWorkspaceUser(payload: {
  email: string;
  display_name: string;
  password: string;
  role: string;
}): Promise<AuthUser> {
  const { data } = await requestJson<AuthUser>("/api/v1/auth/users", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return data;
}

export async function listRolloutHistory(params?: {
  feature_flag_id?: string;
  environment_name?: string;
  limit?: number;
  offset?: number;
}): Promise<PaginatedResponse<RolloutHistoryEntry>> {
  const { data } = await requestJson<
    PaginatedResponse<RolloutHistoryEntry> | RolloutHistoryEntry[]
  >(`/api/v1/rollout-history${buildQuery(params)}`);
  return normalizeList(data);
}

export interface DashboardQueryFilters {
  [key: string]: string | number | boolean | null | undefined;
  range?: "24h" | "7d" | "30d" | "90d" | "custom";
  start_date?: string;
  end_date?: string;
  environment?: string;
  repository_id?: string;
  flag_key?: string;
  experiment_id?: string;
  event_source?: "all" | "simulator_generated" | "application_telemetry";
}

export async function getDashboardOverview(
  params?: DashboardQueryFilters
): Promise<import("@/types/releaseguard").DashboardOverview> {
  const { data } = await requestJson<import("@/types/releaseguard").DashboardOverview>(
    `/api/v1/dashboard/overview${buildQuery(params)}`
  );
  return data;
}

export async function getDashboardMetrics(
  params?: DashboardQueryFilters
): Promise<import("@/types/releaseguard").DashboardMetrics> {
  const { data } = await requestJson<import("@/types/releaseguard").DashboardMetrics>(
    `/api/v1/dashboard/metrics${buildQuery(params)}`
  );
  return data;
}

export async function getDashboardCharts(
  params?: DashboardQueryFilters
): Promise<import("@/types/releaseguard").DashboardCharts> {
  const { data } = await requestJson<import("@/types/releaseguard").DashboardCharts>(
    `/api/v1/dashboard/charts${buildQuery(params)}`
  );
  return data;
}

export async function getDashboardActivity(params?: {
  limit?: number;
  environment?: string;
}): Promise<import("@/types/releaseguard").ActivityFeedItem[]> {
  const { data } = await requestJson<import("@/types/releaseguard").ActivityFeedItem[]>(
    `/api/v1/dashboard/activity${buildQuery(params)}`
  );
  return Array.isArray(data) ? data : [];
}

export async function getDashboardPendingActions(params?: {
  environment?: string;
}): Promise<import("@/types/releaseguard").PendingActionItem[]> {
  const { data } = await requestJson<import("@/types/releaseguard").PendingActionItem[]>(
    `/api/v1/dashboard/pending-actions${buildQuery(params)}`
  );
  return Array.isArray(data) ? data : [];
}

// Phase 3: Metrics & Analytics
export async function getMetricsOverview(params?: DashboardQueryFilters): Promise<Record<string, unknown>> {
  const { data } = await requestJson<Record<string, unknown>>(`/api/v1/metrics/overview${buildQuery(params)}`);
  return data;
}

export async function getReleaseMetrics(params?: DashboardQueryFilters): Promise<Record<string, unknown>> {
  const { data } = await requestJson<Record<string, unknown>>(`/api/v1/metrics/releases${buildQuery(params)}`);
  return data;
}

export async function getPrMetrics(params?: DashboardQueryFilters): Promise<Record<string, unknown>> {
  const { data } = await requestJson<Record<string, unknown>>(`/api/v1/metrics/pull-requests${buildQuery(params)}`);
  return data;
}

export async function getFeatureFlagMetrics(params?: DashboardQueryFilters): Promise<Record<string, unknown>> {
  const { data } = await requestJson<Record<string, unknown>>(`/api/v1/metrics/feature-flags${buildQuery(params)}`);
  return data;
}

export async function getExperimentMetrics(params?: DashboardQueryFilters): Promise<Record<string, unknown>> {
  const { data } = await requestJson<Record<string, unknown>>(`/api/v1/metrics/experiments${buildQuery(params)}`);
  return data;
}

export async function getAgentMetrics(params?: DashboardQueryFilters): Promise<Record<string, unknown>> {
  const { data } = await requestJson<Record<string, unknown>>(`/api/v1/metrics/agents${buildQuery(params)}`);
  return data;
}

export async function getEventMetrics(params?: DashboardQueryFilters): Promise<Record<string, unknown>> {
  const { data } = await requestJson<Record<string, unknown>>(`/api/v1/metrics/events${buildQuery(params)}`);
  return data;
}

export function getMetricsExportUrl(
  section: "releases" | "pull_requests" | "feature_flags" | "experiments" | "agents" | "events",
  params?: DashboardQueryFilters
): string {
  const tok = getStoredAuthToken();
  return `${API_BASE_URL}/api/v1/metrics/export${buildQuery({ ...params, section, token: tok || undefined })}`;
}

// Phase 4: Persistent Scheduling
export interface SchedulePayloadInput {
  name: string;
  description?: string | null;
  job_type: import("@/types/releaseguard").ScheduleJobType;
  frequency: import("@/types/releaseguard").ScheduleFrequency;
  cron_expression?: string | null;
  interval_minutes?: number | null;
  timezone: string;
  start_at?: string | null;
  end_at?: string | null;
  configuration?: Record<string, unknown>;
  retry_policy?: import("@/types/releaseguard").ScheduleRetryPolicy;
  notification_preference?: import("@/types/releaseguard").ScheduleNotificationPref;
  enabled?: boolean;
}

export async function getSchedules(params?: {
  job_type?: string;
  enabled?: boolean;
  environment?: string;
  last_status?: string;
  limit?: number;
  offset?: number;
}): Promise<PaginatedResponse<import("@/types/releaseguard").ScheduledJobItem>> {
  const { data } = await requestJson<PaginatedResponse<import("@/types/releaseguard").ScheduledJobItem>>(
    `/api/v1/schedules${buildQuery(params)}`
  );
  return data;
}

export async function previewSchedule(body: {
  frequency?: string;
  cron_expression?: string | null;
  interval_minutes?: number | null;
  timezone: string;
  start_at?: string | null;
  end_at?: string | null;
  nl_prompt?: string | null;
}): Promise<import("@/types/releaseguard").SchedulePreviewResponse> {
  const { data } = await requestJson<import("@/types/releaseguard").SchedulePreviewResponse>(
    "/api/v1/schedules/preview",
    {
      method: "POST",
      body: JSON.stringify(body),
    }
  );
  return data;
}

export async function createSchedule(
  body: SchedulePayloadInput
): Promise<import("@/types/releaseguard").ScheduledJobItem> {
  const { data } = await requestJson<import("@/types/releaseguard").ScheduledJobItem>("/api/v1/schedules", {
    method: "POST",
    body: JSON.stringify(body),
  });
  return data;
}

export async function updateSchedule(
  id: string,
  body: Partial<SchedulePayloadInput>
): Promise<import("@/types/releaseguard").ScheduledJobItem> {
  const { data } = await requestJson<import("@/types/releaseguard").ScheduledJobItem>(`/api/v1/schedules/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
  return data;
}

export async function deleteSchedule(id: string): Promise<{ deleted: boolean; history_preserved: boolean }> {
  const { data } = await requestJson<{ deleted: boolean; history_preserved: boolean }>(
    `/api/v1/schedules/${id}`,
    { method: "DELETE" }
  );
  return data;
}

export async function pauseSchedule(id: string): Promise<import("@/types/releaseguard").ScheduledJobItem> {
  const { data } = await requestJson<import("@/types/releaseguard").ScheduledJobItem>(
    `/api/v1/schedules/${id}/pause`,
    { method: "POST" }
  );
  return data;
}

export async function resumeSchedule(id: string): Promise<import("@/types/releaseguard").ScheduledJobItem> {
  const { data } = await requestJson<import("@/types/releaseguard").ScheduledJobItem>(
    `/api/v1/schedules/${id}/resume`,
    { method: "POST" }
  );
  return data;
}

export async function runScheduleNow(id: string): Promise<import("@/types/releaseguard").JobExecutionItem> {
  const { data } = await requestJson<import("@/types/releaseguard").JobExecutionItem>(
    `/api/v1/schedules/${id}/run`,
    { method: "POST" }
  );
  return data;
}

export async function getScheduleExecutions(
  id: string,
  params?: { limit?: number; offset?: number }
): Promise<PaginatedResponse<import("@/types/releaseguard").JobExecutionItem>> {
  const { data } = await requestJson<PaginatedResponse<import("@/types/releaseguard").JobExecutionItem>>(
    `/api/v1/schedules/${id}/executions${buildQuery(params)}`
  );
  return data;
}

export async function getAllJobExecutions(params?: {
  scheduled_job_id?: string;
  job_type?: string;
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<PaginatedResponse<import("@/types/releaseguard").JobExecutionItem>> {
  const { data } = await requestJson<PaginatedResponse<import("@/types/releaseguard").JobExecutionItem>>(
    `/api/v1/schedules/executions${buildQuery(params)}`
  );
  return data;
}

// Phase 5: Notifications, Workspace SSE & Deep System Health
export async function listNotifications(params?: {
  category?: string;
  severity?: string;
  unread_only?: boolean;
  unresolved_only?: boolean;
  limit?: number;
  offset?: number;
}): Promise<PaginatedResponse<import("@/types/releaseguard").NotificationItem>> {
  const { data } = await requestJson<PaginatedResponse<import("@/types/releaseguard").NotificationItem>>(
    `/api/v1/notifications${buildQuery(params)}`
  );
  return data;
}

export async function getUnreadNotificationCount(): Promise<{
  unread_count: number;
  critical_unread_count: number;
}> {
  const { data } = await requestJson<{ unread_count: number; critical_unread_count: number }>(
    "/api/v1/notifications/unread-count"
  );
  return data;
}

export async function markNotificationRead(
  id: string
): Promise<import("@/types/releaseguard").NotificationItem> {
  const { data } = await requestJson<import("@/types/releaseguard").NotificationItem>(
    `/api/v1/notifications/${id}/read`,
    { method: "PATCH" }
  );
  return data;
}

export async function resolveNotification(
  id: string
): Promise<import("@/types/releaseguard").NotificationItem> {
  const { data } = await requestJson<import("@/types/releaseguard").NotificationItem>(
    `/api/v1/notifications/${id}/resolve`,
    { method: "PATCH" }
  );
  return data;
}

export async function markAllNotificationsRead(): Promise<{ marked_read: number }> {
  const { data } = await requestJson<{ marked_read: number }>("/api/v1/notifications/read-all", {
    method: "POST",
  });
  return data;
}

export async function clearResolvedNotifications(): Promise<{ cleared_count: number }> {
  const { data } = await requestJson<{ cleared_count: number }>(
    "/api/v1/notifications/clear-resolved",
    { method: "POST" }
  );
  return data;
}

export async function getNotificationPreferences(): Promise<
  import("@/types/releaseguard").NotificationPreference
> {
  const { data } = await requestJson<import("@/types/releaseguard").NotificationPreference>(
    "/api/v1/notifications/preferences"
  );
  return data;
}

export async function updateNotificationPreferences(
  patch: Partial<import("@/types/releaseguard").NotificationPreference>
): Promise<import("@/types/releaseguard").NotificationPreference> {
  const { data } = await requestJson<import("@/types/releaseguard").NotificationPreference>(
    "/api/v1/notifications/preferences",
    {
      method: "PATCH",
      body: JSON.stringify(patch),
    }
  );
  return data;
}

export function getWorkspaceStreamUrl(afterSeq = 0): string {
  const tok = getStoredAuthToken();
  return `${API_BASE_URL}/api/v1/events/stream${buildQuery({
    after_seq: afterSeq > 0 ? afterSeq : undefined,
    token: tok || undefined,
  })}`;
}

export async function getDeepSystemHealth(): Promise<
  import("@/types/releaseguard").DeepSystemHealth
> {
  const { data } = await requestJson<import("@/types/releaseguard").DeepSystemHealth>(
    "/api/v1/system/health"
  );
  return data;
}

export async function getSystemWorkersStatus(): Promise<{
  agent_tasks: Array<Record<string, unknown>>;
  job_executions: Array<Record<string, unknown>>;
}> {
  const { data } = await requestJson<{
    agent_tasks: Array<Record<string, unknown>>;
    job_executions: Array<Record<string, unknown>>;
  }>("/api/v1/system/workers");
  return data;
}

export async function getSystemSchedulerStatus(): Promise<{
  worker: Record<string, unknown>;
  counts: Record<string, number>;
  upcoming: Array<Record<string, unknown>>;
}> {
  const { data } = await requestJson<{
    worker: Record<string, unknown>;
    counts: Record<string, number>;
    upcoming: Array<Record<string, unknown>>;
  }>("/api/v1/system/scheduler");
  return data;
}
