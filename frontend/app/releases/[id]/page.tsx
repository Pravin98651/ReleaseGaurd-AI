"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  AlertTriangle,
  Archive,
  ArrowLeft,
  ArrowRight,
  Bot,
  CheckCircle2,
  Clock,
  Download,
  ExternalLink,
  FileCode2,
  FileText,
  FlaskConical,
  GitPullRequest,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Trash2,
  UserCheck,
  Wifi,
  WifiOff,
  X,
  XCircle,
} from "lucide-react";
import {
  API_BASE_URL,
  ApiError,
  analyzeRelease,
  cancelRelease,
  createRiskAssessment,
  evaluateReleaseReadiness,
  generateCleanupPr,
  getErrorMessage,
  getExperimentAnalysis,
  getExperimentRecommendation,
  getPullRequest,
  getReleaseDetail,
  getReleaseReportExportUrl,
  listAgentExecutions,
  listExperiments,
  listFeatureFlags,
  listRepositories,
  listRiskAssessments,
  listRollouts,
  pauseRelease,
  requestFlagDelete,
  requestReleaseApproval,
  resumeRelease,
  retryRelease,
  validateRelease,
} from "@/lib/api";
import type {
  AgentExecutionRecord,
  Approval,
  CodeChange,
  Experiment,
  ExperimentAnalysis,
  ExperimentRecommendation,
  FeatureFlag,
  MutationOutcome,
  ProgressiveRolloutRecord,
  PullRequest,
  ReleaseDetail,
  ReleaseRiskAssessmentRecord,
  ReleaseStage,
  ReleaseStageStatus,
  Repository,
} from "@/types/releaseguard";
import { formatPct, formatRate, formatTimestamp, truncateHash } from "@/lib/utils";
import {
  ErrorBanner,
  LoadingSkeleton,
  PageHeader,
  SectionCard,
  SuccessBanner,
} from "@/components/ui/StatePrimitives";
import { EnvironmentBadge, RiskLevelBadge, StatusBadge } from "@/components/StatusBadges";
import { EvidenceBadge } from "@/components/EvidenceBadge";
import { ApprovalActionCard } from "@/components/ApprovalActionCard";

type CanonicalStageState =
  | "Pending"
  | "Running"
  | "Completed"
  | "Failed"
  | "Blocked"
  | "Awaiting Approval"
  | "Skipped";

interface LifecycleStageView {
  order: number;
  key: string;
  title: string;
  state: CanonicalStageState;
  badgeStatus: string;
  startedAt: string | null;
  completedAt: string | null;
  agent: string;
  summary: string;
  evidence: string[];
  warnings: string[];
  nextAction: {
    label: string;
    description: string;
    actionKey?: string;
    href?: string;
  };
}

interface LiveSseItem {
  id: string;
  eventType: string;
  message: string;
  timestamp: string;
}

type EpistemicKind = "Verified Fact" | "Observation" | "Simulation" | "AI Inference";

const EPISTEMIC_STYLES: Record<EpistemicKind, string> = {
  "Verified Fact": "border-emerald-500/35 bg-emerald-500/10 text-emerald-300",
  Observation: "border-sky-500/35 bg-sky-500/10 text-sky-300",
  Simulation: "border-amber-500/35 bg-amber-500/10 text-amber-300",
  "AI Inference": "border-indigo-500/35 bg-indigo-500/10 text-indigo-300",
};

function EpistemicBadge({ kind }: { kind: EpistemicKind }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${EPISTEMIC_STYLES[kind]}`}
    >
      {kind}
    </span>
  );
}

function mapStageStatus(
  status: ReleaseStageStatus | undefined,
  opts?: { isApprovalStage?: boolean },
): { state: CanonicalStageState; badgeStatus: string } {
  switch (status) {
    case "in_progress":
      return { state: "Running", badgeStatus: "running" };
    case "passed":
      return { state: "Completed", badgeStatus: "completed" };
    case "failed":
      return { state: "Failed", badgeStatus: "failed" };
    case "blocked":
      return { state: "Blocked", badgeStatus: "blocked" };
    case "waiting":
      return opts?.isApprovalStage
        ? { state: "Awaiting Approval", badgeStatus: "awaiting_approval" }
        : { state: "Pending", badgeStatus: "pending" };
    case "skipped":
      return { state: "Skipped", badgeStatus: "skipped" };
    case "pending":
    default:
      return { state: "Pending", badgeStatus: "pending" };
  }
}

function buildNineLifecycleStages(params: {
  detail: ReleaseDetail;
  pullRequest: PullRequest | null;
  riskAssessment: ReleaseRiskAssessmentRecord | null;
  featureFlag: FeatureFlag | null;
  experiment: Experiment | null;
  experimentAnalysis: ExperimentAnalysis | null;
  experimentRecommendation: ExperimentRecommendation | null;
  rollouts: ProgressiveRolloutRecord[];
  executions: AgentExecutionRecord[];
}): LifecycleStageView[] {
  const {
    detail,
    pullRequest,
    riskAssessment,
    featureFlag,
    experiment,
    experimentAnalysis,
    experimentRecommendation,
    rollouts,
    executions,
  } = params;
  const { release, stages, checks, approvals } = detail;
  const stageByKey = new Map<string, ReleaseStage>(stages.map((s) => [s.stage_key, s]));

  const findExecutionAgent = (keywords: string[], fallback: string): string => {
    const matched = executions.find((ex) =>
      keywords.some(
        (kw) =>
          ex.agent_type.toLowerCase().includes(kw) ||
          ex.agent_name.toLowerCase().includes(kw) ||
          (ex.current_stage ?? "").toLowerCase().includes(kw),
      ),
    );
    return matched ? `${matched.agent_name} (${matched.execution_key})` : fallback;
  };

  // 1. PR Analysis
  const prStage = stageByKey.get("pr_analysis");
  const prMapped = mapStageStatus(
    prStage?.status ??
      (pullRequest?.analysis_status === "completed"
        ? "passed"
        : pullRequest?.analysis_status === "analyzing"
          ? "in_progress"
          : pullRequest?.analysis_status === "failed"
            ? "failed"
            : "pending"),
  );
  const prEvidence: string[] = [];
  if (pullRequest) {
    prEvidence.push(`PR #${pullRequest.github_pr_number}: ${pullRequest.title}`);
    prEvidence.push(`Head SHA: ${truncateHash(pullRequest.head_sha, 10)}`);
    prEvidence.push(
      `Confirmed risks: ${pullRequest.confirmed_risks?.length ?? 0}, Potential risks: ${pullRequest.potential_risks?.length ?? 0}`,
    );
  }
  const prWarnings: string[] = [];
  if (prStage?.error_message) prWarnings.push(prStage.error_message);
  if ((pullRequest?.confirmed_risks?.length ?? 0) > 0) {
    prWarnings.push(`${pullRequest?.confirmed_risks.length} confirmed risk(s) identified in PR diff.`);
  }

  // 2. Risk Assessment
  const riskStatus: ReleaseStageStatus = riskAssessment
    ? riskAssessment.risk_level === "critical" && release.status === "blocked"
      ? "blocked"
      : "passed"
    : prStage?.status === "in_progress"
      ? "in_progress"
      : "pending";
  const riskMapped = mapStageStatus(riskStatus);
  const riskEvidence: string[] = [];
  if (riskAssessment) {
    riskEvidence.push(
      `Assessment ${riskAssessment.assessment_key}: score ${riskAssessment.risk_score ?? "—"} (${riskAssessment.risk_level ?? release.risk_level})`,
    );
    if (riskAssessment.commit_sha) {
      riskEvidence.push(`Evaluated Commit SHA: ${truncateHash(riskAssessment.commit_sha, 10)}`);
    }
  } else {
    riskEvidence.push(`Release baseline risk level: ${release.risk_level}`);
  }
  const riskWarnings: string[] = [];
  if (release.risk_level === "high" || release.risk_level === "critical") {
    riskWarnings.push(`Elevated release risk (${release.risk_level}) requires human gate before rollout.`);
  }

  // 3. Code Patch Validation
  const patchStage = stageByKey.get("patch_validation");
  const patchMapped = mapStageStatus(patchStage?.status);
  const codeChanges = pullRequest?.code_changes ?? [];
  const patchEvidence: string[] = [];
  if (codeChanges.length > 0) {
    codeChanges.forEach((cc) => {
      patchEvidence.push(`${cc.title} (${cc.flag_key}) — validation: ${cc.validation_status}`);
    });
  } else if (patchStage?.summary) {
    patchEvidence.push(patchStage.summary);
  }
  const patchWarnings: string[] = [];
  if (patchStage?.error_message) patchWarnings.push(patchStage.error_message);
  codeChanges
    .filter((cc) => cc.validation_status === "failed")
    .forEach((cc) => patchWarnings.push(`Patch ${cc.title} failed validation.`));

  // 4. Feature Flag Setup
  const flagStage = stageByKey.get("flag_setup");
  const flagStatus: ReleaseStageStatus =
    flagStage?.status ?? (featureFlag ? "passed" : "pending");
  const flagMapped = mapStageStatus(flagStatus);
  const flagEvidence: string[] = [];
  if (featureFlag) {
    flagEvidence.push(`Flagsmith key: ${featureFlag.key} (${featureFlag.flag_type})`);
    flagEvidence.push(`Health score: ${featureFlag.health_score}/100`);
    const envNames = Object.keys(featureFlag.env_states ?? {});
    if (envNames.length > 0) {
      flagEvidence.push(`Environments configured: ${envNames.join(", ")}`);
    }
  }
  const flagWarnings: string[] = [];
  if (flagStage?.error_message) flagWarnings.push(flagStage.error_message);
  if (featureFlag && featureFlag.health_issues.length > 0) {
    flagWarnings.push(`${featureFlag.health_issues.length} flag health issue(s) detected.`);
  }

  // 5. Experimentation
  const expStage = stageByKey.get("experiment_monitoring");
  const expStatus: ReleaseStageStatus =
    expStage?.status ??
    (experiment?.status === "completed"
      ? "passed"
      : experiment?.status === "running"
        ? "in_progress"
        : experiment?.status === "rolled_back"
          ? "failed"
          : "pending");
  const expMapped = mapStageStatus(expStatus);
  const expEvidence: string[] = [];
  if (experiment) {
    expEvidence.push(`Experiment: ${experiment.name} (${experiment.status})`);
  }
  if (experimentAnalysis) {
    expEvidence.push(
      `Control ${formatRate(experimentAnalysis.control.mean_rate)} vs Treatment ${formatRate(experimentAnalysis.treatment.mean_rate)}`,
    );
    expEvidence.push(
      `P(Treatment > Control): ${formatPct(experimentAnalysis.comparison.prob_treatment_beats_control)}`,
    );
  }
  const expWarnings: string[] = [];
  if (expStage?.error_message) expWarnings.push(expStage.error_message);
  if (experimentAnalysis && !experimentAnalysis.sample_adequacy.is_adequate) {
    expWarnings.push(
      `Insufficient sample size (${experimentAnalysis.sample_adequacy.control_exposures}/${experimentAnalysis.sample_adequacy.treatment_exposures} vs ${experimentAnalysis.sample_adequacy.min_required} required).`,
    );
  }
  if (experimentAnalysis?.guardrails.payment_failed?.breached) {
    expWarnings.push("Guardrail breach detected on payment_failed.");
  }

  // 6. Release Recommendation
  const recAvailable = Boolean(experimentRecommendation || release.ai_summary || release.readiness_summary?.evaluated_at);
  const recStatus: ReleaseStageStatus =
    release.readiness_status === "blocked"
      ? "blocked"
      : recAvailable
        ? "passed"
        : "pending";
  const recMapped = mapStageStatus(recStatus);
  const recEvidence: string[] = [];
  if (experimentRecommendation) {
    recEvidence.push(`Experiment AI recommendation: ${experimentRecommendation.recommendation}`);
  }
  if (release.readiness_summary?.evaluated_at) {
    recEvidence.push(
      `Readiness score: ${release.readiness_score}/100 (${release.readiness_status})`,
    );
  }
  if (release.ai_summary) {
    recEvidence.push("AI release summary recorded.");
  }
  const recWarnings = [
    ...(release.readiness_summary?.blocking_issues ?? []),
    ...(release.readiness_summary?.warnings ?? []),
  ];

  // 7. Human Approval
  const appStage = stageByKey.get("approval_gate");
  const pendingApprovals = approvals.filter((a) => a.status === "pending");
  const rejectedApprovals = approvals.filter((a) => a.status === "rejected" || a.status === "revalidation_failed");
  const executedApprovals = approvals.filter((a) => a.status === "executed");
  const derivedAppStatus: ReleaseStageStatus =
    appStage?.status ??
    (pendingApprovals.length > 0
      ? "waiting"
      : rejectedApprovals.length > 0
        ? "failed"
        : executedApprovals.length > 0
          ? "passed"
          : "pending");
  const appMapped = mapStageStatus(derivedAppStatus, { isApprovalStage: true });
  if (pendingApprovals.length > 0) {
    appMapped.state = "Awaiting Approval";
    appMapped.badgeStatus = "awaiting_approval";
  }
  const appEvidence: string[] = approvals.map(
    (a) => `${a.title} [Cat ${a.policy_category}] — ${a.status}`,
  );
  const appWarnings: string[] = [];
  if (appStage?.error_message) appWarnings.push(appStage.error_message);
  if (rejectedApprovals.length > 0) {
    appWarnings.push(`${rejectedApprovals.length} approval(s) rejected or failed revalidation.`);
  }

  // 8. Progressive Rollout
  const rollStage = stageByKey.get("rollout_execution");
  const activeRollout = rollouts[0];
  const rollStatus: ReleaseStageStatus =
    rollStage?.status ??
    (release.current_rollout_percentage >= release.target_rollout_percentage &&
    release.target_rollout_percentage > 0 &&
    release.status === "completed"
      ? "passed"
      : release.status === "rolling_out" ||
          activeRollout?.status === "observing" ||
          activeRollout?.status === "evaluating"
        ? "in_progress"
        : release.status === "rolled_back"
          ? "failed"
          : "pending");
  const rollMapped = mapStageStatus(rollStatus);
  const rollEvidence: string[] = [
    `Current rollout: ${release.current_rollout_percentage}% / Target: ${release.target_rollout_percentage}%`,
  ];
  if (activeRollout) {
    rollEvidence.push(
      `Rollout ${activeRollout.rollout_key}: ${activeRollout.status} (${activeRollout.current_percentage}%)`,
    );
  }
  const rollWarnings: string[] = [];
  if (rollStage?.error_message) rollWarnings.push(rollStage.error_message);
  if (release.status === "rolled_back") rollWarnings.push("Release was rolled back.");

  // 9. Post-Release Verification
  const verStage = stageByKey.get("post_release_verification") ?? stageByKey.get("completed");
  const failedChecks = checks.filter((c) => c.status === "failed");
  const passedChecks = checks.filter((c) => c.status === "passed");
  const verMapped = mapStageStatus(verStage?.status);
  const verEvidence: string[] = [
    `Verification checks: ${passedChecks.length} passed, ${failedChecks.length} failed (${checks.length} total)`,
  ];
  const verWarnings: string[] = failedChecks.map(
    (c) => `${c.title}: ${c.message || "Check failed"}`,
  );
  if (verStage?.error_message) verWarnings.push(verStage.error_message);

  return [
    {
      order: 1,
      key: "pr_analysis",
      title: "PR Analysis",
      state: prMapped.state,
      badgeStatus: prMapped.badgeStatus,
      startedAt: prStage?.started_at ?? pullRequest?.created_at ?? null,
      completedAt: prStage?.completed_at ?? pullRequest?.analyzed_at ?? null,
      agent: findExecutionAgent(["pr"], "PR Agent"),
      summary:
        prStage?.summary ||
        pullRequest?.analysis_summary ||
        (release.pull_request_id
          ? "Ready to run PR diff & risk analysis."
          : "No pull request linked to this release."),
      evidence: prEvidence,
      warnings: prWarnings,
      nextAction: {
        label: pullRequest?.analysis_status === "completed" ? "Re-run PR Analysis" : "Analyze PR",
        actionKey: release.pull_request_id ? "analyze" : undefined,
        href: release.pull_request_id ? undefined : "/pull-requests",
        description: release.pull_request_id
          ? "Inspect PR diff, confirmed risks, and flag opportunities."
          : "Link a pull request to run automated PR analysis.",
      },
    },
    {
      order: 2,
      key: "risk_assessment",
      title: "Risk Assessment",
      state: riskMapped.state,
      badgeStatus: riskMapped.badgeStatus,
      startedAt: riskAssessment?.created_at ?? prStage?.started_at ?? null,
      completedAt: riskAssessment?.updated_at ?? null,
      agent: "Risk Intelligence Engine",
      summary: riskAssessment
        ? `7-dimension risk assessment completed: ${riskAssessment.risk_level ?? release.risk_level} (${riskAssessment.risk_score ?? "—"}/100).`
        : "Deterministic 7-dimension risk assessment has not been run yet.",
      evidence: riskEvidence,
      warnings: riskWarnings,
      nextAction: {
        label: riskAssessment ? "Re-run Risk Assessment" : "Run Risk Assessment",
        actionKey: "risk",
        description: "Compute 7-dimension deterministic risk score and mitigations.",
      },
    },
    {
      order: 3,
      key: "patch_validation",
      title: "Code Patch Validation",
      state: patchMapped.state,
      badgeStatus: patchMapped.badgeStatus,
      startedAt: patchStage?.started_at ?? null,
      completedAt: patchStage?.completed_at ?? null,
      agent: findExecutionAgent(["patch", "pr"], "Patch Validator"),
      summary:
        patchStage?.summary ||
        (codeChanges.length > 0
          ? `${codeChanges.length} generated code patch(es) tracked.`
          : "Awaiting isolated AST, lint, SHA, and test validation."),
      evidence: patchEvidence,
      warnings: patchWarnings,
      nextAction: {
        label: "Validate Patch",
        actionKey: release.pull_request_id ? "validate" : undefined,
        href: release.pull_request_id ? undefined : "/pull-requests",
        description: "Run isolated patch validation (AST, path safety, SHA, lint/tests).",
      },
    },
    {
      order: 4,
      key: "flag_setup",
      title: "Feature Flag Setup",
      state: flagMapped.state,
      badgeStatus: flagMapped.badgeStatus,
      startedAt: flagStage?.started_at ?? featureFlag?.created_at ?? null,
      completedAt: flagStage?.completed_at ?? featureFlag?.last_synced_at ?? null,
      agent: findExecutionAgent(["flag"], "Flag Agent"),
      summary:
        flagStage?.summary ||
        (featureFlag
          ? `Feature flag '${featureFlag.key}' is linked and tracked.`
          : "No feature flag linked yet."),
      evidence: flagEvidence,
      warnings: flagWarnings,
      nextAction: {
        label: "Open Flagsmith Hub",
        href: "/flagsmith",
        description: "Configure Flagsmith environments, targeting, or multivariate options.",
      },
    },
    {
      order: 5,
      key: "experiment_monitoring",
      title: "Experimentation",
      state: expMapped.state,
      badgeStatus: expMapped.badgeStatus,
      startedAt: expStage?.started_at ?? experiment?.started_at ?? null,
      completedAt: expStage?.completed_at ?? experiment?.ended_at ?? null,
      agent: findExecutionAgent(["experiment"], "Experiment Agent"),
      summary:
        expStage?.summary ||
        (experiment
          ? `Experiment '${experiment.name}' is ${experiment.status}.`
          : "No A/B experiment linked yet."),
      evidence: expEvidence,
      warnings: expWarnings,
      nextAction: {
        label: "Open Experiments",
        href: "/experiments",
        description: "Review Bayesian lift, sample adequacy, and guardrail telemetry.",
      },
    },
    {
      order: 6,
      key: "release_recommendation",
      title: "Release Recommendation",
      state: recMapped.state,
      badgeStatus: recMapped.badgeStatus,
      startedAt: release.readiness_summary?.evaluated_at ?? null,
      completedAt: release.readiness_summary?.evaluated_at ?? null,
      agent: findExecutionAgent(["supervisor"], "Release Supervisor"),
      summary:
        experimentRecommendation?.narrative ||
        release.ai_summary ||
        (release.readiness_summary?.evaluated_at
          ? `Readiness evaluated at ${release.readiness_score}/100 (${release.readiness_status}).`
          : "Evaluate readiness to synthesize deterministic gates and AI recommendation."),
      evidence: recEvidence,
      warnings: recWarnings,
      nextAction: {
        label: "Evaluate Readiness",
        actionKey: "readiness",
        description: "Re-evaluate blocking gates and readiness score.",
      },
    },
    {
      order: 7,
      key: "approval_gate",
      title: "Human Approval",
      state: appMapped.state,
      badgeStatus: appMapped.badgeStatus,
      startedAt: appStage?.started_at ?? approvals[0]?.created_at ?? null,
      completedAt: appStage?.completed_at ?? executedApprovals[0]?.executed_at ?? null,
      agent: "Human Operator (Policy Gate)",
      summary:
        pendingApprovals.length > 0
          ? `${pendingApprovals.length} approval request(s) awaiting human decision.`
          : executedApprovals.length > 0
            ? "Human approval granted and executed."
            : appStage?.summary || "Approval gate pending operator submission.",
      evidence: appEvidence,
      warnings: appWarnings,
      nextAction: {
        label: pendingApprovals.length > 0 ? "Review Pending Approval" : "Request Approval",
        actionKey: pendingApprovals.length > 0 ? "tab:approvals" : "approval",
        description:
          pendingApprovals.length > 0
            ? "Approve or reject the gated production/flag action."
            : "Submit release for Category B/C human approval.",
      },
    },
    {
      order: 8,
      key: "rollout_execution",
      title: "Progressive Rollout",
      state: rollMapped.state,
      badgeStatus: rollMapped.badgeStatus,
      startedAt: rollStage?.started_at ?? activeRollout?.started_at ?? null,
      completedAt: rollStage?.completed_at ?? activeRollout?.completed_at ?? null,
      agent: "Progressive Delivery Controller",
      summary:
        rollStage?.summary ||
        (activeRollout
          ? `Rollout ${activeRollout.rollout_key} at ${activeRollout.current_percentage}% (${activeRollout.status}).`
          : `Rollout at ${release.current_rollout_percentage}% of ${release.target_rollout_percentage}% target.`),
      evidence: rollEvidence,
      warnings: rollWarnings,
      nextAction: {
        label: "Open Progressive Delivery",
        href: "/progressive-delivery",
        description: "Manage staged percentage rollout and automatic rollback guardrails.",
      },
    },
    {
      order: 9,
      key: "post_release_verification",
      title: "Post-Release Verification",
      state: verMapped.state,
      badgeStatus: verMapped.badgeStatus,
      startedAt: verStage?.started_at ?? null,
      completedAt: verStage?.completed_at ?? release.completed_at ?? null,
      agent: "Verification & Audit Engine",
      summary:
        verStage?.summary ||
        (release.status === "completed"
          ? "Post-release verification completed."
          : "Final verification checks run after rollout progression."),
      evidence: verEvidence,
      warnings: verWarnings,
      nextAction: {
        label: "View Final Release Report",
        actionKey: "open:report",
        description: "Inspect Verified Facts, Observations, Simulations, and AI Inferences.",
      },
    },
  ];
}

export default function ReleaseDetailPage() {
  const params = useParams<{ id: string }>();
  const releaseId = params?.id ?? "";

  const [detail, setDetail] = useState<ReleaseDetail | null>(null);
  const [executions, setExecutions] = useState<AgentExecutionRecord[]>([]);
  const [pullRequest, setPullRequest] = useState<PullRequest | null>(null);
  const [riskAssessment, setRiskAssessment] = useState<ReleaseRiskAssessmentRecord | null>(null);
  const [featureFlag, setFeatureFlag] = useState<FeatureFlag | null>(null);
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [experimentAnalysis, setExperimentAnalysis] = useState<ExperimentAnalysis | null>(null);
  const [experimentRecommendation, setExperimentRecommendation] =
    useState<ExperimentRecommendation | null>(null);
  const [rollouts, setRollouts] = useState<ProgressiveRolloutRecord[]>([]);
  const [repository, setRepository] = useState<Repository | null>(null);

  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<
    "overview" | "report" | "approvals" | "artifacts" | "timeline"
  >("overview");
  const [selectedStageOrder, setSelectedStageOrder] = useState<number>(1);
  const [reportDrawerOpen, setReportDrawerOpen] = useState(false);
  const [timelineFilter, setTimelineFilter] = useState("");
  const [sseConnected, setSseConnected] = useState(false);
  const [liveSseFeed, setLiveSseFeed] = useState<LiveSseItem[]>([]);
  const [contextWarning, setContextWarning] = useState<string | null>(null);
  const [cleanupBusy, setCleanupBusy] = useState<"pr" | "delete" | null>(null);
  const [cleanupError, setCleanupError] = useState<string | null>(null);
  const [cleanupBlockers, setCleanupBlockers] = useState<string[]>([]);
  const [cleanupNotice, setCleanupNotice] = useState<string | null>(null);
  const [cleanupPrOutcome, setCleanupPrOutcome] = useState<MutationOutcome<CodeChange> | null>(null);
  const [deleteApproval, setDeleteApproval] = useState<Approval | null>(null);
  const [deleteEnv, setDeleteEnv] = useState<string | null>(null);
  const [deleteAction, setDeleteAction] = useState<"archive" | "delete">("archive");

  const loadLinkedContext = useCallback(async (relDetail: ReleaseDetail) => {
    const rel = relDetail.release;
    const contextFailures: string[] = [];

    const [exeRes, riskRes, rolloutRes, repoRes] = await Promise.all([
      listAgentExecutions({ release_id: rel.id, limit: 20 }).catch((err) => {
        contextFailures.push(`Executions: ${getErrorMessage(err)}`);
        return { items: [] };
      }),
      listRiskAssessments({ release_id: rel.id, current_only: true, limit: 5 }).catch((err) => {
        contextFailures.push(`Risk assessments: ${getErrorMessage(err)}`);
        return { items: [] };
      }),
      listRollouts({ release_id: rel.id, limit: 5 }).catch((err) => {
        contextFailures.push(`Rollouts: ${getErrorMessage(err)}`);
        return { items: [] };
      }),
      rel.repository_id
        ? listRepositories({ limit: 50 }).catch((err) => {
            contextFailures.push(`Repositories: ${getErrorMessage(err)}`);
            return { items: [] };
          })
        : Promise.resolve({ items: [] }),
    ]);

    setExecutions(Array.isArray(exeRes.items) ? exeRes.items : []);
    setRiskAssessment(Array.isArray(riskRes.items) && riskRes.items.length > 0 ? riskRes.items[0] : null);
    setRollouts(Array.isArray(rolloutRes.items) ? rolloutRes.items : []);
    if (rel.repository_id && Array.isArray(repoRes.items)) {
      setRepository(repoRes.items.find((r) => r.id === rel.repository_id) ?? null);
    }

    const extraPromises: Promise<void>[] = [];

    if (rel.pull_request_id) {
      extraPromises.push(
        getPullRequest(rel.pull_request_id)
          .then(setPullRequest)
          .catch((err) => {
            setPullRequest(null);
            contextFailures.push(`Pull request: ${getErrorMessage(err)}`);
          }),
      );
    } else {
      setPullRequest(null);
    }

    if (rel.feature_flag_id) {
      extraPromises.push(
        listFeatureFlags({ limit: 100 })
          .then((res) => {
            const found = res.items.find((f) => f.id === rel.feature_flag_id) ?? null;
            setFeatureFlag(found);
          })
          .catch((err) => {
            setFeatureFlag(null);
            contextFailures.push(`Feature flag: ${getErrorMessage(err)}`);
          }),
      );
    } else {
      setFeatureFlag(null);
    }

    if (rel.experiment_id) {
      extraPromises.push(
        Promise.all([
          listExperiments().catch((err) => {
            contextFailures.push(`Experiments: ${getErrorMessage(err)}`);
            return { items: [] };
          }),
          getExperimentAnalysis(rel.experiment_id).catch((err) => {
            if (!(err instanceof ApiError && err.status === 404)) {
              contextFailures.push(`Experiment analysis: ${getErrorMessage(err)}`);
            }
            return null;
          }),
          getExperimentRecommendation(rel.experiment_id).catch((err) => {
            if (!(err instanceof ApiError && err.status === 404)) {
              contextFailures.push(`Experiment recommendation: ${getErrorMessage(err)}`);
            }
            return null;
          }),
        ]).then(([expList, analysis, rec]) => {
          setExperiment(expList.items.find((e) => e.id === rel.experiment_id) ?? null);
          setExperimentAnalysis(analysis);
          setExperimentRecommendation(rec);
        }),
      );
    } else {
      setExperiment(null);
      setExperimentAnalysis(null);
      setExperimentRecommendation(null);
    }

    await Promise.all(extraPromises);
    setContextWarning(
      contextFailures.length > 0
        ? `Some linked release context failed to load — ${contextFailures.join("; ")}`
        : null,
    );
  }, []);

  const loadDetail = useCallback(async () => {
    if (!releaseId) return;
    try {
      const res = await getReleaseDetail(releaseId);
      setDetail(res);
      setError(null);
      await loadLinkedContext(res);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [releaseId, loadLinkedContext]);

  useEffect(() => {
    if (!releaseId) return;
    let active = true;
    getReleaseDetail(releaseId)
      .then((res) => {
        if (!active) return;
        setDetail(res);
        setError(null);
        void loadLinkedContext(res);
      })
      .catch((err) => {
        if (!active) return;
        setError(getErrorMessage(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [releaseId, loadLinkedContext]);

  const activeTaskId = useMemo(() => {
    const activeExe = executions.find(
      (ex) =>
        ex.agent_task_id &&
        ["queued", "initializing", "planning", "running", "tool_running", "waiting_for_approval"].includes(
          ex.status,
        ),
    );
    return activeExe?.agent_task_id ?? executions.find((ex) => ex.agent_task_id)?.agent_task_id ?? null;
  }, [executions]);

  const appendSseItem = useCallback((eventType: string, message: string) => {
    setLiveSseFeed((prev) => [
      {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        eventType,
        message,
        timestamp: new Date().toISOString(),
      },
      ...prev.slice(0, 14),
    ]);
  }, []);

  // Real SSE subscriptions: release events + agent executions stream
  useEffect(() => {
    if (!releaseId || typeof EventSource === "undefined") return;

    const releaseEs = new EventSource(`${API_BASE_URL}/api/v1/events/stream`, {
      withCredentials: true,
    });
    releaseEs.onopen = () => setSseConnected(true);
    releaseEs.onerror = () => setSseConnected(false);

    const onReleaseEvent = (eventName: string) => (evt: MessageEvent) => {
      try {
        const parsed = JSON.parse(evt.data);
        if (parsed?.release_id === releaseId) {
          appendSseItem(eventName, parsed?.title || parsed?.message || "Release state updated");
          void loadDetail();
        }
      } catch {
        // Ignore malformed SSE payloads
      }
    };
    releaseEs.addEventListener("release.updated", onReleaseEvent("release.updated"));
    releaseEs.addEventListener("release.readiness_updated", onReleaseEvent("release.readiness_updated"));
    releaseEs.addEventListener("release.approval_requested", onReleaseEvent("release.approval_requested"));

    const execEs = new EventSource(`${API_BASE_URL}/api/v1/agent-executions/stream`, {
      withCredentials: true,
    });
    execEs.onopen = () => setSseConnected(true);
    execEs.onerror = () => setSseConnected(false);
    const onExecEvent = (eventName: string) => (evt: MessageEvent) => {
      try {
        const parsed = JSON.parse(evt.data);
        if (parsed?.release_id === releaseId) {
          appendSseItem(eventName, parsed?.current_activity || parsed?.message || "Agent execution updated");
          void loadDetail();
        }
      } catch {
        // Ignore malformed SSE payloads
      }
    };
    execEs.addEventListener("execution.created", onExecEvent("execution.created"));
    execEs.addEventListener("execution.updated", onExecEvent("execution.updated"));
    execEs.addEventListener("execution.decision", onExecEvent("execution.decision"));

    return () => {
      releaseEs.close();
      execEs.close();
    };
  }, [releaseId, loadDetail, appendSseItem]);

  // Real SSE subscription for active agent task stream (9 SSE event types)
  useEffect(() => {
    if (!activeTaskId || typeof EventSource === "undefined") return;
    const taskEs = new EventSource(`${API_BASE_URL}/api/v1/agent/tasks/${activeTaskId}/stream`, {
      withCredentials: true,
    });
    taskEs.onopen = () => setSseConnected(true);
    taskEs.onerror = () => setSseConnected(false);
    const eventTypes = [
      "task.started",
      "agent.thinking",
      "agent.tool_proposed",
      "policy.evaluated",
      "approval.required",
      "tool.executed",
      "agent.completed",
      "task.failed",
      "task.cancelled",
    ];
    const listeners = eventTypes.map((evName) => {
      const fn = (evt: MessageEvent) => {
        try {
          const parsed = JSON.parse(evt.data);
          appendSseItem(
            evName,
            parsed?.message || parsed?.tool_name || parsed?.summary || `Received ${evName}`,
          );
          if (
            ["tool.executed", "approval.required", "agent.completed", "task.failed", "task.cancelled"].includes(
              evName,
            )
          ) {
            void loadDetail();
          }
        } catch {
          // Ignore malformed SSE payloads
        }
      };
      taskEs.addEventListener(evName, fn);
      return { evName, fn };
    });

    return () => {
      listeners.forEach(({ evName, fn }) => taskEs.removeEventListener(evName, fn));
      taskEs.close();
    };
  }, [activeTaskId, loadDetail, appendSseItem]);

  const releaseStatus = detail?.release.status;
  useEffect(() => {
    if (sseConnected || !releaseId) return;
    if (releaseStatus && ["completed", "rolled_back", "cancelled"].includes(releaseStatus)) {
      return;
    }
    const timer = setInterval(() => {
      void loadDetail();
    }, 15000);
    return () => clearInterval(timer);
  }, [sseConnected, releaseId, releaseStatus, loadDetail]);

  const runAction = async (key: string, fn: () => Promise<unknown>, successMsg: string) => {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      await fn();
      setNotice(successMsg);
      await loadDetail();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const lifecycleStages = useMemo(() => {
    if (!detail) return [];
    return buildNineLifecycleStages({
      detail,
      pullRequest,
      riskAssessment,
      featureFlag,
      experiment,
      experimentAnalysis,
      experimentRecommendation,
      rollouts,
      executions,
    });
  }, [
    detail,
    pullRequest,
    riskAssessment,
    featureFlag,
    experiment,
    experimentAnalysis,
    experimentRecommendation,
    rollouts,
    executions,
  ]);

  const selectedStage =
    lifecycleStages.find((s) => s.order === selectedStageOrder) ?? lifecycleStages[0] ?? null;

  if (loading) {
    return (
      <div className="space-y-4">
        <LoadingSkeleton rows={6} />
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="space-y-4">
        <ErrorBanner error={error || "Release not found."} onDismiss={() => setError(null)} />
        <Link
          href="/releases"
          className="inline-flex items-center gap-1.5 text-xs text-indigo-300 hover:underline"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Back to Releases
        </Link>
      </div>
    );
  }

  const { release, checks, events, artifacts, approvals } = detail;
  const isTerminal = ["completed", "rolled_back", "cancelled"].includes(release.status);
  const filteredEvents = timelineFilter.trim()
    ? events.filter((e) =>
        `${e.event_type} ${e.title} ${e.message ?? ""}`
          .toLowerCase()
          .includes(timelineFilter.trim().toLowerCase()),
      )
    : events;

  const handleStageAction = (actionKey: string) => {
    if (actionKey === "analyze") {
      void runAction("analyze", () => analyzeRelease(release.id), "PR risk & static analysis completed.");
    } else if (actionKey === "validate") {
      void runAction("validate", () => validateRelease(release.id), "Isolated patch validation completed.");
    } else if (actionKey === "readiness") {
      void runAction(
        "readiness",
        () => evaluateReleaseReadiness(release.id),
        "Readiness score and verification checks updated.",
      );
    } else if (actionKey === "risk") {
      void runAction(
        "risk",
        () =>
          createRiskAssessment({
            release_id: release.id,
            pull_request_id: release.pull_request_id ?? undefined,
            repository_id: release.repository_id ?? undefined,
            target_environment: release.target_environment,
          }),
        "7-dimension Risk Assessment completed.",
      );
    } else if (actionKey === "approval") {
      void runAction(
        "approval",
        () => requestReleaseApproval(release.id),
        "Approval request submitted for human review.",
      );
    } else if (actionKey === "tab:approvals") {
      setActiveTab("approvals");
    } else if (actionKey === "open:report") {
      setReportDrawerOpen(true);
    }
  };

  const hasSyntheticSimulation = Boolean(
    experiment?.name?.toLowerCase().includes("sim") ||
      experimentAnalysis?.analytics_mode === "tracked_event_fallback" ||
      release.metadata_json?.demo_mode === true ||
      release.metadata_json?.synthetic === true,
  );

  const renderFinalReleaseReportBody = () => (
    <div className="space-y-5 text-xs">
      {/* Epistemic Separation Legend */}
      <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-emerald-300">Verified Facts</span>
            <EpistemicBadge kind="Verified Fact" />
          </div>
          <p className="mt-1 text-[11px] text-slate-400">
            Deterministic repo/PR metadata, Commit SHA, validation checks, policy approvals, rollout %.
          </p>
        </div>
        <div className="rounded-lg border border-sky-500/30 bg-sky-500/5 p-3">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-sky-300">Observations</span>
            <EpistemicBadge kind="Observation" />
          </div>
          <p className="mt-1 text-[11px] text-slate-400">
            Live Flagsmith states, experiment telemetry rates, guardrail counts, audit events.
          </p>
        </div>
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-amber-300">Simulations</span>
            <EpistemicBadge kind="Simulation" />
          </div>
          <p className="mt-1 text-[11px] text-slate-400">
            {hasSyntheticSimulation
              ? "Synthetic demo traffic or test harness telemetry detected — never treated as prod."
              : "No synthetic simulation traffic recorded for this release."}
          </p>
        </div>
        <div className="rounded-lg border border-indigo-500/30 bg-indigo-500/5 p-3">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-indigo-300">AI Inferences</span>
            <EpistemicBadge kind="AI Inference" />
          </div>
          <p className="mt-1 text-[11px] text-slate-400">
            LLM risk hypotheses, AI release summary, and AI rollout/experiment recommendations.
          </p>
        </div>
      </div>

      {/* 1-4: Repo, PR, Commit SHA, Code Changes */}
      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3.5 space-y-2">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-slate-100">1. Repository & 2. Pull Request</span>
            <EpistemicBadge kind="Verified Fact" />
          </div>
          <div className="space-y-1 text-slate-300">
            <div>
              <span className="text-slate-400">Repo:</span>{" "}
              <span className="font-mono">{repository?.full_name ?? release.repository_id ?? "Not linked"}</span>
            </div>
            <div>
              <span className="text-slate-400">PR:</span>{" "}
              {pullRequest ? (
                <a
                  href={pullRequest.html_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-mono text-indigo-300 hover:underline"
                >
                  #{pullRequest.github_pr_number} · {pullRequest.title}
                </a>
              ) : (
                <span>{release.pull_request_id ?? "Not linked"}</span>
              )}
            </div>
            <div>
              <span className="text-slate-400">3. Commit SHA:</span>{" "}
              <span className="font-mono text-slate-100">
                {pullRequest?.head_sha || riskAssessment?.commit_sha || "Not recorded"}
              </span>
            </div>
          </div>
        </div>

        <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3.5 space-y-2">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-slate-100">4. Code Changes</span>
            <EpistemicBadge kind="Verified Fact" />
          </div>
          {pullRequest?.code_changes && pullRequest.code_changes.length > 0 ? (
            <ul className="space-y-1.5 text-slate-300">
              {pullRequest.code_changes.map((cc) => (
                <li key={cc.id} className="flex items-center justify-between gap-2">
                  <span>
                    <span className="font-mono text-indigo-300">{cc.flag_key}</span> — {cc.title}
                  </span>
                  <StatusBadge status={cc.validation_status} />
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-slate-400">
              {pullRequest?.changed_files
                ? `${pullRequest.changed_files.length} changed file(s) in PR.`
                : "No generated code patches recorded."}
            </p>
          )}
        </div>
      </div>

      {/* 5: Risk */}
      <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3.5 space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="font-semibold text-slate-100">5. Risk Assessment</span>
          <div className="flex items-center gap-1.5">
            <EpistemicBadge kind="Verified Fact" />
            <EpistemicBadge kind="AI Inference" />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <RiskLevelBadge level={release.risk_level} />
          {riskAssessment && (
            <span className="font-mono text-slate-300">
              7-Dim Score: {riskAssessment.risk_score ?? "—"}/100 ({riskAssessment.assessment_key})
            </span>
          )}
        </div>
        {pullRequest && (
          <div className="grid gap-2 pt-1 md:grid-cols-2">
            <div>
              <div className="mb-1 text-[11px] font-semibold text-slate-300">
                Confirmed Risks ({pullRequest.confirmed_risks?.length ?? 0})
              </div>
              {pullRequest.confirmed_risks?.length ? (
                <ul className="list-disc pl-4 text-slate-400">
                  {pullRequest.confirmed_risks.map((r, i) => (
                    <li key={i}>
                      <span className="text-slate-200">{r.title}</span> ({r.file_path})
                    </li>
                  ))}
                </ul>
              ) : (
                <span className="text-slate-500">None recorded.</span>
              )}
            </div>
            <div>
              <div className="mb-1 text-[11px] font-semibold text-slate-300">
                Potential Risks / Hypotheses ({pullRequest.potential_risks?.length ?? 0})
              </div>
              {pullRequest.potential_risks?.length ? (
                <ul className="list-disc pl-4 text-slate-400">
                  {pullRequest.potential_risks.map((r, i) => (
                    <li key={i}>
                      <span className="text-slate-200">{r.title}</span> — {r.assumption}
                    </li>
                  ))}
                </ul>
              ) : (
                <span className="text-slate-500">None recorded.</span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* 6 & 7: Flags & Experiment Config / Results */}
      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3.5 space-y-2">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-slate-100">6. Feature Flags</span>
            <EpistemicBadge kind="Observation" />
          </div>
          {featureFlag ? (
            <div className="space-y-1 text-slate-300">
              <div>
                Key: <span className="font-mono text-indigo-300">{featureFlag.key}</span> ({featureFlag.flag_type})
              </div>
              <div>Health Score: {featureFlag.health_score}/100</div>
              <div className="flex flex-wrap gap-1.5 pt-1">
                {Object.entries(featureFlag.env_states ?? {}).map(([env, st]) => (
                  <span
                    key={env}
                    className="rounded border border-slate-700 bg-slate-900 px-2 py-0.5 font-mono text-[10px]"
                  >
                    {env}: {st.enabled ? "ON" : "OFF"}
                    {st.rollout_percentage !== null && st.rollout_percentage !== undefined
                      ? ` (${st.rollout_percentage}%)`
                      : ""}
                  </span>
                ))}
              </div>
            </div>
          ) : (
            <p className="text-slate-400">No feature flag linked.</p>
          )}
        </div>

        <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3.5 space-y-2">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-slate-100">7. Experiment Config & Results</span>
            <EpistemicBadge kind={hasSyntheticSimulation ? "Simulation" : "Observation"} />
          </div>
          {experiment ? (
            <div className="space-y-1 text-slate-300">
              <div className="font-medium text-slate-100">
                {experiment.name} <StatusBadge status={experiment.status} />
              </div>
              <div className="text-slate-400">Hypothesis: {experiment.hypothesis}</div>
              {experimentAnalysis ? (
                <div className="grid grid-cols-2 gap-2 pt-1 font-mono text-[11px]">
                  <div>Control: {formatRate(experimentAnalysis.control.mean_rate)}</div>
                  <div>Treatment: {formatRate(experimentAnalysis.treatment.mean_rate)}</div>
                  <div>
                    P(Beat): {formatPct(experimentAnalysis.comparison.prob_treatment_beats_control)}
                  </div>
                  <div>
                    Sample: {experimentAnalysis.sample_adequacy.is_adequate ? "Adequate" : "Insufficient"}
                  </div>
                </div>
              ) : (
                <div className="text-slate-500">Telemetry analysis not yet available.</div>
              )}
            </div>
          ) : (
            <p className="text-slate-400">No experiment linked.</p>
          )}
        </div>
      </div>

      {/* 8 & 9: Guardrails & AI Recommendation */}
      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3.5 space-y-2">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-slate-100">8. Guardrails</span>
            <EvidenceBadge
              evidenceClass={
                experimentAnalysis?.guardrails?.payment_failed
                  ? hasSyntheticSimulation
                    ? "SIMULATED"
                    : "OBSERVED"
                  : checks.some((c) => c.is_blocking)
                    ? "VERIFIED"
                    : "UNKNOWN"
              }
            />
          </div>
          {experimentAnalysis?.guardrails?.payment_failed ? (
            <div className="space-y-1 text-slate-300">
              <div>
                Payment Error Breach:{" "}
                <span
                  className={
                    experimentAnalysis.guardrails.payment_failed.breached
                      ? "font-semibold text-rose-300"
                      : "font-semibold text-emerald-300"
                  }
                >
                  {experimentAnalysis.guardrails.payment_failed.breached ? "BREACHED" : "Within Threshold"}
                </span>
              </div>
              <div className="font-mono text-[11px] text-slate-400">
                Control: {formatRate(experimentAnalysis.guardrails.payment_failed.control_rate)} · Treatment:{" "}
                {formatRate(experimentAnalysis.guardrails.payment_failed.treatment_rate)}
              </div>
            </div>
          ) : (
            <p className="text-slate-400">
              Blocking readiness gates: {checks.filter((c) => c.is_blocking && c.status === "failed").length} failed.
            </p>
          )}
        </div>

        <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3.5 space-y-2">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-slate-100">9. AI Recommendation</span>
            <EpistemicBadge kind="AI Inference" />
          </div>
          {experimentRecommendation && (
            <div className="font-semibold text-indigo-300">
              {experimentRecommendation.recommendation}
            </div>
          )}
          <p className="text-slate-300">
            {experimentRecommendation?.narrative ||
              experimentRecommendation?.rationale ||
              release.ai_summary ||
              "No AI recommendation recorded yet."}
          </p>
        </div>
      </div>

      {/* 10, 11, 12: Human Approval, Rollout, Verification */}
      <div className="grid gap-3 md:grid-cols-3">
        <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3.5 space-y-2">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-slate-100">10. Human Approval</span>
            <EpistemicBadge kind="Verified Fact" />
          </div>
          {approvals.length === 0 ? (
            <p className="text-slate-400">No approval gates recorded.</p>
          ) : (
            <ul className="space-y-1.5 text-slate-300">
              {approvals.map((a) => (
                <li key={a.id} className="space-y-0.5">
                  <div className="flex items-center justify-between gap-1">
                    <span className="font-medium">{a.title}</span>
                    <StatusBadge status={a.status} />
                  </div>
                  <div className="font-mono text-[10px] text-slate-500">
                    Cat {a.policy_category} · hash {truncateHash(a.action_hash, 8)}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3.5 space-y-2">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-slate-100">11. Rollout</span>
            <EpistemicBadge kind="Verified Fact" />
          </div>
          <div className="font-mono text-sm font-semibold text-slate-100">
            {release.current_rollout_percentage}% / {release.target_rollout_percentage}%
          </div>
          <div className="text-slate-400">
            Target env: <span className="font-mono text-slate-200">{release.target_environment}</span>
          </div>
          {rollouts[0] && (
            <div className="text-[11px] text-slate-300">
              {rollouts[0].rollout_key} · <StatusBadge status={rollouts[0].status} />
            </div>
          )}
        </div>

        <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3.5 space-y-2">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-slate-100">12. Verification</span>
            <EpistemicBadge kind="Verified Fact" />
          </div>
          <div className="font-mono text-sm font-semibold text-slate-100">
            Readiness: {release.readiness_score}/100
          </div>
          <StatusBadge status={release.readiness_status} />
          <div className="text-[11px] text-slate-400">
            {checks.filter((c) => c.status === "passed").length} passed ·{" "}
            {checks.filter((c) => c.status === "failed").length} failed
          </div>
        </div>
      </div>

      {/* 13: Audit History */}
      <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3.5 space-y-2">
        <div className="flex items-center justify-between">
          <span className="font-semibold text-slate-100">
            13. Audit History ({events.length} events · {artifacts.length} artifacts)
          </span>
          <EpistemicBadge kind="Observation" />
        </div>
        {events.length === 0 ? (
          <p className="text-slate-400">No audit events recorded yet.</p>
        ) : (
          <div className="max-h-56 space-y-1.5 overflow-y-auto pr-1">
            {events.slice(0, 20).map((ev) => (
              <div
                key={ev.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded border border-slate-800/80 bg-[#090c12] px-2.5 py-1.5"
              >
                <div>
                  <span className="font-medium text-slate-200">{ev.title}</span>
                  {ev.message && <span className="ml-2 text-slate-400">{ev.message}</span>}
                </div>
                <span className="font-mono text-[10px] text-slate-500">
                  {ev.event_type} · {formatTimestamp(ev.created_at)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <Link href="/releases" className="inline-flex items-center gap-1 hover:text-slate-200">
          <ArrowLeft className="h-3.5 w-3.5" />
          Releases
        </Link>
        <span>/</span>
        <span className="font-mono text-indigo-300">{release.release_key}</span>
      </div>

      <PageHeader
        title={`${release.release_key}: ${release.title}`}
        description={
          release.description ||
          `Managed ${release.release_type} release targeting ${release.target_environment} (v${release.version}).`
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium ${
                sseConnected
                  ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                  : "border-slate-700 bg-slate-900 text-slate-400"
              }`}
            >
              {sseConnected ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
              {sseConnected ? "Live SSE" : "SSE Reconnecting"}
            </span>
            <button
              type="button"
              onClick={() => setReportDrawerOpen(true)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-500/40 bg-indigo-500/10 px-3 py-1.5 text-xs font-medium text-indigo-200 hover:bg-indigo-500/20"
            >
              <FileText className="h-3.5 w-3.5" />
              Final Release Report
            </button>
            <Link
              href="/progressive-delivery"
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800"
            >
              Progressive Delivery
            </Link>
            <button
              type="button"
              onClick={() => void loadDetail()}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-800"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Refresh
            </button>
            <a
              href={getReleaseReportExportUrl(release.id)}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-800"
            >
              <Download className="h-3.5 w-3.5" />
              Export Markdown
            </a>
          </div>
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={release.status} />
        <EnvironmentBadge environment={release.target_environment} />
        <RiskLevelBadge level={release.risk_level} />
        <StatusBadge status={release.readiness_status} />
        <span className="rounded-full border border-slate-700 bg-slate-900 px-2.5 py-0.5 font-mono text-[11px] text-slate-300">
          Readiness: {release.readiness_score}/100
        </span>
        <span className="rounded-full border border-slate-700 bg-slate-900 px-2.5 py-0.5 font-mono text-[11px] text-slate-300">
          v{release.version}
        </span>
      </div>

      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {contextWarning && (
        <ErrorBanner error={contextWarning} onDismiss={() => setContextWarning(null)} />
      )}
      <SuccessBanner message={notice} onDismiss={() => setNotice(null)} />

      {/* Live SSE Activity Feed (only when real SSE events arrive) */}
      {liveSseFeed.length > 0 && (
        <SectionCard
          title="Live Execution Stream"
          subtitle="Real-time SSE events received from backend"
          actions={
            <button
              type="button"
              onClick={() => setLiveSseFeed([])}
              className="text-[11px] text-slate-400 hover:text-slate-200"
            >
              Clear
            </button>
          }
        >
          <div className="max-h-36 space-y-1.5 overflow-y-auto text-xs">
            {liveSseFeed.map((item) => (
              <div
                key={item.id}
                className="flex items-center justify-between gap-2 rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-1.5"
              >
                <div className="flex items-center gap-2">
                  <span className="rounded border border-sky-500/30 bg-sky-500/10 px-1.5 py-0.5 font-mono text-[10px] text-sky-300">
                    {item.eventType}
                  </span>
                  <span className="text-slate-200">{item.message}</span>
                </div>
                <span className="font-mono text-[10px] text-slate-500">
                  {formatTimestamp(item.timestamp)}
                </span>
              </div>
            ))}
          </div>
        </SectionCard>
      )}

      {/* Operator Controls */}
      {!isTerminal && (
        <SectionCard
          title="Lifecycle Controls"
          subtitle="Execute stages, evaluate readiness, or manage release state"
        >
          <div className="flex flex-wrap gap-2">
            {release.pull_request_id && (
              <>
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() =>
                    void runAction(
                      "analyze",
                      () => analyzeRelease(release.id),
                      "PR risk & static analysis completed.",
                    )
                  }
                  className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
                >
                  <Sparkles className="h-3.5 w-3.5" />
                  {busy === "analyze" ? "Analyzing..." : "Analyze PR"}
                </button>
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() =>
                    void runAction(
                      "validate",
                      () => validateRelease(release.id),
                      "Isolated patch validation completed.",
                    )
                  }
                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800 disabled:opacity-50"
                >
                  <FileCode2 className="h-3.5 w-3.5" />
                  {busy === "validate" ? "Validating..." : "Validate Patch"}
                </button>
              </>
            )}

            <button
              type="button"
              disabled={busy !== null}
              onClick={() =>
                void runAction(
                  "readiness",
                  () => evaluateReleaseReadiness(release.id),
                  "Readiness score and verification checks updated.",
                )
              }
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800 disabled:opacity-50"
            >
              <ShieldCheck className="h-3.5 w-3.5 text-emerald-400" />
              {busy === "readiness" ? "Evaluating..." : "Evaluate Readiness"}
            </button>

            <button
              type="button"
              disabled={busy !== null}
              onClick={() =>
                void runAction(
                  "risk",
                  () =>
                    createRiskAssessment({
                      release_id: release.id,
                      pull_request_id: release.pull_request_id ?? undefined,
                      repository_id: release.repository_id ?? undefined,
                      target_environment: release.target_environment,
                    }),
                  "Intelligent Risk Assessment completed (view in Risk Intelligence).",
                )
              }
              className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-500/40 bg-indigo-500/10 px-3 py-1.5 text-xs font-medium text-indigo-200 hover:bg-indigo-500/20 disabled:opacity-50"
            >
              <ShieldCheck className="h-3.5 w-3.5 text-indigo-400" />
              {busy === "risk" ? "Assessing Risk..." : "Run Risk Assessment"}
            </button>

            <button
              type="button"
              disabled={busy !== null}
              onClick={() =>
                void runAction(
                  "approval",
                  () => requestReleaseApproval(release.id),
                  "Approval request submitted for human review.",
                )
              }
              className="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs font-semibold text-amber-200 hover:bg-amber-500/20 disabled:opacity-50"
            >
              <CheckCircle2 className="h-3.5 w-3.5" />
              {busy === "approval" ? "Requesting..." : "Request Approval"}
            </button>

            {release.status === "paused" ? (
              <button
                type="button"
                disabled={busy !== null}
                onClick={() =>
                  void runAction(
                    "resume",
                    () => resumeRelease(release.id, { expected_version: release.version }),
                    "Release resumed.",
                  )
                }
                className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-200 hover:bg-emerald-500/20"
              >
                <Play className="h-3.5 w-3.5" />
                Resume
              </button>
            ) : (
              <button
                type="button"
                disabled={busy !== null}
                onClick={() =>
                  void runAction(
                    "pause",
                    () => pauseRelease(release.id, { expected_version: release.version }),
                    "Release paused.",
                  )
                }
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800"
              >
                <Pause className="h-3.5 w-3.5" />
                Pause
              </button>
            )}

            {(release.status === "failed" || release.status === "blocked") && (
              <button
                type="button"
                disabled={busy !== null}
                onClick={() =>
                  void runAction(
                    "retry",
                    () => retryRelease(release.id, { expected_version: release.version }),
                    "Release retried from failed stage.",
                  )
                }
                className="inline-flex items-center gap-1.5 rounded-lg border border-sky-500/40 bg-sky-500/10 px-3 py-1.5 text-xs font-medium text-sky-200 hover:bg-sky-500/20"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                Retry Failed Stage
              </button>
            )}

            <button
              type="button"
              disabled={busy !== null}
              onClick={() =>
                void runAction(
                  "cancel",
                  () => cancelRelease(release.id, { expected_version: release.version }),
                  "Release cancelled.",
                )
              }
              className="inline-flex items-center gap-1.5 rounded-lg border border-rose-500/35 bg-rose-500/10 px-3 py-1.5 text-xs font-medium text-rose-200 hover:bg-rose-500/20"
            >
              <XCircle className="h-3.5 w-3.5" />
              Cancel
            </button>
          </div>
        </SectionCard>
      )}

      {/* 9-Stage Visual Release Lifecycle */}
      <SectionCard
        title="9-Stage Visual Release Lifecycle"
        subtitle="Click any stage to inspect timestamps, responsible agent, evidence, warnings, and next action"
      >
        <div className="grid gap-2.5 sm:grid-cols-3 lg:grid-cols-3 xl:grid-cols-9">
          {lifecycleStages.map((st) => {
            const isSelected = selectedStage?.order === st.order;
            return (
              <button
                key={st.key}
                type="button"
                onClick={() => setSelectedStageOrder(st.order)}
                className={`rounded-lg border p-2.5 text-left text-xs transition-colors ${
                  isSelected
                    ? "border-indigo-500 bg-indigo-500/10 ring-1 ring-indigo-500/40"
                    : "border-slate-800 bg-[#0d1119] hover:border-slate-700"
                }`}
              >
                <div className="mb-1.5 flex items-center justify-between gap-1">
                  <span className="font-mono text-[10px] text-slate-400">#{st.order}</span>
                  <StatusBadge status={st.badgeStatus} label={st.state} />
                </div>
                <div className="font-semibold text-slate-100">{st.title}</div>
                <div className="mt-1 truncate text-[10px] text-slate-400">{st.agent}</div>
                {st.warnings.length > 0 && (
                  <div className="mt-1 inline-flex items-center gap-1 text-[10px] text-amber-300">
                    <AlertTriangle className="h-3 w-3" />
                    {st.warnings.length} warning{st.warnings.length > 1 ? "s" : ""}
                  </div>
                )}
              </button>
            );
          })}
        </div>

        {/* Selected Stage Inspector */}
        {selectedStage && (
          <div className="mt-4 rounded-xl border border-slate-800 bg-[#0d1119] p-4 text-xs space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/80 pb-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs text-indigo-300">Stage #{selectedStage.order}</span>
                <h3 className="text-sm font-semibold text-slate-100">{selectedStage.title}</h3>
                <StatusBadge status={selectedStage.badgeStatus} label={selectedStage.state} />
              </div>
              <div className="flex items-center gap-1.5 text-[11px] text-slate-300">
                <Bot className="h-3.5 w-3.5 text-indigo-400" />
                <span>{selectedStage.agent}</span>
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="rounded-lg border border-slate-800/80 bg-[#090c12] p-2.5">
                <div className="text-[10px] uppercase tracking-wider text-slate-500">Started</div>
                <div className="mt-1 flex items-center gap-1 font-mono text-[11px] text-slate-200">
                  <Clock className="h-3 w-3 text-slate-400" />
                  {formatTimestamp(selectedStage.startedAt)}
                </div>
              </div>
              <div className="rounded-lg border border-slate-800/80 bg-[#090c12] p-2.5">
                <div className="text-[10px] uppercase tracking-wider text-slate-500">Completed</div>
                <div className="mt-1 flex items-center gap-1 font-mono text-[11px] text-slate-200">
                  <Clock className="h-3 w-3 text-slate-400" />
                  {formatTimestamp(selectedStage.completedAt)}
                </div>
              </div>
              <div className="rounded-lg border border-slate-800/80 bg-[#090c12] p-2.5 sm:col-span-2">
                <div className="text-[10px] uppercase tracking-wider text-slate-500">Stage Summary</div>
                <p className="mt-1 text-slate-200">{selectedStage.summary}</p>
              </div>
            </div>

            <div className="grid gap-3 lg:grid-cols-2">
              <div className="rounded-lg border border-slate-800/80 bg-[#090c12] p-3">
                <div className="mb-1.5 text-[11px] font-semibold text-slate-200">Evidence</div>
                {selectedStage.evidence.length === 0 ? (
                  <p className="text-slate-500">No stage evidence recorded yet.</p>
                ) : (
                  <ul className="list-disc space-y-1 pl-4 text-slate-300">
                    {selectedStage.evidence.map((item, idx) => (
                      <li key={idx}>{item}</li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="rounded-lg border border-slate-800/80 bg-[#090c12] p-3 space-y-2">
                <div className="text-[11px] font-semibold text-slate-200">Warnings & Next Action</div>
                {selectedStage.warnings.length > 0 && (
                  <div className="rounded border border-amber-500/30 bg-amber-500/10 p-2 text-amber-200">
                    <ul className="list-disc pl-4 space-y-0.5">
                      {selectedStage.warnings.map((w, idx) => (
                        <li key={idx}>{w}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <p className="text-slate-400">{selectedStage.nextAction.description}</p>
                {selectedStage.nextAction.actionKey && (
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => handleStageAction(selectedStage.nextAction.actionKey!)}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-500/40 bg-indigo-500/15 px-3 py-1.5 text-xs font-medium text-indigo-200 hover:bg-indigo-500/25 disabled:opacity-50"
                  >
                    {selectedStage.nextAction.label}
                    <ArrowRight className="h-3.5 w-3.5" />
                  </button>
                )}
                {selectedStage.nextAction.href && (
                  <Link
                    href={selectedStage.nextAction.href}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-500/40 bg-indigo-500/15 px-3 py-1.5 text-xs font-medium text-indigo-200 hover:bg-indigo-500/25"
                  >
                    {selectedStage.nextAction.label}
                    <ArrowRight className="h-3.5 w-3.5" />
                  </Link>
                )}
              </div>
            </div>
          </div>
        )}
      </SectionCard>

      {/* Readiness & Checks */}
      <SectionCard
        title={`Readiness Score: ${release.readiness_score}/100`}
        subtitle="Deterministic verification checks & blocking gate evaluation"
        actions={<StatusBadge status={release.readiness_status} />}
      >
        {release.readiness_summary?.blocking_issues &&
          release.readiness_summary.blocking_issues.length > 0 && (
            <div className="mb-3 rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-200">
              <div className="mb-1 flex items-center gap-1.5 font-semibold">
                <AlertTriangle className="h-3.5 w-3.5" />
                Blocking Issues
              </div>
              <ul className="list-disc pl-5">
                {release.readiness_summary.blocking_issues.map((msg, idx) => (
                  <li key={idx}>{msg}</li>
                ))}
              </ul>
            </div>
          )}

        <div className="grid gap-2.5 md:grid-cols-2 lg:grid-cols-3">
          {checks.map((chk) => (
            <div
              key={chk.id}
              className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 text-xs"
            >
              <div className="mb-1.5 flex items-center justify-between gap-2">
                <span className="font-semibold text-slate-100">{chk.title}</span>
                <StatusBadge status={chk.status} />
              </div>
              <p className="text-[11px] text-slate-400">{chk.message || "—"}</p>
              <div className="mt-2 flex items-center justify-between font-mono text-[10px] text-slate-500">
                <span>weight: {chk.weight}</span>
                <span>{chk.is_blocking ? "blocking gate" : "advisory"}</span>
              </div>
            </div>
          ))}
        </div>
      </SectionCard>

      {/* Tabs */}
      <div className="flex flex-wrap gap-2 border-b border-slate-800 pb-2 text-xs">
        {(
          [
            ["overview", "Overview & Links"],
            ["report", "Final Release Report"],
            ["approvals", `Approvals (${approvals.length})`],
            ["artifacts", `Artifacts (${artifacts.length})`],
            ["timeline", `Timeline (${events.length})`],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setActiveTab(key)}
            className={`rounded-lg px-3 py-1.5 font-medium ${
              activeTab === key
                ? "border border-indigo-500/40 bg-indigo-500/20 text-indigo-200"
                : "text-slate-400 hover:bg-slate-900 hover:text-slate-200"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {activeTab === "overview" && (
        <div className="grid gap-4 lg:grid-cols-2">
          <SectionCard title="AI Summary & Rollout Scope">
            <p className="text-xs leading-relaxed text-slate-300">
              {release.ai_summary || "No AI summary recorded yet. Run Analyze PR to generate one."}
            </p>
            <div className="mt-4 grid grid-cols-2 gap-3 text-xs">
              <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3">
                <div className="text-slate-400">Rollout Progress</div>
                <div className="mt-1 font-mono text-sm font-semibold text-slate-100">
                  {release.current_rollout_percentage}% / {release.target_rollout_percentage}%
                </div>
              </div>
              <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3">
                <div className="text-slate-400">Created</div>
                <div className="mt-1 font-mono text-xs text-slate-200">
                  {formatTimestamp(release.created_at)}
                </div>
              </div>
            </div>
          </SectionCard>

          <SectionCard title="Linked Resources">
            <div className="space-y-2 text-xs">
              <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2">
                <span className="flex items-center gap-1.5 text-slate-400">
                  <GitPullRequest className="h-3.5 w-3.5" />
                  Pull Request
                </span>
                {pullRequest ? (
                  <Link href="/pull-requests" className="font-mono text-indigo-300 hover:underline">
                    #{pullRequest.github_pr_number} · {truncateHash(pullRequest.head_sha, 7)}
                  </Link>
                ) : release.pull_request_id ? (
                  <Link href="/pull-requests" className="font-mono text-indigo-300 hover:underline">
                    {release.pull_request_id.slice(0, 8)}...
                  </Link>
                ) : (
                  <span className="text-slate-500">None</span>
                )}
              </div>
              <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2">
                <span className="flex items-center gap-1.5 text-slate-400">
                  <ShieldAlert className="h-3.5 w-3.5" />
                  Feature Flag
                </span>
                {featureFlag ? (
                  <Link href="/flagsmith" className="font-mono text-indigo-300 hover:underline">
                    {featureFlag.key}
                  </Link>
                ) : release.feature_flag_id ? (
                  <Link href="/flagsmith" className="font-mono text-indigo-300 hover:underline">
                    {release.feature_flag_id.slice(0, 8)}...
                  </Link>
                ) : (
                  <span className="text-slate-500">None</span>
                )}
              </div>
              <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2">
                <span className="flex items-center gap-1.5 text-slate-400">
                  <FlaskConical className="h-3.5 w-3.5" />
                  Experiment
                </span>
                {experiment ? (
                  <Link href="/experiments" className="font-mono text-indigo-300 hover:underline">
                    {experiment.name}
                  </Link>
                ) : release.experiment_id ? (
                  <Link href="/experiments" className="font-mono text-indigo-300 hover:underline">
                    {release.experiment_id.slice(0, 8)}...
                  </Link>
                ) : (
                  <span className="text-slate-500">None</span>
                )}
              </div>
              <div className="border-t border-slate-800 pt-2">
                <div className="mb-1.5 text-[11px] font-semibold text-slate-300">
                  Agent Executions ({executions.length})
                </div>
                {executions.length === 0 ? (
                  <span className="text-slate-500">No agent executions linked yet.</span>
                ) : (
                  <div className="space-y-1.5">
                    {executions.map((ex) => (
                      <Link
                        key={ex.id}
                        href={`/command-center/${ex.id}`}
                        className="flex items-center justify-between rounded-lg border border-slate-800 bg-[#0d1119] px-2.5 py-1.5 hover:border-indigo-500/40"
                      >
                        <span className="font-mono text-indigo-300">
                          {ex.execution_key} · {ex.agent_name}
                        </span>
                        <StatusBadge status={ex.status} />
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </SectionCard>

          {/* Screen 6: Release Completion & Flag Cleanup */}
          <SectionCard
            title="Release Completion & Flag Cleanup"
            subtitle="Flag lifecycle status, cleanup Draft PR generation, and Category C Flagsmith archive/delete"
            className="lg:col-span-2"
            actions={
              featureFlag ? (
                <StatusBadge
                  status={featureFlag.lifecycle_status ?? "active"}
                  label={(featureFlag.lifecycle_status ?? "active").toUpperCase()}
                />
              ) : undefined
            }
          >
            {!featureFlag ? (
              <p className="text-xs text-slate-400">
                No feature flag is linked to this release. Link a feature flag to track post-release cleanup and retirement.
              </p>
            ) : (
              (() => {
                const lifecycleStatus = (featureFlag.lifecycle_status ?? "active").toUpperCase();
                const details = (featureFlag.lifecycle_details ?? {}) as Record<string, unknown>;
                const detailBlockers = Array.isArray(details.blockers)
                  ? details.blockers.map((b) => String(b))
                  : [];
                const prUrl =
                  (typeof cleanupPrOutcome?.data?.draft_pr_url === "string" &&
                    cleanupPrOutcome.data.draft_pr_url) ||
                  (typeof details.cleanup_pr_url === "string" && details.cleanup_pr_url) ||
                  null;
                const prNumber =
                  cleanupPrOutcome?.data?.draft_pr_number ??
                  (typeof details.cleanup_pr_number === "number" ? details.cleanup_pr_number : null);
                const approvalId =
                  deleteApproval?.id ||
                  (typeof details.cleanup_approval_id === "string" ? details.cleanup_approval_id : null);
                const repoId = release.repository_id ?? featureFlag.repository_id ?? null;
                const envOptions = Array.from(
                  new Set([
                    ...(Object.keys(featureFlag.env_states ?? {})),
                    release.target_environment || "development",
                    "development",
                    "staging",
                    "production",
                  ]),
                );

                const handleGenerateCleanupPr = async () => {
                  if (!repoId) {
                    setCleanupError("No repository is linked to this release or feature flag.");
                    return;
                  }
                  setCleanupBusy("pr");
                  setCleanupError(null);
                  setCleanupBlockers([]);
                  setCleanupNotice(null);
                  try {
                    const outcome = await generateCleanupPr(featureFlag.id, {
                      repository_id: repoId,
                      keep_branch: "enabled",
                      open_draft_pr: true,
                    });
                    setCleanupPrOutcome(outcome);
                    if (outcome.execution_status === "approval_required" && outcome.approval) {
                      setCleanupNotice(
                        `Cleanup Draft PR requires approval (${outcome.approval.title}).`,
                      );
                    } else if (outcome.data?.draft_pr_url) {
                      setCleanupNotice(
                        `Cleanup Draft PR #${outcome.data.draft_pr_number ?? ""} created.`,
                      );
                    } else {
                      setCleanupNotice("Cleanup patch generated and validated.");
                    }
                    await loadDetail();
                  } catch (err) {
                    setCleanupError(getErrorMessage(err));
                  } finally {
                    setCleanupBusy(null);
                  }
                };

                const effectiveDeleteEnv =
                  deleteEnv ?? (release.target_environment || "development");

                const handleRequestDelete = async () => {
                  setCleanupBusy("delete");
                  setCleanupError(null);
                  setCleanupBlockers([]);
                  setCleanupNotice(null);
                  try {
                    const outcome = await requestFlagDelete(featureFlag.id, {
                      action: deleteAction,
                      environment_name: effectiveDeleteEnv,
                      reason: `Post-release ${deleteAction} requested from Release ${release.release_key} (${effectiveDeleteEnv})`,
                    });
                    if (outcome.approval) {
                      setDeleteApproval(outcome.approval);
                    }
                    setCleanupNotice(
                      `Category C ${deleteAction} approval queued for '${featureFlag.key}' (${effectiveDeleteEnv}).`,
                    );
                    await loadDetail();
                  } catch (err) {
                    if (err instanceof ApiError && err.status === 409) {
                      setCleanupError(`[${err.code}] ${err.message}`);
                      const rawDetails = err.details ?? {};
                      const blockersList: string[] = [];
                      if (typeof rawDetails.remaining_code_references === "number" && rawDetails.remaining_code_references > 0) {
                        blockersList.push(
                          `${rawDetails.remaining_code_references} code reference(s) still remain in the repository.`,
                        );
                      }
                      if (Array.isArray(rawDetails.blockers)) {
                        rawDetails.blockers.forEach((b) => blockersList.push(String(b)));
                      }
                      if (blockersList.length === 0) {
                        blockersList.push(err.message);
                      }
                      setCleanupBlockers(blockersList);
                    } else {
                      setCleanupError(getErrorMessage(err));
                    }
                  } finally {
                    setCleanupBusy(null);
                  }
                };

                return (
                  <div className="space-y-3 text-xs">
                    <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2.5">
                      <div className="space-y-0.5">
                        <div className="text-slate-400">
                          Feature Flag: <span className="font-mono text-indigo-300">{featureFlag.key}</span>
                        </div>
                        <div className="text-[11px] text-slate-400">
                          Lifecycle Status:{" "}
                          <span className="font-mono font-semibold text-slate-200">{lifecycleStatus}</span>
                          {featureFlag.code_references?.length > 0 && (
                            <span className="ml-2 text-slate-500">
                              · {featureFlag.code_references.length} code ref(s)
                            </span>
                          )}
                        </div>
                      </div>
                      <StatusBadge status={featureFlag.lifecycle_status ?? "active"} label={lifecycleStatus} />
                    </div>

                    {lifecycleStatus === "PROMOTED" && detailBlockers.length > 0 && (
                      <div className="rounded-lg border border-amber-500/35 bg-amber-500/10 p-3 text-amber-200">
                        <div className="mb-1 flex items-center gap-1.5 font-semibold">
                          <AlertTriangle className="h-3.5 w-3.5" />
                          Cleanup Blockers ({detailBlockers.length})
                        </div>
                        <ul className="list-disc space-y-0.5 pl-4">
                          {detailBlockers.map((blocker, idx) => (
                            <li key={idx}>{blocker}</li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {cleanupError && (
                      <ErrorBanner error={cleanupError} onDismiss={() => setCleanupError(null)} />
                    )}
                    {cleanupNotice && (
                      <SuccessBanner message={cleanupNotice} onDismiss={() => setCleanupNotice(null)} />
                    )}

                    {cleanupBlockers.length > 0 && (
                      <div className="rounded-lg border border-rose-500/35 bg-rose-500/10 p-3 text-rose-200">
                        <div className="mb-1 flex items-center gap-1.5 font-semibold">
                          <ShieldAlert className="h-3.5 w-3.5" />
                          409 Precondition Blockers
                        </div>
                        <ul className="list-disc space-y-0.5 pl-4">
                          {cleanupBlockers.map((blocker, idx) => (
                            <li key={idx}>{blocker}</li>
                          ))}
                        </ul>
                      </div>
                    )}

                    <div className="grid gap-3 md:grid-cols-2">
                      {/* Generate Cleanup Draft PR */}
                      <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 space-y-2.5">
                        <div className="font-semibold text-slate-100">1. Cleanup Draft PR (Category B)</div>
                        <p className="text-[11px] text-slate-400">
                          Generate a validated patch removing flag branches and open a GitHub Draft PR.
                        </p>
                        <div className="flex flex-wrap items-center gap-2">
                          <button
                            type="button"
                            disabled={cleanupBusy !== null || !repoId || lifecycleStatus === "CLEANED_UP"}
                            onClick={() => void handleGenerateCleanupPr()}
                            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
                          >
                            <GitPullRequest className="h-3.5 w-3.5" />
                            {cleanupBusy === "pr" ? "Generating..." : "Generate Cleanup Draft PR"}
                          </button>
                          {prUrl && (
                            <a
                              href={prUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex items-center gap-1 rounded-lg border border-emerald-500/35 bg-emerald-500/10 px-2.5 py-1.5 text-[11px] font-medium text-emerald-200 hover:bg-emerald-500/20"
                            >
                              Draft PR {prNumber ? `#${prNumber}` : ""}
                              <ExternalLink className="h-3 w-3" />
                            </a>
                          )}
                        </div>
                        {cleanupPrOutcome?.execution_status === "approval_required" && cleanupPrOutcome.approval && (
                          <Link
                            href="/approvals"
                            className="inline-flex items-center gap-1 text-[11px] text-amber-300 hover:underline"
                          >
                            Review Category B Approval ({cleanupPrOutcome.approval.id.slice(0, 8)}…)
                            <ArrowRight className="h-3 w-3" />
                          </Link>
                        )}
                      </div>

                      {/* Request Flag Archive/Delete */}
                      <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 space-y-2.5">
                        <div className="font-semibold text-slate-100">2. Flag Retirement (Category C)</div>
                        <p className="text-[11px] text-slate-400">
                          Queue a mandatory human approval to archive or delete the flag in Flagsmith once cleanup PR is merged.
                        </p>
                        <div className="flex flex-wrap items-center gap-2">
                          <select
                            aria-label="Target environment for flag retirement"
                            value={effectiveDeleteEnv}
                            onChange={(e) => setDeleteEnv(e.target.value)}
                            className="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-[11px] text-slate-200"
                          >
                            {envOptions.map((env) => (
                              <option key={env} value={env}>
                                {env}
                              </option>
                            ))}
                          </select>
                          <select
                            aria-label="Flag retirement action"
                            value={deleteAction}
                            onChange={(e) => setDeleteAction(e.target.value as "archive" | "delete")}
                            className="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-[11px] text-slate-200"
                          >
                            <option value="archive">Archive</option>
                            <option value="delete">Delete</option>
                          </select>
                          <button
                            type="button"
                            disabled={cleanupBusy !== null || lifecycleStatus === "CLEANED_UP"}
                            onClick={() => void handleRequestDelete()}
                            className="inline-flex items-center gap-1.5 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 text-xs font-semibold text-rose-200 hover:bg-rose-500/20 disabled:opacity-50"
                          >
                            {deleteAction === "delete" ? (
                              <Trash2 className="h-3.5 w-3.5" />
                            ) : (
                              <Archive className="h-3.5 w-3.5" />
                            )}
                            {cleanupBusy === "delete"
                              ? "Requesting..."
                              : `Request Flag ${deleteAction === "delete" ? "Delete" : "Archive"}`}
                          </button>
                        </div>
                        {approvalId && (
                          <Link
                            href="/approvals"
                            className="inline-flex items-center gap-1 rounded-lg border border-amber-500/35 bg-amber-500/10 px-2.5 py-1 text-[11px] font-medium text-amber-200 hover:bg-amber-500/20"
                          >
                            Category C Approval Queued ({approvalId.slice(0, 8)}…) — Open Approvals
                            <ArrowRight className="h-3 w-3" />
                          </Link>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })()
            )}
          </SectionCard>
        </div>
      )}

      {activeTab === "report" && (
        <SectionCard
          title="Structured Final Release Report"
          subtitle="Explicit separation of Verified Facts, Observations, Simulations, and AI Inferences"
          actions={
            <a
              href={getReleaseReportExportUrl(release.id)}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1 text-xs text-slate-200 hover:bg-slate-800"
            >
              <Download className="h-3.5 w-3.5" />
              Export Markdown
            </a>
          }
        >
          {renderFinalReleaseReportBody()}
        </SectionCard>
      )}

      {activeTab === "approvals" && (
        <div className="space-y-3">
          {approvals.length === 0 ? (
            <SectionCard title="Approvals">
              <p className="text-xs text-slate-400">
                No approvals requested yet for this release.
              </p>
            </SectionCard>
          ) : (
            approvals.map((app) => (
              <ApprovalActionCard key={app.id} approval={app} onUpdated={() => void loadDetail()} />
            ))
          )}
        </div>
      )}

      {activeTab === "artifacts" && (
        <SectionCard title="Release Artifacts">
          {artifacts.length === 0 ? (
            <p className="text-xs text-slate-400">No artifacts recorded yet.</p>
          ) : (
            <div className="space-y-2">
              {artifacts.map((art) => (
                <div
                  key={art.id}
                  className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 text-xs"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-slate-100">{art.title}</span>
                    <span className="rounded border border-slate-700 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                      {art.artifact_type}
                    </span>
                  </div>
                  {art.summary && <p className="mt-1 text-slate-400">{art.summary}</p>}
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      )}

      {activeTab === "timeline" && (
        <SectionCard
          title="Chronological Lifecycle Timeline"
          actions={
            <input
              type="text"
              value={timelineFilter}
              onChange={(e) => setTimelineFilter(e.target.value)}
              placeholder="Filter events..."
              className="rounded-lg border border-slate-700 bg-[#0d1119] px-2.5 py-1 text-xs text-slate-100 placeholder:text-slate-500"
            />
          }
        >
          <div className="space-y-2">
            {filteredEvents.length === 0 ? (
              <p className="text-xs text-slate-400">
                {events.length === 0
                  ? "No lifecycle events recorded yet."
                  : "No lifecycle events match your filter."}
              </p>
            ) : (
              filteredEvents.map((ev) => (
                <div
                  key={ev.id}
                  className="flex flex-wrap items-start justify-between gap-2 rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2 text-xs"
                >
                  <div>
                    <div className="font-semibold text-slate-100">{ev.title}</div>
                    {ev.message && <div className="mt-0.5 text-slate-400">{ev.message}</div>}
                  </div>
                  <div className="text-right font-mono text-[10px] text-slate-500">
                    <div>{ev.event_type}</div>
                    <div>{formatTimestamp(ev.created_at)}</div>
                  </div>
                </div>
              ))
            )}
          </div>
        </SectionCard>
      )}

      {/* Slide-Over Final Release Report Drawer */}
      {reportDrawerOpen && (
        <div
          className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-xs"
          role="dialog"
          aria-modal="true"
          aria-label="Final Release Report Drawer"
        >
          <div className="flex h-full w-full max-w-4xl flex-col border-l border-slate-800 bg-[#090c12] shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-800 px-5 py-4">
              <div className="flex items-center gap-2">
                <UserCheck className="h-4 w-4 text-indigo-400" />
                <div>
                  <h2 className="text-sm font-semibold text-slate-100">
                    Final Release Report — {release.release_key}
                  </h2>
                  <p className="text-[11px] text-slate-400">
                    Verified Facts vs Observations vs Simulations vs AI Inferences
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <a
                  href={getReleaseReportExportUrl(release.id)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1 text-xs text-slate-200 hover:bg-slate-800"
                >
                  <Download className="h-3.5 w-3.5" />
                  Export Markdown
                </a>
                <button
                  type="button"
                  onClick={() => setReportDrawerOpen(false)}
                  aria-label="Close report drawer"
                  className="rounded-lg border border-slate-700 bg-slate-900 p-1.5 text-slate-300 hover:bg-slate-800"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto p-5">{renderFinalReleaseReportBody()}</div>
          </div>
        </div>
      )}
    </div>
  );
}
