export type PolicyCategory = "A" | "B" | "C" | "DENY" | "FORBIDDEN";

export type RiskLevel = "low" | "medium" | "high" | "critical";

export type AnalyticsMode = "flagsmith_native" | "tracked_event_fallback";

export type ApprovalStatus =
  | "pending"
  | "executing"
  | "executed"
  | "rejected"
  | "expired"
  | "revalidation_failed"
  | "failed"
  | "cancelled";

export type AgentSpecialist =
  | "supervisor"
  | "pr_agent"
  | "flag_agent"
  | "experiment_agent";

export type AgentTaskStatus =
  | "queued"
  | "running"
  | "awaiting_approval"
  | "completed"
  | "failed"
  | "cancelled";

export type ExperimentStatus =
  | "draft"
  | "running"
  | "paused"
  | "completed"
  | "rolled_back";

export type RecommendationStatus =
  | "Continue Monitoring"
  | "Expand Rollout"
  | "Maintain Current Rollout"
  | "Reduce Rollout"
  | "Recommend Rollback";

export type HealthIssueType =
  | "stale"
  | "unused"
  | "always_on"
  | "always_off"
  | "orphaned"
  | "duplicate"
  | "env_drift";

export type RolloutStage = 0 | 10 | 25 | 50 | 75 | 100;

export interface ApiErrorPayload {
  code: string;
  message: string;
  details?: Record<string, unknown> | null;
  request_id?: string | null;
}

export interface ApiErrorEnvelope {
  error: ApiErrorPayload;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface SystemHealth {
  status: string;
  database: string;
  gemini_configured: boolean;
  github_connected: boolean;
  flagsmith_connected: boolean;
  analytics_mode: AnalyticsMode | string;
}

export interface IntegrationConnection {
  id: string;
  provider: "github" | "flagsmith" | string;
  auth_mode: "oauth" | "pat" | "org_token" | string;
  status: "connected" | "disconnected" | "error" | "expired" | string;
  external_account_id?: string | null;
  external_account_name?: string | null;
  scopes: string[];
  token_hint: string;
  metadata_json: Record<string, unknown>;
  last_verified_at?: string | null;
  expires_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface GuardrailThresholds {
  min_exposures: number;
  max_expected_loss: number;
  guardrail_max_error_rate: number;
  soft_error_delta: number;
  hard_error_delta: number;
}

export interface HealthThresholds {
  stale_days: number;
  unused_days: number;
  always_on_days: number;
  always_off_days: number;
}

export interface WorkspaceSettings {
  id?: string;
  category_b_requires_confirmation: boolean;
  approval_ttl_seconds: number;
  gemini_model: string;
  flagsmith_analytics_mode: AnalyticsMode;
  flagsmith_capability_probed_at?: string | null;
  default_guardrails: GuardrailThresholds;
  health_thresholds: HealthThresholds;
  integrations?: IntegrationConnection[];
  updated_at?: string | null;
}

export interface Repository {
  id: string;
  connection_id?: string | null;
  github_repo_id: number;
  owner: string;
  name: string;
  full_name: string;
  default_branch: string;
  is_private: boolean;
  flagsmith_project_id?: number | null;
  language?: string | null;
  last_synced_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface BranchInfo {
  name: string;
  commit_sha?: string;
  sha?: string;
  protected: boolean;
}

export interface FlagCodeReference {
  flag_key: string;
  file_path: string;
  line_number: number;
  snippet: string;
  sdk_method?: string | null;
}

export interface RepoScanResult {
  repository_id: string;
  files_scanned?: number;
  references_found?: number;
  references_by_flag?: Record<string, FlagCodeReference[]>;
  scanned_files?: number;
  matched_flags?: number;
  references?: FlagCodeReference[];
  summary?: string;
}

export interface ConfirmedRisk {
  title: string;
  severity: RiskLevel | string;
  file_path: string;
  line_range?: string | null;
  evidence: string;
  mitigation?: string | null;
}

export interface PotentialRisk {
  title: string;
  severity: RiskLevel | string;
  assumption: string;
  verification_needed: string;
}

export interface FlagOpportunity {
  suggested_flag_key: string;
  flag_type?: "STANDARD" | "MULTIVARIATE" | string;
  target_files: string[];
  rationale: string;
  recommended_rollout?: string | null;
}

export interface FileDiffItem {
  file_path: string;
  action: "create" | "modify" | string;
  before_code: string;
  after_code: string;
  unified_diff: string;
}

export interface GeneratedTestFile {
  file_path: string;
  content: string;
}

export interface ValidationCheckItem {
  name: string;
  status: "passed" | "failed" | "skipped" | string;
  detail?: string | null;
  message?: string | null;
  skipped_reason?: string | null;
}

export interface ValidationReport {
  ast_ok?: boolean;
  path_safety_ok?: boolean;
  overwrite_ok?: boolean;
  ruff_ok?: boolean | null;
  pytest_ok?: boolean | null;
  eslint_ok?: boolean | null;
  checks?: ValidationCheckItem[];
  stdout?: string | null;
  stderr?: string | null;
  skipped_reasons?: Record<string, string>;
}

export interface CodeChange {
  id: string;
  repository_id: string;
  pull_request_id?: string | null;
  feature_flag_id?: string | null;
  agent_task_id?: string | null;
  change_type: "flag_wrap" | "flag_cleanup" | "experiment_instrumentation" | string;
  flag_key: string;
  title: string;
  description?: string | null;
  files_diff: FileDiffItem[];
  generated_tests: GeneratedTestFile[];
  validation_status: "pending" | "running" | "passed" | "failed" | string;
  validation_report: ValidationReport;
  draft_pr_number?: number | null;
  draft_pr_url?: string | null;
  draft_branch_name?: string | null;
  status: "draft" | "validated" | "validation_failed" | "pr_opened" | "discarded" | string;
  created_at: string;
  updated_at: string;
}

export interface PullRequest {
  id: string;
  repository_id: string;
  github_pr_number: number;
  title: string;
  body?: string | null;
  author_login: string;
  head_branch: string;
  base_branch: string;
  head_sha: string;
  state: "open" | "closed" | "merged" | string;
  is_draft: boolean;
  html_url: string;
  analysis_status: "pending" | "analyzing" | "completed" | "failed" | string;
  risk_score?: number | null;
  risk_level?: RiskLevel | null;
  analysis_summary?: string | null;
  confirmed_risks: ConfirmedRisk[];
  potential_risks: PotentialRisk[];
  flag_opportunities: FlagOpportunity[];
  code_changes?: CodeChange[];
  diff_summary?: string | null;
  analyzed_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface FlagEnvironmentState {
  env_key?: string;
  environment_name?: string;
  is_production: boolean;
  use_v2_versioning?: boolean;
  enabled: boolean;
  value?: string | number | boolean | null;
  rollout_percentage?: number | null;
  mv_options?: Array<{
    value: string;
    percentage_allocation: number;
  }>;
  segment_overrides?: Array<Record<string, unknown>>;
  version_uuid?: string | null;
}

export interface MultivariateOption {
  id?: number;
  value: string;
  default_percentage_allocation: number;
}

export interface CodeReference {
  file_path: string;
  line_number: number;
  snippet: string;
}

export interface HealthIssue {
  issue_type: HealthIssueType | string;
  penalty: number;
  summary: string;
  evidence?: Record<string, unknown> | string | null;
}

export interface FeatureFlag {
  id: string;
  flagsmith_feature_id?: number | null;
  flagsmith_project_id: number;
  repository_id?: string | null;
  key: string;
  description?: string | null;
  flag_type: "STANDARD" | "MULTIVARIATE" | string;
  is_archived: boolean;
  env_states: Record<string, FlagEnvironmentState>;
  mv_options: MultivariateOption[];
  code_references: CodeReference[];
  health_score: number;
  health_issues: HealthIssue[];
  last_synced_at?: string | null;
  last_health_scan_at?: string | null;
  flagsmith_created_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface Approval {
  id: string;
  agent_task_id?: string | null;
  requested_by_id?: string | null;
  decided_by_id?: string | null;
  policy_category: "B" | "C" | string;
  action_type: string;
  target_resource_type: string;
  target_resource_id: string;
  environment_name?: string | null;
  title: string;
  rationale?: string | null;
  action_payload: Record<string, unknown>;
  expected_precondition: Record<string, unknown>;
  action_hash: string;
  idempotency_key: string;
  status: ApprovalStatus;
  decision_comment?: string | null;
  execution_result?: Record<string, unknown> | null;
  expires_at: string;
  decided_at?: string | null;
  executed_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface MutationOutcome<T = Record<string, unknown>> {
  execution_status: "executed" | "approval_required" | string;
  data?: T;
  approval?: Approval;
  verification?: Record<string, unknown>;
  task_id?: string;
  stream_url?: string;
}

export interface BayesianVariantMetrics {
  exposures: number;
  conversions: number;
  posterior_alpha: number;
  posterior_beta: number;
  mean_rate: number;
  credible_interval_95: [number, number];
}

export interface ExperimentAnalysis {
  experiment_id: string;
  analytics_mode: AnalyticsMode | string;
  sample_adequacy: {
    control_exposures: number;
    treatment_exposures: number;
    min_required: number;
    is_adequate: boolean;
  };
  control: BayesianVariantMetrics;
  treatment: BayesianVariantMetrics;
  comparison: {
    prob_treatment_beats_control: number;
    absolute_lift_mean: number;
    absolute_lift_cri_95: [number, number];
    relative_lift_mean: number;
    relative_lift_cri_95: [number, number];
    expected_loss_choose_treatment: number;
    expected_loss_choose_control: number;
  };
  guardrails: {
    payment_failed?: {
      control_errors: number;
      control_rate: number;
      treatment_errors: number;
      treatment_rate: number;
      prob_treatment_exceeds_soft_delta: number;
      prob_treatment_exceeds_hard_delta: number;
      prob_treatment_exceeds_max_error_rate?: number;
      breached: boolean;
      soft_breached?: boolean;
    };
    checkout_abandoned?: {
      control_count: number;
      treatment_count: number;
    };
    [key: string]: unknown;
  };
}

export interface RecommendationTrigger {
  metric: string;
  actual_value: number;
  threshold: number;
  operator: string;
  met: boolean;
  description: string;
}

export interface ExperimentRecommendation {
  experiment_id: string;
  recommendation: RecommendationStatus;
  triggers?: Array<string | RecommendationTrigger> | Record<string, unknown>;
  confidence_summary?: string | null;
  preconditions?: string[];
  potential_consequences?: string[];
  narrative?: string | null;
  rationale?: string | null;
}

export interface Experiment {
  id: string;
  feature_flag_id: string;
  flag_key?: string | null;
  name: string;
  hypothesis: string;
  environment_name: string;
  environment_key?: string | null;
  analytics_mode: AnalyticsMode | string;
  flagsmith_experiment_id?: string | null;
  status: ExperimentStatus | string;
  control_variation: string;
  treatment_variation: string;
  traffic_allocation: Record<string, number>;
  primary_metric: string;
  guardrail_metrics: string[];
  guardrail_thresholds: Partial<GuardrailThresholds>;
  latest_bayesian_result?: ExperimentAnalysis | null;
  latest_recommendation?: RecommendationStatus | null;
  recommendation_rationale?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface EventsSummaryBucket {
  bucket_start: string;
  variation_key: string;
  feature_variation_assigned?: number;
  checkout_started?: number;
  checkout_completed?: number;
  checkout_abandoned?: number;
  payment_failed?: number;
  [key: string]: string | number | undefined;
}

export interface EventsSummaryResponse {
  total_events?: number;
  by_variation?: Record<string, Record<string, number>>;
  buckets?: EventsSummaryBucket[];
}

export interface RolloutHistoryEntry {
  id: string;
  feature_flag_id: string;
  flag_key?: string | null;
  approval_id?: string | null;
  actor_id?: string | null;
  environment_name: string;
  action: string;
  previous_state: Record<string, unknown>;
  new_state: Record<string, unknown>;
  verified: boolean;
  verification_details: Record<string, unknown>;
  reason?: string | null;
  created_at: string;
}

export interface AuditLogEntry {
  id: string;
  actor_type: string;
  actor_id?: string | null;
  policy_category: PolicyCategory | string;
  event_action: string;
  resource_type: string;
  resource_id?: string | null;
  environment_name?: string | null;
  approval_id?: string | null;
  action_hash?: string | null;
  status: "success" | "blocked" | "failed" | "pending" | string;
  details: Record<string, unknown>;
  created_at: string;
}

export interface DashboardSummary {
  counts: {
    repositories: number;
    open_prs: number;
    high_risk_prs: number;
    active_flags: number;
    active_experiments: number;
    pending_approvals: number;
    unhealthy_flags: number;
  };
  average_health_score?: number | null;
  integration_status?: {
    github_connected?: boolean;
    flagsmith_connected?: boolean;
    gemini_configured?: boolean;
    analytics_mode?: string;
    [key: string]: unknown;
  };
  recent_rollouts: RolloutHistoryEntry[];
  recent_audits: AuditLogEntry[];
}

export interface ActiveRolloutItem {
  feature_flag_id: string;
  flag_key: string;
  environment_name: string;
  is_production: boolean;
  enabled: boolean;
  current_percentage: number;
  strategy?: string;
  latest_recommendation?: RecommendationStatus | string | null;
  last_updated_at?: string | null;
  verified?: boolean;
}

export interface ReleasesOverview {
  active_rollouts: ActiveRolloutItem[];
  recent_rollouts?: RolloutHistoryEntry[];
  recent_history?: RolloutHistoryEntry[];
  recommendations?: Array<{
    experiment_id?: string;
    flag_key?: string;
    environment_name?: string;
    recommendation: RecommendationStatus | string;
    rationale?: string | null;
    narrative?: string | null;
  }>;
  incident_flags?: Array<{
    flag_key: string;
    environment_name: string;
    error_rate: number;
    severity: string;
    summary: string;
  }>;
  incident_alerts?: Array<Record<string, unknown>>;
}

export interface IncidentInvestigationResult {
  correlation_not_causation: boolean;
  disclaimer?: string;
  causal_disclaimer?: string;
  environment_name?: string | null;
  flag_key?: string | null;
  window_minutes?: number;
  observed_metrics: {
    total_exposures?: number;
    total_payment_failed?: number;
    total_abandoned?: number;
    total_checkout_abandoned?: number;
    overall_error_rate?: number;
    overall_payment_failed_rate?: number;
    by_variation?: Record<
      string,
      {
        exposures: number;
        payment_failed: number;
        checkout_abandoned: number;
        error_rate?: number;
        payment_failed_rate?: number;
      }
    >;
    windows?: Array<{
      window_start: string;
      payment_failed: number;
      checkout_abandoned: number;
      exposures: number;
      error_rate?: number;
      payment_failed_rate?: number;
    }>;
  };
  temporal_correlations: Array<{
    source_type?: string;
    source_id?: string;
    event_type?: string;
    title?: string;
    summary?: string;
    timestamp?: string;
    occurred_at?: string;
    delta_minutes?: number;
    delta_minutes_from_onset?: number;
    minutes_from_spike?: number;
    correlation_score?: number;
    temporal_score?: number;
    correlation_not_causation: boolean;
    details?: Record<string, unknown>;
  }>;
  confounding_checks: Array<{
    check_name: string;
    status?: string;
    finding?: string;
    summary?: string;
    likely_confounder?: boolean;
    confounder_detected?: boolean;
  }>;
  recommended_next_steps: string[];
}

export interface FlagHealthResponse {
  average_health_score?: number;
  total_flags?: number;
  flags_scanned?: number;
  unhealthy_count?: number;
  unhealthy_flags?: number;
  detector_counts?: Record<string, number>;
  issues_by_type?: Record<string, number>;
  items: FeatureFlag[];
}

export interface AgentMessage {
  role: "user" | "assistant" | "system" | string;
  content: string;
  timestamp?: string;
  task_id?: string;
}

export type AgentEventType =
  | "task.started"
  | "agent.thinking"
  | "agent.tool_proposed"
  | "policy.evaluated"
  | "approval.required"
  | "tool.executed"
  | "agent.completed"
  | "task.failed"
  | "heartbeat";

export interface AgentStepEvent {
  seq: number;
  event_type: AgentEventType | string;
  timestamp?: string;
  payload: Record<string, unknown>;
}

export interface AgentTask {
  id: string;
  session_id: string;
  specialist: AgentSpecialist | string;
  user_prompt: string;
  status: AgentTaskStatus;
  steps_log: AgentStepEvent[];
  final_response?: string | null;
  error_message?: string | null;
  gemini_model?: string | null;
  token_usage?: {
    prompt_tokens?: number;
    candidate_tokens?: number;
    total_tokens?: number;
  };
  pending_approvals?: Approval[];
  started_at?: string | null;
  completed_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentSession {
  id: string;
  user_id?: string | null;
  title: string;
  thread_id: string;
  active_specialist: AgentSpecialist | string;
  status: "active" | "paused_approval" | "completed" | "archived" | string;
  context_scope: {
    repository_id?: string | null;
    pull_request_id?: string | null;
    flagsmith_project_id?: number | null;
    environment?: string | null;
  };
  message_history: AgentMessage[];
  tasks?: AgentTask[];
  created_at: string;
  updated_at: string;
}
