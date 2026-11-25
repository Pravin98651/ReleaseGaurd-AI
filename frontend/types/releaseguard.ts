export type PolicyCategory = "A" | "B" | "C" | "DENY" | "FORBIDDEN";

export type UserRole = "admin" | "release_manager" | "engineer" | "viewer";

export interface AuthUser {
  id: string;
  email: string;
  display_name: string;
  role: UserRole | string;
  github_login?: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface AuthStatus {
  require_auth: boolean;
  authenticated: boolean;
  github_oauth_enabled: boolean;
  user?: AuthUser | null;
}

export interface AuthTokenResponse {
  access_token: string;
  token_type: "bearer" | string;
  expires_in_seconds: number;
  user: AuthUser;
}

export interface ChangedFileItem {
  filename: string;
  status: string;
  additions: number;
  deletions: number;
  patch: string;
}

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
  changed_files?: ChangedFileItem[];
  diff_summary?: string | Record<string, unknown> | null;
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

export type TimeRangePreset = "24h" | "7d" | "30d" | "90d" | "custom";

export interface MetricTrend {
  previous_value: number;
  delta: number;
  percent_change?: number | null;
  direction: "up" | "down" | "flat";
}

export interface DashboardMetrics {
  filter: {
    range: TimeRangePreset;
    start_at: string;
    end_at: string;
    environment?: string | null;
    repository_id?: string | null;
    flag_key?: string | null;
    experiment_id?: string | null;
    event_source: "all" | "simulator_generated" | "application_telemetry";
  };
  engineering: {
    connected_repositories: number;
    synchronized_prs: number;
    prs_analyzed: number;
    pending_pr_analyses: number;
    draft_prs_created: number;
    failed_analysis_tasks: number;
    trends?: Record<string, MetricTrend>;
  };
  releases: {
    total_releases: number;
    releases_in_progress: number;
    successful_releases: number;
    failed_releases: number;
    rollbacks_executed: number;
    releases_awaiting_approval: number;
    release_success_rate: number | null;
    lifecycle_total_releases?: number;
    lifecycle_active_releases?: number;
    lifecycle_ready_releases?: number;
    lifecycle_blocked_releases?: number;
    lifecycle_completed_releases?: number;
    lifecycle_avg_readiness_score?: number | null;
    trends?: Record<string, MetricTrend>;
  };
  feature_flags: {
    total_flags: number;
    enabled_flags: number;
    disabled_flags: number;
    gradual_rollout_flags: number;
    flags_with_health_issues: number;
    pending_flag_changes: number;
    average_health_score: number | null;
  };
  experiments: {
    total_experiments: number;
    running_experiments: number;
    completed_experiments: number;
    paused_experiments: number;
    positive_recommendation_experiments: number;
    breached_guardrail_experiments: number;
  };
  approvals: {
    pending_approvals: number;
    approved_operations: number;
    rejected_operations: number;
    expired_approvals: number;
  };
  agents: {
    total_agent_executions: number;
    successful_executions: number;
    failed_executions: number;
    running_executions: number;
    cancelled_executions: number;
    awaiting_approval_executions: number;
    average_execution_duration_seconds: number | null;
    agent_tool_calls: number;
    total_tokens: number | null;
  };
  sources: Record<string, string>;
}

export interface ExperimentPerformancePoint {
  experiment_id: string;
  name: string;
  flag_key?: string | null;
  environment_name: string;
  status: string;
  analytics_mode: string;
  primary_metric: string;
  recommendation?: string | null;
  hypothesis: string;
  traffic_allocation: Record<string, number>;
  control_exposures: number;
  control_conversions: number;
  control_rate: number;
  control_ci_95: number[];
  treatment_exposures: number;
  treatment_conversions: number;
  treatment_rate: number;
  treatment_ci_95: number[];
  absolute_improvement: number;
  relative_improvement: number;
  prob_treatment_beats_control: number;
  expected_loss_treatment: number;
  payment_failed_breached: boolean;
  payment_failed_severity: string;
  control_error_rate: number;
  treatment_error_rate: number;
  sample_adequate: boolean;
  started_at?: string | null;
  ended_at?: string | null;
  simulator_event_count: number;
  telemetry_event_count: number;
}

export interface DashboardCharts {
  filter: DashboardMetrics["filter"];
  release_activity: Array<{
    bucket: string;
    label: string;
    releases_created: number;
    releases_completed: number;
    releases_failed: number;
    rollbacks: number;
  }>;
  experiment_performance: ExperimentPerformancePoint[];
  flag_distribution: Array<{ key: string; name: string; count: number }>;
  agent_activity: Array<{
    bucket: string;
    label: string;
    successful: number;
    failed: number;
    running: number;
    awaiting_approval: number;
    avg_duration_seconds: number | null;
  }>;
  telemetry_activity: Array<{
    bucket: string;
    label: string;
    total_events: number;
    checkout_started: number;
    checkout_completed: number;
    payment_failed: number;
    checkout_abandoned: number;
    simulator_events: number;
    telemetry_events: number;
  }>;
  release_health: Array<{ key: string; name: string; count: number }>;
  telemetry_totals: {
    total_events?: number;
    simulator_events?: number;
    application_telemetry_events?: number;
  };
}

export interface ActivityFeedItem {
  id: string;
  event_type: string;
  title: string;
  description: string;
  timestamp: string;
  resource_type: string;
  resource_id?: string | null;
  resource_label?: string | null;
  environment_name?: string | null;
  actor: string;
  status: string;
  href: string;
}

export interface PendingActionItem {
  id: string;
  category: string;
  severity: "critical" | "high" | "medium" | "info";
  title: string;
  description: string;
  resource_type: string;
  resource_id?: string | null;
  environment_name?: string | null;
  created_at: string;
  action_label: string;
  href: string;
}

export interface DashboardOverview {
  metrics: DashboardMetrics;
  charts: DashboardCharts;
  recent_activity: ActivityFeedItem[];
  pending_actions: PendingActionItem[];
  integration_status: Record<string, unknown>;
  upcoming_schedules: Array<Record<string, unknown>>;
  data_sources: Record<string, unknown>;
}

export type ScheduleJobType =
  | "repo_sync"
  | "pr_analysis"
  | "flag_health_scan"
  | "experiment_monitor"
  | "release_monitor"
  | "ai_maintenance_report"
  | "custom_agent_task";

export type ScheduleFrequency =
  | "once"
  | "interval_minutes"
  | "hourly"
  | "daily"
  | "weekly"
  | "monthly"
  | "cron";

export type JobExecutionStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "skipped"
  | "retrying";

export interface ScheduleRetryPolicy {
  max_retries: number;
  retry_delay_seconds: number;
  timeout_seconds: number;
}

export interface ScheduleNotificationPref {
  on_success: boolean;
  on_failure: boolean;
}

export interface ScheduledJobItem {
  id: string;
  name: string;
  description?: string | null;
  job_type: ScheduleJobType;
  frequency: ScheduleFrequency;
  cron_expression?: string | null;
  interval_minutes?: number | null;
  timezone: string;
  start_at?: string | null;
  end_at?: string | null;
  configuration: Record<string, unknown>;
  retry_policy: ScheduleRetryPolicy;
  notification_preference: ScheduleNotificationPref;
  enabled: boolean;
  owner_id?: string | null;
  last_run_at?: string | null;
  next_run_at?: string | null;
  last_status?: string | null;
  consecutive_failures: number;
  missed_runs_count: number;
  human_description: string;
  next_runs_preview: Array<{ utc: string; local: string }>;
  created_at: string;
  updated_at: string;
}

export interface JobExecutionItem {
  id: string;
  scheduled_job_id?: string | null;
  schedule_name: string;
  job_type: ScheduleJobType;
  execution_status: JobExecutionStatus;
  trigger_type: "scheduled" | "manual" | "misfire_recovery" | "retry";
  started_at?: string | null;
  completed_at?: string | null;
  input_payload: Record<string, unknown>;
  result_summary?: Record<string, unknown> | null;
  error_details?: string | null;
  retry_count: number;
  agent_task_id?: string | null;
  resource_type?: string | null;
  resource_id?: string | null;
  created_at: string;
}

export interface SchedulePreviewResponse {
  valid: boolean;
  frequency: string;
  cron_expression?: string | null;
  interval_minutes?: number | null;
  timezone: string;
  human_description: string;
  next_runs: Array<{ utc: string; local: string }>;
  nl_plan?: Record<string, unknown> | null;
  policy_notice?: string;
}

export type NotificationSeverity = "info" | "warning" | "high" | "critical";

export interface NotificationItem {
  id: string;
  user_id?: string | null;
  category: string;
  severity: NotificationSeverity | string;
  title: string;
  message: string;
  resource_type?: string | null;
  resource_id?: string | null;
  environment_name?: string | null;
  action_href?: string | null;
  metadata_json: Record<string, unknown>;
  is_read: boolean;
  read_at?: string | null;
  is_resolved: boolean;
  resolved_at?: string | null;
  created_at: string;
}

export interface NotificationPreference {
  in_app_enabled: boolean;
  severity_threshold: NotificationSeverity;
  muted_categories: string[];
  updated_at?: string | null;
}

export interface DeepSystemHealth {
  status: "healthy" | "degraded" | "unhealthy" | string;
  checked_at: string;
  database: { status: string; latency_ms: number; error?: string | null };
  backend_api: {
    status: string;
    requests_total: number;
    errors_5xx: number;
    avg_latency_ms?: number | null;
    last_error?: string | null;
  };
  scheduler: {
    instance_id: string;
    running: boolean;
    poll_interval_seconds: number;
    started_at?: string | null;
    last_tick_at?: string | null;
    ticks_completed: number;
    last_error?: string | null;
    attention_schedules: number;
  };
  workers: {
    active_agent_tasks: number;
    failed_agent_tasks: number;
    running_job_executions: number;
    failed_job_executions: number;
  };
  integrations: {
    github: Record<string, unknown>;
    flagsmith: Record<string, unknown>;
    gemini: Record<string, unknown>;
    recent_errors: Array<{
      id: string;
      event_action: string;
      created_at?: string | null;
      details: Record<string, unknown>;
    }>;
  };
  demo_app: {
    backend: { status: string; http_status?: number; latency_ms?: number | null; url?: string; error?: string };
    frontend: { status: string; http_status?: number; latency_ms?: number | null; url?: string; error?: string };
  };
}

export type ReleaseStatus =
  | "draft"
  | "initializing"
  | "analyzing"
  | "awaiting_patch"
  | "validating"
  | "ready_for_flag"
  | "flag_provisioning"
  | "experiment_running"
  | "awaiting_approval"
  | "approved"
  | "rolling_out"
  | "monitoring"
  | "completed"
  | "paused"
  | "blocked"
  | "failed"
  | "rolled_back"
  | "cancelled";

export type ReleaseType =
  | "feature"
  | "hotfix"
  | "experiment"
  | "config"
  | "rollback_recovery";

export type ReleaseStageKey =
  | "draft"
  | "pr_analysis"
  | "patch_validation"
  | "flag_setup"
  | "experiment_monitoring"
  | "approval_gate"
  | "rollout_execution"
  | "post_release_verification"
  | "completed";

export type ReleaseStageStatus =
  | "pending"
  | "in_progress"
  | "waiting"
  | "passed"
  | "failed"
  | "skipped"
  | "blocked";

export type ReleaseReadinessStatus =
  | "ready"
  | "needs_attention"
  | "blocked"
  | "not_ready";

export type ReleaseCheckStatus =
  | "passed"
  | "failed"
  | "warning"
  | "pending"
  | "skipped";

export type ReleaseArtifactType =
  | "pr_analysis"
  | "code_patch"
  | "validation_report"
  | "flag_config"
  | "experiment_report"
  | "approval_snapshot"
  | "release_report"
  | "incident_report";

export interface ReleaseStage {
  id: string;
  release_id: string;
  stage_key: ReleaseStageKey;
  stage_order: number;
  title: string;
  status: ReleaseStageStatus;
  attempt_count: number;
  max_attempts: number;
  summary?: string | null;
  details_json: Record<string, unknown>;
  error_message?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReleaseEvent {
  id: string;
  release_id: string;
  event_type: string;
  from_status?: ReleaseStatus | null;
  to_status?: ReleaseStatus | null;
  stage_key?: ReleaseStageKey | null;
  actor_id?: string | null;
  actor_type: string;
  title: string;
  message?: string | null;
  payload_json: Record<string, unknown>;
  created_at: string;
}

export interface ReleaseArtifact {
  id: string;
  release_id: string;
  artifact_type: ReleaseArtifactType;
  title: string;
  source_table?: string | null;
  source_id?: string | null;
  uri?: string | null;
  summary?: string | null;
  content_json: Record<string, unknown>;
  created_by_id?: string | null;
  created_at: string;
}

export interface ReleaseCheck {
  id: string;
  release_id: string;
  check_key: string;
  category: string;
  title: string;
  status: ReleaseCheckStatus;
  is_blocking: boolean;
  weight: number;
  message?: string | null;
  details_json: Record<string, unknown>;
  evaluated_at?: string | null;
}

export interface ReleaseReadinessSummary {
  score?: number;
  status?: ReleaseReadinessStatus;
  blocking_issues?: string[];
  missing_checks?: string[];
  warnings?: string[];
  passed_checks?: string[];
  failed_checks?: string[];
  recommended_actions?: string[];
  evaluated_at?: string;
}

export interface ReleaseRecord {
  id: string;
  release_key: string;
  title: string;
  description?: string | null;
  release_type: ReleaseType;
  target_environment: string;
  status: ReleaseStatus;
  previous_status?: ReleaseStatus | null;
  version: number;
  repository_id?: string | null;
  pull_request_id?: string | null;
  feature_flag_id?: string | null;
  experiment_id?: string | null;
  owner_id?: string | null;
  created_by_id?: string | null;
  risk_level: RiskLevel;
  readiness_score: number;
  readiness_status: ReleaseReadinessStatus;
  readiness_summary: ReleaseReadinessSummary;
  target_rollout_percentage: number;
  current_rollout_percentage: number;
  ai_summary?: string | null;
  metadata_json: Record<string, unknown>;
  error_message?: string | null;
  scheduled_for?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  paused_at?: string | null;
  cancelled_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReleaseDetail {
  release: ReleaseRecord;
  stages: ReleaseStage[];
  checks: ReleaseCheck[];
  events: ReleaseEvent[];
  artifacts: ReleaseArtifact[];
  approvals: Approval[];
}

export interface ReleaseCreateInput {
  title?: string;
  description?: string;
  release_type?: ReleaseType;
  target_environment?: string;
  repository_id?: string;
  pull_request_id?: string;
  feature_flag_id?: string;
  experiment_id?: string;
  risk_level?: RiskLevel;
  target_rollout_percentage?: number;
  scheduled_for?: string;
  ai_assist?: boolean;
  allow_duplicate?: boolean;
}

export interface ReleaseUpdateInput {
  expected_version: number;
  title?: string;
  description?: string;
  target_environment?: string;
  feature_flag_id?: string;
  experiment_id?: string;
  risk_level?: RiskLevel;
  target_rollout_percentage?: number;
  scheduled_for?: string;
}

export type AgentExecutionStatus =
  | "queued"
  | "initializing"
  | "planning"
  | "running"
  | "tool_running"
  | "waiting_for_approval"
  | "waiting_for_input"
  | "pause_requested"
  | "paused"
  | "resuming"
  | "retrying"
  | "completed"
  | "failed"
  | "cancellation_requested"
  | "cancelled"
  | "timed_out"
  | "interrupted"
  | "recovering";

export type AgentStepStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "skipped"
  | "paused"
  | "cancelled";

export type AgentToolCallStatus =
  | "running"
  | "completed"
  | "failed"
  | "timed_out"
  | "cancelled";

export type AgentDecisionSource =
  | "ai_recommendation"
  | "deterministic_rule"
  | "verified_fact"
  | "human_decision";

export type AgentDecisionStatus = "pending" | "accepted" | "rejected" | "superseded";

export interface AgentExecutionStep {
  id: string;
  execution_id: string;
  step_key: string;
  step_name: string;
  step_type: string;
  sequence: number;
  status: AgentStepStatus;
  description?: string | null;
  input_summary: Record<string, unknown>;
  output_summary: Record<string, unknown>;
  started_at?: string | null;
  completed_at?: string | null;
  duration_ms?: number | null;
  retry_count: number;
  error_details?: string | null;
  created_at: string;
}

export interface AgentToolCall {
  id: string;
  execution_id: string;
  step_id?: string | null;
  tool_name: string;
  tool_category: string;
  invocation_id: string;
  status: AgentToolCallStatus;
  is_reversible: boolean;
  input_summary: Record<string, unknown>;
  output_summary: Record<string, unknown>;
  started_at?: string | null;
  completed_at?: string | null;
  duration_ms?: number | null;
  retry_count: number;
  error_code?: string | null;
  error_message?: string | null;
  created_at: string;
}

export interface AgentDecision {
  id: string;
  execution_id: string;
  step_id?: string | null;
  decision_type: string;
  decision_source: AgentDecisionSource;
  decision_summary: string;
  recommendation?: string | null;
  confidence?: number | null;
  decision_status: AgentDecisionStatus;
  supporting_evidence: string[];
  evidence_references: Array<Record<string, unknown>>;
  model_name?: string | null;
  created_at: string;
}

export interface AgentExecutionTrace {
  id: string;
  execution_id: string;
  sequence_number: number;
  event_type: string;
  category: string;
  stage?: string | null;
  actor_type: string;
  actor_id?: string | null;
  message: string;
  payload: Record<string, unknown>;
  correlation_id: string;
  created_at: string;
}

export interface AgentExecutionSnapshot {
  id: string;
  execution_id: string;
  checkpoint_number: number;
  checkpoint_type: string;
  resumable_state: AgentExecutionStatus;
  current_step_id?: string | null;
  completed_step_ids: string[];
  pending_operation?: string | null;
  context_reference: Record<string, unknown>;
  created_at: string;
}

export interface AgentExecutionRecord {
  id: string;
  execution_key: string;
  agent_type: string;
  agent_name: string;
  task_name: string;
  task_description?: string | null;
  status: AgentExecutionStatus;
  previous_status?: AgentExecutionStatus | null;
  priority: number;
  execution_mode: string;
  initiated_by_id?: string | null;
  actor_type: string;
  agent_task_id?: string | null;
  repository_id?: string | null;
  pull_request_id?: string | null;
  release_id?: string | null;
  experiment_id?: string | null;
  schedule_id?: string | null;
  parent_execution_id?: string | null;
  current_stage?: string | null;
  current_activity?: string | null;
  completed_steps: number;
  total_steps?: number | null;
  started_at?: string | null;
  completed_at?: string | null;
  last_heartbeat_at?: string | null;
  timeout_at?: string | null;
  duration_ms?: number | null;
  error_code?: string | null;
  error_message?: string | null;
  retry_count: number;
  max_retries: number;
  pause_requested: boolean;
  cancel_requested: boolean;
  correlation_id: string;
  execution_metadata: Record<string, unknown>;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface AgentExecutionDetail {
  execution: AgentExecutionRecord;
  steps: AgentExecutionStep[];
  tool_calls: AgentToolCall[];
  decisions: AgentDecision[];
  traces: AgentExecutionTrace[];
  checkpoints: AgentExecutionSnapshot[];
  approvals: Approval[];
}

export interface AgentExecutionOverview {
  total_executions: number;
  active_executions: number;
  queued_executions: number;
  waiting_for_approval: number;
  paused_executions: number;
  failed_executions: number;
  completed_executions: number;
  average_duration_ms?: number | null;
  tool_failure_rate?: number | null;
  total_tool_calls: number;
  failed_tool_calls: number;
}

export type RiskClassification = "low" | "moderate" | "elevated" | "high" | "critical";
export type RiskAssessmentStatus =
  | "pending"
  | "running"
  | "completed"
  | "partial"
  | "failed"
  | "outdated";
export type MitigationStatus = "pending" | "accepted" | "rejected" | "completed";

export interface RiskDimensionResult {
  id: string;
  assessment_id: string;
  dimension_key: string;
  dimension_name: string;
  score?: number | null;
  weight: number;
  weighted_contribution?: number | null;
  severity: RiskClassification;
  status: string;
  data_sufficiency: string;
  evidence_quality: string;
  explanations: string[];
  limitations: string[];
  details_json: Record<string, unknown>;
  created_at: string;
}

export interface RiskFinding {
  id: string;
  assessment_id: string;
  dimension_key: string;
  finding_type: string;
  title: string;
  description: string;
  severity: RiskClassification;
  is_ai_inferred: boolean;
  affected_module?: string | null;
  source_reference?: string | null;
  evidence_summary?: string | null;
  status: string;
  created_at: string;
}

export interface RiskEvidence {
  id: string;
  assessment_id: string;
  finding_id?: string | null;
  evidence_type: string;
  source_type: string;
  source_reference: string;
  title: string;
  summary: string;
  structured_data: Record<string, unknown>;
  collected_at: string;
}

export interface RiskMitigation {
  id: string;
  assessment_id: string;
  finding_id?: string | null;
  dimension_key: string;
  title: string;
  description: string;
  priority: RiskClassification;
  mitigation_type: string;
  status: MitigationStatus;
  required: boolean;
  suggested_role?: string | null;
  verification_criteria?: string | null;
  verification_evidence?: string | null;
  completed_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface RiskScenario {
  id: string;
  assessment_id: string;
  title: string;
  description: string;
  trigger_condition: string;
  affected_component: string;
  potential_impact: string;
  severity: RiskClassification;
  likelihood?: string | null;
  is_potential: boolean;
  supporting_evidence: string[];
  detection_strategy?: string | null;
  mitigation_summary?: string | null;
  related_dimensions: string[];
  created_at: string;
}

export interface ReleaseRiskAssessmentRecord {
  id: string;
  assessment_key: string;
  release_id?: string | null;
  pull_request_id?: string | null;
  repository_id?: string | null;
  execution_id?: string | null;
  target_environment: string;
  commit_sha?: string | null;
  release_version?: number | null;
  assessment_status: RiskAssessmentStatus;
  is_current: boolean;
  risk_score?: number | null;
  risk_level: RiskClassification;
  assessment_confidence?: number | null;
  data_completeness: number;
  flag_suitability: string;
  scoring_version: number;
  analyzer_version: string;
  evaluated_dimensions: string[];
  unavailable_dimensions: string[];
  limitations: string[];
  critical_overrides: string[];
  evidence_summary: Record<string, unknown>;
  dependency_graph: Record<string, unknown>;
  ai_interpretation?: string | null;
  fallback_mode: boolean;
  completed_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface RiskAssessmentDetail {
  assessment: ReleaseRiskAssessmentRecord;
  dimensions: RiskDimensionResult[];
  findings: RiskFinding[];
  evidence: RiskEvidence[];
  mitigations: RiskMitigation[];
  scenarios: RiskScenario[];
}

export type ProgressiveRolloutStatus =
  | "draft"
  | "ready"
  | "awaiting_approval"
  | "observing"
  | "evaluating"
  | "advance_recommended"
  | "paused"
  | "rollback_recommended"
  | "awaiting_rollback_approval"
  | "rolling_back"
  | "rolled_back"
  | "completed"
  | "failed"
  | "cancelled";

export type ProgressiveStageStatus =
  | "pending"
  | "awaiting_approval"
  | "active"
  | "observing"
  | "evaluating"
  | "passed"
  | "paused"
  | "failed"
  | "rolled_back"
  | "skipped";

export type GuardrailEvalStatus =
  | "pass"
  | "warning"
  | "breach"
  | "insufficient_data"
  | "stale_data";

export type RolloutDecisionType =
  | "advance_recommended"
  | "continue_observation"
  | "pause_recommended"
  | "rollback_recommended"
  | "manual_review_required"
  | "blocked"
  | "completed";

export type RollbackOperationStatus =
  | "pending"
  | "awaiting_approval"
  | "executing"
  | "completed"
  | "verified"
  | "failed";

export interface ProgressiveRolloutStage {
  id: string;
  rollout_id: string;
  stage_number: number;
  target_percentage: number;
  status: ProgressiveStageStatus;
  observation_started_at?: string | null;
  observation_ends_at?: string | null;
  minimum_observation_seconds: number;
  maximum_observation_seconds: number;
  minimum_sample_size: number;
  actual_sample_size: number;
  healthy_evaluations_count: number;
  activated_at?: string | null;
  completed_at?: string | null;
  evaluation_status?: string | null;
  decision?: RolloutDecisionType | null;
  approval_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface RolloutMetricSnapshot {
  id: string;
  rollout_id: string;
  stage_id?: string | null;
  metric_key: string;
  metric_value: number;
  sample_size: number;
  numerator?: number | null;
  denominator?: number | null;
  unit: string;
  window_start: string;
  window_end: string;
  data_source: string;
  freshness_status: string;
  created_at: string;
}

export interface RolloutGuardrail {
  id: string;
  rollout_id: string;
  metric_key: string;
  operator: string;
  threshold: number;
  severity: RiskClassification;
  evaluation_window_seconds: number;
  minimum_sample_size: number;
  consecutive_breach_count: number;
  current_breach_streak: number;
  action: string;
  enabled: boolean;
  created_at: string;
}

export interface GuardrailEvaluation {
  id: string;
  rollout_id: string;
  stage_id?: string | null;
  guardrail_id: string;
  observed_value?: number | null;
  threshold: number;
  sample_size: number;
  status: GuardrailEvalStatus;
  severity: RiskClassification;
  evidence: Record<string, unknown>;
  evaluated_at: string;
}

export interface RolloutDecision {
  id: string;
  rollout_id: string;
  stage_id?: string | null;
  decision_type: RolloutDecisionType;
  decision_summary: string;
  supporting_evidence: string[];
  missing_evidence: string[];
  recommended_action?: string | null;
  decision_source: string;
  created_at: string;
}

export interface ProgressiveRolloutEvent {
  id: string;
  rollout_id: string;
  stage_id?: string | null;
  event_type: string;
  actor_type: string;
  actor_id?: string | null;
  message: string;
  structured_data: Record<string, unknown>;
  created_at: string;
}

export interface RollbackOperation {
  id: string;
  rollout_id: string;
  stage_id?: string | null;
  feature_flag_id: string;
  environment: string;
  previous_configuration: Record<string, unknown>;
  requested_configuration: Record<string, unknown>;
  status: RollbackOperationStatus;
  reason: string;
  approval_id?: string | null;
  verification_result: Record<string, unknown>;
  started_at?: string | null;
  completed_at?: string | null;
  created_at: string;
}

export interface ProgressiveRolloutRecord {
  id: string;
  rollout_key: string;
  name: string;
  description?: string | null;
  release_id?: string | null;
  repository_id?: string | null;
  pull_request_id?: string | null;
  commit_sha?: string | null;
  feature_flag_id: string;
  feature_flag_key: string;
  experiment_id?: string | null;
  environment: string;
  status: ProgressiveRolloutStatus;
  current_stage_id?: string | null;
  current_percentage: number;
  target_percentage: number;
  primary_metric_key: string;
  rollout_configuration: Record<string, unknown>;
  observation_configuration: Record<string, unknown>;
  rollback_configuration: Record<string, unknown>;
  started_at?: string | null;
  completed_at?: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface ProgressiveRolloutDetail {
  rollout: ProgressiveRolloutRecord;
  stages: ProgressiveRolloutStage[];
  guardrails: RolloutGuardrail[];
  metrics: RolloutMetricSnapshot[];
  evaluations: GuardrailEvaluation[];
  decisions: RolloutDecision[];
  events: ProgressiveRolloutEvent[];
  rollbacks: RollbackOperation[];
}

export interface ProgressiveRolloutCreateInput {
  name: string;
  description?: string | null;
  feature_flag_id: string;
  environment?: string;
  release_id?: string | null;
  experiment_id?: string | null;
  stage_percentages?: number[] | null;
  primary_metric_key?: string;
  observation_config?: Record<string, unknown> | null;
  guardrails?: Array<Record<string, unknown>> | null;
}

// Phase 5: Release Intelligence & Learning Engine
export type PolicyReviewStatus =
  | "pending_review"
  | "accepted"
  | "rejected"
  | "modified"
  | "implemented"
  | "verified"
  | "superseded";

export type RecommendationOutcomeClass =
  | "verified_benefit"
  | "observed_positive_outcome"
  | "no_measurable_impact"
  | "negative_outcome"
  | "inconclusive"
  | "not_evaluable";

export interface ReleaseFailurePattern {
  id: string;
  pattern_key: string;
  name: string;
  description: string;
  category: string;
  repository_id?: string | null;
  affected_module?: string | null;
  dependency_reference?: string | null;
  sample_size: number;
  failure_count: number;
  observed_rate: number;
  evidence_quality: string;
  is_exploratory: boolean;
  confidence_data: Record<string, unknown>;
  supporting_release_ids: string[];
  recommended_mitigation?: string | null;
  first_observed_at?: string | null;
  last_observed_at?: string | null;
  last_analyzed_at: string;
}

export interface RepositoryIntelligenceProfile {
  id: string;
  repository_id: string;
  repository_name?: string | null;
  release_count: number;
  failure_count: number;
  rollback_count: number;
  historical_failure_rate: number;
  avg_recovery_seconds?: number | null;
  common_failure_categories: Array<{ category: string; count: number }>;
  high_risk_modules: Array<{
    module: string;
    release_count: number;
    failure_count: number;
    failure_rate: number;
  }>;
  dependency_patterns: Array<Record<string, unknown>>;
  risk_indicators: Record<string, unknown>;
  recommendations: Array<{
    title: string;
    rationale: string;
    policy_area: string;
  }>;
  last_analyzed_at: string;
  version: number;
}

export interface DependencyIntelligenceItem {
  dependency: string;
  release_count: number;
  failure_count: number;
  rollback_count: number;
  failure_rate: number;
  repositories: string[];
  release_ids: string[];
  recommendation: string;
}

export interface RecommendationOutcomeRecord {
  id: string;
  recommendation_id: string;
  release_id?: string | null;
  repository_id?: string | null;
  recommendation_category: string;
  outcome_classification: RecommendationOutcomeClass;
  accepted?: boolean | null;
  implemented: boolean;
  verified: boolean;
  actual_outcome?: string | null;
  evidence: Record<string, unknown>;
  evaluation_method: string;
  evaluated_at: string;
}

export interface RecommendationAccuracyMetrics {
  total_recommendations: number;
  responded_count: number;
  accepted_count: number;
  rejected_count: number;
  implemented_count: number;
  verified_count: number;
  evaluable_count: number;
  positive_outcome_count: number;
  negative_outcome_count: number;
  inconclusive_or_not_evaluable_count: number;
  acceptance_rate?: number | null;
  implementation_rate?: number | null;
  verification_rate?: number | null;
  positive_outcome_rate?: number | null;
  by_category: Record<string, { total: number; accepted: number; positive: number; negative: number }>;
  by_model: Record<string, { total: number; accepted: number; positive: number; negative: number }>;
  by_agent: Record<string, { total: number; accepted: number; positive: number; negative: number }>;
  methodology_note: string;
}

export interface PolicyRecommendationRecord {
  id: string;
  title: string;
  description: string;
  policy_area: string;
  current_configuration: Record<string, unknown>;
  proposed_configuration: Record<string, unknown>;
  approved_configuration?: Record<string, unknown> | null;
  supporting_evidence: Record<string, unknown>;
  affected_repositories: string[];
  expected_benefit?: string | null;
  tradeoffs?: string | null;
  confidence_data: Record<string, unknown>;
  review_status: PolicyReviewStatus;
  reviewer_id?: string | null;
  review_notes?: string | null;
  approval_id?: string | null;
  reviewed_at?: string | null;
  implemented_at?: string | null;
  verified_at?: string | null;
  verification_evidence: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ReleaseInsightRecord {
  id: string;
  insight_key: string;
  title: string;
  description: string;
  insight_category: string;
  evidence: Record<string, unknown>;
  evidence_classification: string;
  affected_repositories: string[];
  affected_releases: string[];
  sample_size: number;
  confidence_data: Record<string, unknown>;
  severity: RiskClassification;
  status: string;
  generated_by: string;
  created_at: string;
  updated_at: string;
}

export interface IntelligencePerformanceMetrics {
  window_days: number;
  total_records: number;
  qualifying_releases: number;
  successful_releases: number;
  failed_releases: number;
  rolled_back_releases: number;
  verified_outcomes: number;
  incomplete_outcomes: number;
  deployment_frequency_per_week: number;
  change_failure_rate?: number | null;
  change_failure_rate_ci: Record<string, unknown>;
  rollback_rate?: number | null;
  release_success_rate?: number | null;
  mean_time_to_recovery_seconds?: number | null;
  lead_time_seconds?: number | null;
  approval_latency_seconds?: number | null;
  guardrail_breach_rate?: number | null;
  guardrail_evaluations_count: number;
  data_sufficiency: string;
  definitions: Record<string, string>;
}

export interface IntelligenceOverview {
  performance: IntelligencePerformanceMetrics;
  trend_direction: string;
  previous_period: Record<string, unknown>;
  rollback_summary: {
    total_rollbacks: number;
    top_causes: Array<{ category: string; count: number }>;
    avg_recovery_seconds?: number | null;
  };
  recommendation_accuracy: RecommendationAccuracyMetrics;
  top_patterns: ReleaseFailurePattern[];
  insights: ReleaseInsightRecord[];
  pending_policy_recommendations: number;
}

export interface IntelligenceKnowledgeResponse {
  question: string;
  direct_answer: string;
  supporting_evidence: {
    window_days: number;
    qualifying_releases: number;
    sample_size: number;
    references: Array<Record<string, unknown>>;
  };
  relevant_releases: Array<{ id: string; release_key: string; title: string; status: string }>;
  observed_pattern: string;
  confidence_and_limitations: string[];
  recommended_action: string;
  evidence_classification: string;
}

export interface IntelligenceAnalysisRun {
  id: string;
  analysis_type: string;
  scope: Record<string, unknown>;
  status: string;
  input_record_count: number;
  output_record_count: number;
  summary: Record<string, unknown>;
  error?: string | null;
  model_version?: string | null;
  analysis_version: string;
  started_at: string;
  completed_at?: string | null;
}

// Phase 6: Developer Experience Integration
export interface GitHubInstallationRecord {
  id: string;
  installation_id: number;
  account_login: string;
  account_type: string;
  repository_selection: string;
  repositories: Array<Record<string, unknown>>;
  permissions: Record<string, string>;
  status: string;
  token_expires_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface GitHubWebhookEventRecord {
  id: string;
  delivery_id: string;
  event_type: string;
  action?: string | null;
  installation_id?: number | null;
  repository_id?: string | null;
  pr_number?: number | null;
  commit_sha?: string | null;
  signature_verified: boolean;
  processing_status: string;
  retry_count: number;
  error_message?: string | null;
  correlation_id: string;
  payload_summary: Record<string, unknown>;
  received_at: string;
  processed_at?: string | null;
}

export interface NotificationDeliveryRecord {
  id: string;
  provider: string;
  channel_target: string;
  event_type: string;
  severity: string;
  status: string;
  retry_count: number;
  response_status?: number | null;
  error_message?: string | null;
  sent_at?: string | null;
  created_at: string;
}

export interface NotificationIntegrationSummary {
  connections: Array<{
    id: string;
    provider: string;
    status: string;
    account_label?: string | null;
    token_hint?: string | null;
    verified_at?: string | null;
  }>;
  deliveries: NotificationDeliveryRecord[];
}

export interface ReleaseReadinessReportRecord {
  id: string;
  release_id: string;
  report_key: string;
  title: string;
  readiness_outcome: string;
  report_data: Record<string, unknown>;
  markdown_content: string;
  created_by_id?: string | null;
  created_at: string;
}

export interface ShareableReportRecord {
  id: string;
  report_id: string;
  expires_at: string;
  revoked_at?: string | null;
  access_count: number;
  last_accessed_at?: string | null;
  created_at: string;
  share_token?: string | null;
  share_url?: string | null;
}

// Phase 7: Multi-Repository Release Intelligence
export interface OrganizationRecord {
  id: string;
  name: string;
  slug: string;
  github_org_id?: number | null;
  settings: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface OrganizationReleaseOverview {
  organization_id: string;
  total_repositories: number;
  total_releases: number;
  active_releases: number;
  successful_releases: number;
  failed_releases: number;
  blocked_releases: number;
  active_rollouts: number;
  pending_approvals: number;
  change_failure_rate?: number | null;
}

export interface DependencyGraphData {
  nodes: string[];
  edges: Array<{
    id: string;
    source_repository_id: string;
    target_repository_id: string;
    dependency_type: string;
    version_constraint?: string | null;
    verification_status: string;
  }>;
  recommended_order: string[];
  has_cycles: boolean;
}

export interface CoordinatedReleasePlanRecord {
  id: string;
  organization_id: string;
  name: string;
  status: string;
  risk_summary: Record<string, unknown>;
  recommended_order: string[];
  blocking_dependencies: Array<Record<string, unknown>>;
  created_by_id?: string | null;
  approved_by_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface RepositoryHealthSnapshotRecord {
  id: string;
  repository_id: string;
  health_score?: number | null;
  health_category: string;
  evidence: Record<string, unknown>;
  sample_size: number;
  data_completeness: number;
  calculated_at: string;
}

export interface OrganizationReleasePolicyRecord {
  id: string;
  organization_id: string;
  name: string;
  policy_type: string;
  configuration: Record<string, unknown>;
  is_mandatory: boolean;
  version: number;
  status: string;
  created_by_id?: string | null;
  approved_by_id?: string | null;
  created_at: string;
  updated_at: string;
}
