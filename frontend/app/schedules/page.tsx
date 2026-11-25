"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  Clock,
  Edit3,
  History,
  Pause,
  Play,
  Plus,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  Trash2,
} from "lucide-react";

import { AppShell } from "@/components/AppShell";
import { EmptyState, LoadingSkeleton, MetricCard, PageHeader, SectionCard } from "@/components/ui/StatePrimitives";
import { StatusBadge } from "@/components/StatusBadges";
import {
  createSchedule,
  deleteSchedule,
  getAllJobExecutions,
  listExperiments,
  listRepositories,
  getSchedules,
  pauseSchedule,
  previewSchedule,
  resumeSchedule,
  runScheduleNow,
  updateSchedule,
} from "@/lib/api";
import type {
  Experiment,
  JobExecutionItem,
  Repository,
  ScheduleFrequency,
  ScheduleJobType,
  SchedulePreviewResponse,
  ScheduledJobItem,
} from "@/types/releaseguard";

const JOB_TYPE_OPTIONS: Array<{ value: ScheduleJobType; label: string; desc: string }> = [
  { value: "repo_sync", label: "Repository Sync", desc: "Sync connected GitHub repos & pull requests" },
  { value: "pr_analysis", label: "PR Intelligence Scan", desc: "Analyze new/updated PRs (skips unchanged SHAs)" },
  { value: "flag_health_scan", label: "Feature Flag Health Scan", desc: "Run 7-detector stale/unused flag checks" },
  { value: "experiment_monitor", label: "Experiment Bayesian Monitor", desc: "Compute posteriors, recommendations & guardrails" },
  { value: "release_monitor", label: "Release Operations Monitor", desc: "Audit rollout drift, overdue approvals & breaches" },
  { value: "ai_maintenance_report", label: "AI Maintenance Report", desc: "Digest PR risk, stale flags & approval queues" },
  { value: "custom_agent_task", label: "Custom Natural-Language Agent", desc: "Run policy-gated ReleaseGuard agent workflow" },
];

const FREQUENCY_OPTIONS: Array<{ value: ScheduleFrequency; label: string }> = [
  { value: "once", label: "One-time" },
  { value: "interval_minutes", label: "Every N minutes" },
  { value: "hourly", label: "Hourly" },
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
  { value: "cron", label: "Custom Cron (5-field)" },
];

const TIMEZONE_PRESETS = [
  "UTC",
  "Asia/Kolkata",
  "America/New_York",
  "America/Los_Angeles",
  "Europe/London",
  "Europe/Berlin",
  "Asia/Tokyo",
  "Australia/Sydney",
];

function formatDate(raw?: string | null): string {
  if (!raw) return "—";
  try {
    return new Date(raw).toLocaleString();
  } catch {
    return raw;
  }
}

export default function SchedulesPage() {
  const [schedules, setSchedules] = useState<ScheduledJobItem[]>([]);
  const [executions, setExecutions] = useState<JobExecutionItem[]>([]);
  const [repos, setRepos] = useState<Repository[]>([]);
  const [experiments, setExperiments] = useState<Experiment[]>([]);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const [filterType, setFilterType] = useState<string>("");
  const [filterStatus, setFilterStatus] = useState<string>("");
  const [filterEnv, setFilterEnv] = useState<string>("");
  const [selectedScheduleId, setSelectedScheduleId] = useState<string>("");

  // Create / Edit Form State
  const [formOpen, setFormOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [jobType, setJobType] = useState<ScheduleJobType>("repo_sync");
  const [frequency, setFrequency] = useState<ScheduleFrequency>("daily");
  const [cronExpression, setCronExpression] = useState("0 9 * * *");
  const [intervalMinutes, setIntervalMinutes] = useState<number>(30);
  const [timezone, setTimezone] = useState<string>(() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    } catch {
      return "UTC";
    }
  });
  const [startAt, setStartAt] = useState("");
  const [endAt, setEndAt] = useState("");
  const [repoIdCfg, setRepoIdCfg] = useState("");
  const [envCfg, setEnvCfg] = useState("");
  const [expIdCfg, setExpIdCfg] = useState("");
  const [projectIdCfg, setProjectIdCfg] = useState("");
  const [nlPrompt, setNlPrompt] = useState("");
  const [maxRetries, setMaxRetries] = useState(2);
  const [retryDelay, setRetryDelay] = useState(30);
  const [timeoutSec, setTimeoutSec] = useState(120);
  const [notifySuccess, setNotifySuccess] = useState(false);
  const [notifyFailure, setNotifyFailure] = useState(true);
  const [enabled, setEnabled] = useState(true);

  const [preview, setPreview] = useState<SchedulePreviewResponse | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const enabledFilter =
        filterStatus === "enabled" ? true : filterStatus === "paused" ? false : undefined;
      const lastStatusFilter = filterStatus === "failed" ? "failed" : undefined;

      const [schedRes, execRes, repoRes, expRes] = await Promise.all([
        getSchedules({
          job_type: filterType || undefined,
          enabled: enabledFilter,
          environment: filterEnv || undefined,
          last_status: lastStatusFilter,
          limit: 100,
        }),
        getAllJobExecutions({
          scheduled_job_id: selectedScheduleId || undefined,
          job_type: filterType || undefined,
          limit: 50,
        }),
        listRepositories({ limit: 50 }).catch(() => ({ items: [] })),
        listExperiments().catch(() => ({ items: [] })),
      ]);
      setSchedules(schedRes.items);
      setExecutions(execRes.items);
      setRepos(Array.isArray(repoRes.items) ? repoRes.items : []);
      setExperiments(Array.isArray(expRes.items) ? expRes.items : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load schedules");
    } finally {
      setLoading(false);
    }
  }, [filterType, filterStatus, filterEnv, selectedScheduleId]);

  useEffect(() => {
    const t = setTimeout(() => {
      void loadData();
    }, 0);
    return () => clearTimeout(t);
  }, [loadData]);

  const resetForm = () => {
    setEditingId(null);
    setName("");
    setDescription("");
    setJobType("repo_sync");
    setFrequency("daily");
    setCronExpression("0 9 * * *");
    setIntervalMinutes(30);
    setStartAt("");
    setEndAt("");
    setRepoIdCfg("");
    setEnvCfg("");
    setExpIdCfg("");
    setProjectIdCfg("");
    setNlPrompt("");
    setMaxRetries(2);
    setRetryDelay(30);
    setTimeoutSec(120);
    setNotifySuccess(false);
    setNotifyFailure(true);
    setEnabled(true);
    setPreview(null);
  };

  const openEditForm = (s: ScheduledJobItem) => {
    setEditingId(s.id);
    setName(s.name);
    setDescription(s.description || "");
    setJobType(s.job_type);
    setFrequency(s.frequency);
    setCronExpression(s.cron_expression || "0 9 * * *");
    setIntervalMinutes(s.interval_minutes || 30);
    setTimezone(s.timezone || "UTC");
    setStartAt(s.start_at ? s.start_at.slice(0, 16) : "");
    setEndAt(s.end_at ? s.end_at.slice(0, 16) : "");
    const cfg = s.configuration || {};
    setRepoIdCfg(typeof cfg.repository_id === "string" ? cfg.repository_id : "");
    setEnvCfg(typeof cfg.environment_name === "string" ? cfg.environment_name : "");
    setExpIdCfg(typeof cfg.experiment_id === "string" ? cfg.experiment_id : "");
    setProjectIdCfg(cfg.project_id !== undefined && cfg.project_id !== null ? String(cfg.project_id) : "");
    setNlPrompt(typeof cfg.prompt === "string" ? cfg.prompt : "");
    setMaxRetries(s.retry_policy?.max_retries ?? 2);
    setRetryDelay(s.retry_policy?.retry_delay_seconds ?? 30);
    setTimeoutSec(s.retry_policy?.timeout_seconds ?? 120);
    setNotifySuccess(Boolean(s.notification_preference?.on_success));
    setNotifyFailure(s.notification_preference?.on_failure ?? true);
    setEnabled(s.enabled);
    setPreview(null);
    setFormOpen(true);
  };

  const handlePreview = async () => {
    setPreviewLoading(true);
    setError(null);
    try {
      const res = await previewSchedule({
        frequency,
        cron_expression: frequency === "cron" || frequency === "daily" || frequency === "weekly" || frequency === "hourly" || frequency === "monthly" ? cronExpression : null,
        interval_minutes: frequency === "interval_minutes" ? intervalMinutes : null,
        timezone,
        start_at: startAt ? new Date(startAt).toISOString() : null,
        end_at: endAt ? new Date(endAt).toISOString() : null,
        nl_prompt: jobType === "custom_agent_task" && nlPrompt.trim() ? nlPrompt.trim() : null,
      });
      setPreview(res);
      if (res.nl_plan) {
        if (typeof res.nl_plan.frequency === "string") {
          setFrequency(res.nl_plan.frequency as ScheduleFrequency);
        }
        if (typeof res.nl_plan.cron_expression === "string") {
          setCronExpression(res.nl_plan.cron_expression);
        }
        if (typeof res.nl_plan.interval_minutes === "number") {
          setIntervalMinutes(res.nl_plan.interval_minutes);
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Schedule preview failed");
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    setBanner(null);

    const configuration: Record<string, unknown> = {};
    if (repoIdCfg) configuration.repository_id = repoIdCfg;
    if (envCfg) configuration.environment_name = envCfg;
    if (expIdCfg) configuration.experiment_id = expIdCfg;
    if (projectIdCfg.trim() && !Number.isNaN(Number(projectIdCfg.trim()))) {
      configuration.project_id = Number(projectIdCfg.trim());
    }
    if (jobType === "custom_agent_task" && nlPrompt.trim()) {
      configuration.prompt = nlPrompt.trim();
    }

    const payload = {
      name: name.trim(),
      description: description.trim() || null,
      job_type: jobType,
      frequency,
      cron_expression: frequency === "cron" ? cronExpression.trim() : undefined,
      interval_minutes: frequency === "interval_minutes" ? intervalMinutes : undefined,
      timezone: timezone.trim() || "UTC",
      start_at: startAt ? new Date(startAt).toISOString() : null,
      end_at: endAt ? new Date(endAt).toISOString() : null,
      configuration,
      retry_policy: {
        max_retries: maxRetries,
        retry_delay_seconds: retryDelay,
        timeout_seconds: timeoutSec,
      },
      notification_preference: {
        on_success: notifySuccess,
        on_failure: notifyFailure,
      },
      enabled,
    };

    try {
      if (editingId) {
        await updateSchedule(editingId, payload);
        setBanner("Schedule updated in PostgreSQL.");
      } else {
        await createSchedule(payload);
        setBanner("Scheduled automation job created.");
      }
      setFormOpen(false);
      resetForm();
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save schedule");
    } finally {
      setSubmitting(false);
    }
  };

  const handleAction = async (
    id: string,
    action: "pause" | "resume" | "run" | "delete"
  ) => {
    setBusyId(id);
    setError(null);
    setBanner(null);
    try {
      if (action === "pause") {
        await pauseSchedule(id);
        setBanner("Schedule paused.");
      } else if (action === "resume") {
        await resumeSchedule(id);
        setBanner("Schedule resumed.");
      } else if (action === "run") {
        const res = await runScheduleNow(id);
        setBanner(
          `Manual run finished (${res.execution_status}). Execution ID: ${res.id.slice(0, 8)}`
        );
      } else if (action === "delete") {
        await deleteSchedule(id);
        setBanner("Schedule deleted. Historical execution logs were preserved.");
        if (selectedScheduleId === id) setSelectedScheduleId("");
      }
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setBusyId(null);
    }
  };

  const stats = useMemo(() => {
    const active = schedules.filter((s) => s.enabled).length;
    const paused = schedules.filter((s) => !s.enabled).length;
    const attention = schedules.filter((s) => s.last_status === "failed" || s.consecutive_failures > 0).length;
    const succeededExecs = executions.filter((e) => e.execution_status === "completed").length;
    return { total: schedules.length, active, paused, attention, succeededExecs };
  }, [schedules, executions]);

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader
          title="Scheduled Automation"
          description="PostgreSQL-persisted recurring & one-time release engineering jobs with IANA timezone conversion, duplicate lease protection, retry policies, and immutable execution history."
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => void loadData()}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-medium text-slate-200 hover:bg-slate-800"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                Refresh
              </button>
              <button
                type="button"
                onClick={() => {
                  resetForm();
                  setFormOpen((v) => !v);
                }}
                className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3.5 py-2 text-xs font-semibold text-white hover:bg-indigo-400"
              >
                <Plus className="h-3.5 w-3.5" />
                {formOpen ? "Close Builder" : "New Schedule"}
              </button>
            </div>
          }
        />

        {/* Policy guardrail banner */}
        <div className="flex items-start gap-2.5 rounded-xl border border-indigo-500/25 bg-indigo-950/20 px-4 py-2.5 text-xs text-indigo-200">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-indigo-400" />
          <span>
            <strong>Policy Guarantee:</strong> Scheduled jobs run under the PolicyEngine. Category B/C mutations (including production Flagsmith changes or Draft PR actions) ALWAYS pause for explicit human sign-off in Approvals—automation cannot self-approve, merge PRs, or execute shell commands.
          </span>
        </div>

        {error && (
          <div className="flex items-center gap-2 rounded-xl border border-rose-500/30 bg-rose-950/30 px-4 py-3 text-xs text-rose-200">
            <AlertTriangle className="h-4 w-4 shrink-0 text-rose-400" />
            <span>{error}</span>
          </div>
        )}
        {banner && (
          <div className="flex items-center gap-2 rounded-xl border border-emerald-500/30 bg-emerald-950/30 px-4 py-3 text-xs text-emerald-200">
            <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" />
            <span>{banner}</span>
          </div>
        )}

        {/* KPI Cards */}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <MetricCard label="Total Schedules" value={stats.total} icon={<CalendarClock className="h-4 w-4" />} />
          <MetricCard label="Enabled Jobs" value={stats.active} icon={<Play className="h-4 w-4 text-emerald-400" />} />
          <MetricCard label="Paused Jobs" value={stats.paused} icon={<Pause className="h-4 w-4 text-slate-400" />} />
          <MetricCard
            label="Attention Needed"
            value={stats.attention}
            tone={stats.attention > 0 ? "rose" : "default"}
            icon={<AlertTriangle className={`h-4 w-4 ${stats.attention > 0 ? "text-rose-400" : "text-slate-400"}`} />}
          />
          <MetricCard label="Recent Succeeded Runs" value={stats.succeededExecs} icon={<History className="h-4 w-4 text-cyan-400" />} />
        </div>

        {/* Create / Edit Builder */}
        {formOpen && (
          <SectionCard
            title={editingId ? "Edit Scheduled Job" : "Create Scheduled Automation Job"}
            subtitle="Configure task handler, frequency/cron, IANA timezone, retries, and inspect exact next 5 run timestamps before saving"
          >
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="grid gap-4 md:grid-cols-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Schedule Name *</label>
                  <input
                    required
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="e.g. Nightly NovaShop PR Scan"
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Job Type *</label>
                  <select
                    value={jobType}
                    onChange={(e) => setJobType(e.target.value as ScheduleJobType)}
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100"
                  >
                    {JOB_TYPE_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">IANA Timezone *</label>
                  <input
                    list="tz-presets"
                    value={timezone}
                    onChange={(e) => setTimezone(e.target.value)}
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100"
                  />
                  <datalist id="tz-presets">
                    {TIMEZONE_PRESETS.map((tz) => (
                      <option key={tz} value={tz} />
                    ))}
                  </datalist>
                </div>
              </div>

              <div className="grid gap-4 md:grid-cols-4">
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Frequency Mode</label>
                  <select
                    value={frequency}
                    onChange={(e) => setFrequency(e.target.value as ScheduleFrequency)}
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100"
                  >
                    {FREQUENCY_OPTIONS.map((f) => (
                      <option key={f.value} value={f.value}>
                        {f.label}
                      </option>
                    ))}
                  </select>
                </div>

                {frequency === "interval_minutes" ? (
                  <div>
                    <label className="mb-1 block text-xs font-medium text-slate-300">Interval (Minutes)</label>
                    <input
                      type="number"
                      min={1}
                      max={1440}
                      value={intervalMinutes}
                      onChange={(e) => setIntervalMinutes(Number(e.target.value))}
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100"
                    />
                  </div>
                ) : frequency === "cron" ? (
                  <div>
                    <label className="mb-1 block text-xs font-medium text-slate-300">5-Field Cron</label>
                    <input
                      value={cronExpression}
                      onChange={(e) => setCronExpression(e.target.value)}
                      placeholder="30 9 * * 1-5"
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs text-slate-100"
                    />
                  </div>
                ) : null}

                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Start Date / Time</label>
                  <input
                    type="datetime-local"
                    value={startAt}
                    onChange={(e) => setStartAt(e.target.value)}
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">End Date (Optional)</label>
                  <input
                    type="datetime-local"
                    value={endAt}
                    onChange={(e) => setEndAt(e.target.value)}
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100"
                  />
                </div>
              </div>

              {/* Target scoping */}
              <div className="grid gap-4 md:grid-cols-4">
                {(jobType === "repo_sync" || jobType === "pr_analysis" || jobType === "flag_health_scan") && (
                  <div>
                    <label className="mb-1 block text-xs font-medium text-slate-300">Target Repository</label>
                    <select
                      value={repoIdCfg}
                      onChange={(e) => setRepoIdCfg(e.target.value)}
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100"
                    >
                      <option value="">All connected repositories</option>
                      {repos.map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.full_name}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                {(jobType === "experiment_monitor" || jobType === "release_monitor") && (
                  <div>
                    <label className="mb-1 block text-xs font-medium text-slate-300">Environment Filter</label>
                    <input
                      value={envCfg}
                      onChange={(e) => setEnvCfg(e.target.value)}
                      placeholder="e.g. staging or all"
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100"
                    />
                  </div>
                )}
                {jobType === "experiment_monitor" && (
                  <div>
                    <label className="mb-1 block text-xs font-medium text-slate-300">Experiment</label>
                    <select
                      value={expIdCfg}
                      onChange={(e) => setExpIdCfg(e.target.value)}
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100"
                    >
                      <option value="">All running experiments</option>
                      {experiments.map((ex) => (
                        <option key={ex.id} value={ex.id}>
                          {ex.name} ({ex.environment_name})
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                {jobType === "flag_health_scan" && (
                  <div>
                    <label className="mb-1 block text-xs font-medium text-slate-300">Flagsmith Project ID</label>
                    <input
                      value={projectIdCfg}
                      onChange={(e) => setProjectIdCfg(e.target.value)}
                      placeholder="Optional numeric project ID"
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100"
                    />
                  </div>
                )}
              </div>

              {jobType === "custom_agent_task" && (
                <div>
                  <label className="mb-1 flex items-center gap-1.5 text-xs font-medium text-slate-300">
                    <Sparkles className="h-3.5 w-3.5 text-indigo-400" />
                    Natural-Language Schedule & Agent Prompt
                  </label>
                  <textarea
                    rows={3}
                    value={nlPrompt}
                    onChange={(e) => setNlPrompt(e.target.value)}
                    placeholder="e.g. Every weekday at 9am check all open high-risk PRs and scan flag health without touching production"
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100"
                  />
                </div>
              )}

              <div className="grid gap-4 md:grid-cols-5">
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Max Retries</label>
                  <input
                    type="number"
                    min={0}
                    max={5}
                    value={maxRetries}
                    onChange={(e) => setMaxRetries(Number(e.target.value))}
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Retry Delay (s)</label>
                  <input
                    type="number"
                    min={1}
                    max={3600}
                    value={retryDelay}
                    onChange={(e) => setRetryDelay(Number(e.target.value))}
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Timeout (s)</label>
                  <input
                    type="number"
                    min={5}
                    max={1800}
                    value={timeoutSec}
                    onChange={(e) => setTimeoutSec(Number(e.target.value))}
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100"
                  />
                </div>
                <div className="flex items-end gap-4 pb-2 md:col-span-2">
                  <label className="flex items-center gap-1.5 text-xs text-slate-300">
                    <input
                      type="checkbox"
                      checked={notifyFailure}
                      onChange={(e) => setNotifyFailure(e.target.checked)}
                    />
                    Notify on Failure
                  </label>
                  <label className="flex items-center gap-1.5 text-xs text-slate-300">
                    <input
                      type="checkbox"
                      checked={notifySuccess}
                      onChange={(e) => setNotifySuccess(e.target.checked)}
                    />
                    Notify on Success
                  </label>
                  <label className="flex items-center gap-1.5 text-xs text-slate-300">
                    <input
                      type="checkbox"
                      checked={enabled}
                      onChange={(e) => setEnabled(e.target.checked)}
                    />
                    Enabled
                  </label>
                </div>
              </div>

              {preview && (
                <div className="rounded-xl border border-cyan-500/30 bg-cyan-950/20 p-3 text-xs text-cyan-100 space-y-2">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-semibold">{preview.human_description}</span>
                    {preview.cron_expression && (
                      <code className="rounded bg-slate-900 px-2 py-0.5 text-[11px] text-cyan-300">
                        cron: {preview.cron_expression}
                      </code>
                    )}
                  </div>
                  <div className="grid gap-1.5 sm:grid-cols-5">
                    {preview.next_runs.map((run, idx) => (
                      <div key={idx} className="rounded border border-slate-800 bg-slate-950/80 p-2 text-[11px]">
                        <div className="text-slate-300">{run.local}</div>
                        <div className="font-mono text-[10px] text-slate-500">{run.utc.slice(0, 19)}Z</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
                <button
                  type="button"
                  disabled={previewLoading}
                  onClick={() => void handlePreview()}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-cyan-500/40 bg-cyan-500/10 px-3 py-2 text-xs font-medium text-cyan-200 hover:bg-cyan-500/20"
                >
                  <Clock className="h-3.5 w-3.5" />
                  {previewLoading ? "Calculating..." : "Preview Next 5 Run Times"}
                </button>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setFormOpen(false);
                      resetForm();
                    }}
                    className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-300 hover:bg-slate-800"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={submitting}
                    className="rounded-lg bg-indigo-500 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-60"
                  >
                    {submitting ? "Saving..." : editingId ? "Save Changes" : "Create Schedule"}
                  </button>
                </div>
              </div>
            </form>
          </SectionCard>
        )}

        {/* Filter Bar */}
        <SectionCard
          title="Filter Schedules & History"
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={filterType}
                onChange={(e) => setFilterType(e.target.value)}
                className="rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200"
              >
                <option value="">All Job Types</option>
                {JOB_TYPE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
              <select
                value={filterStatus}
                onChange={(e) => setFilterStatus(e.target.value)}
                className="rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200"
              >
                <option value="">All States</option>
                <option value="enabled">Enabled</option>
                <option value="paused">Paused</option>
                <option value="failed">Last Run Failed</option>
              </select>
              <input
                value={filterEnv}
                onChange={(e) => setFilterEnv(e.target.value)}
                placeholder="Environment..."
                className="rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 w-32"
              />
            </div>
          }
        >
          {loading ? (
            <LoadingSkeleton rows={4} />
          ) : schedules.length === 0 ? (
            <EmptyState
              icon={<CalendarClock className="h-5 w-5" />}
              title="No matching schedules"
              description="Create a recurring or one-time automation schedule to sync repos, analyze PRs, scan flag health, or monitor experiments."
              action={
                <button
                  type="button"
                  onClick={() => {
                    resetForm();
                    setFormOpen(true);
                  }}
                  className="rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white"
                >
                  Create First Schedule
                </button>
              }
            />
          ) : (
            <div className="divide-y divide-slate-800/80">
              {schedules.map((s) => (
                <div key={s.id} className="flex flex-col gap-3 py-3 lg:flex-row lg:items-center lg:justify-between">
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold text-slate-100">{s.name}</span>
                      <StatusBadge status={s.enabled ? "active" : "paused"} />
                      {s.last_status && <StatusBadge status={s.last_status} />}
                      <span className="rounded bg-slate-800 px-2 py-0.5 text-[10px] font-mono text-cyan-300">
                        {s.job_type}
                      </span>
                      {s.consecutive_failures > 0 && (
                        <span className="rounded bg-rose-500/15 px-2 py-0.5 text-[10px] text-rose-300">
                          {s.consecutive_failures} consecutive failure(s)
                        </span>
                      )}
                      {s.missed_runs_count > 0 && (
                        <span className="rounded bg-amber-500/15 px-2 py-0.5 text-[10px] text-amber-300">
                          {s.missed_runs_count} missed run(s)
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-slate-300">{s.human_description}</p>
                    <div className="flex flex-wrap gap-4 text-[11px] text-slate-400">
                      <span>Next Run: {formatDate(s.next_run_at)}</span>
                      <span>Last Run: {formatDate(s.last_run_at)}</span>
                      <span>TZ: {s.timezone}</span>
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      disabled={busyId === s.id}
                      onClick={() => void handleAction(s.id, "run")}
                      className="inline-flex items-center gap-1 rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2.5 py-1.5 text-xs font-medium text-emerald-200 hover:bg-emerald-500/20"
                    >
                      <Play className="h-3 w-3" /> Run Now
                    </button>
                    {s.enabled ? (
                      <button
                        type="button"
                        disabled={busyId === s.id}
                        onClick={() => void handleAction(s.id, "pause")}
                        className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-200 hover:bg-slate-800"
                      >
                        <Pause className="h-3 w-3" /> Pause
                      </button>
                    ) : (
                      <button
                        type="button"
                        disabled={busyId === s.id}
                        onClick={() => void handleAction(s.id, "resume")}
                        className="inline-flex items-center gap-1 rounded-md border border-indigo-500/40 bg-indigo-500/10 px-2.5 py-1.5 text-xs text-indigo-200 hover:bg-indigo-500/20"
                      >
                        <Play className="h-3 w-3" /> Resume
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => openEditForm(s)}
                      className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-200 hover:bg-slate-800"
                    >
                      <Edit3 className="h-3 w-3" /> Edit
                    </button>
                    <button
                      type="button"
                      onClick={() => setSelectedScheduleId((cur) => (cur === s.id ? "" : s.id))}
                      className={`inline-flex items-center gap-1 rounded-md border px-2.5 py-1.5 text-xs ${
                        selectedScheduleId === s.id
                          ? "border-cyan-500 bg-cyan-500/20 text-cyan-200"
                          : "border-slate-700 bg-slate-900 text-slate-200 hover:bg-slate-800"
                      }`}
                    >
                      <History className="h-3 w-3" /> Logs
                    </button>
                    <button
                      type="button"
                      disabled={busyId === s.id}
                      onClick={() => void handleAction(s.id, "delete")}
                      className="inline-flex items-center gap-1 rounded-md border border-rose-500/30 bg-rose-500/10 px-2.5 py-1.5 text-xs text-rose-200 hover:bg-rose-500/20"
                    >
                      <Trash2 className="h-3 w-3" /> Delete
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </SectionCard>

        {/* Execution History */}
        <SectionCard
          title={selectedScheduleId ? "Filtered Execution History" : "Global Job Execution History"}
          subtitle="Immutable run logs preserved even when parent schedules are removed"
          actions={
            selectedScheduleId ? (
              <button
                type="button"
                onClick={() => setSelectedScheduleId("")}
                className="text-xs font-medium text-indigo-300 hover:text-indigo-200"
              >
                Show All Schedules
              </button>
            ) : null
          }
        >
          {executions.length === 0 ? (
            <EmptyState
              icon={<History className="h-5 w-5" />}
              title="No job executions recorded yet"
              description="Trigger Run Now on any schedule or wait for the next due timestamp."
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="border-b border-slate-800 text-[11px] uppercase text-slate-400">
                  <tr>
                    <th className="py-2 pr-4">Schedule</th>
                    <th className="py-2 pr-4">Type</th>
                    <th className="py-2 pr-4">Trigger</th>
                    <th className="py-2 pr-4">Status</th>
                    <th className="py-2 pr-4">Retries</th>
                    <th className="py-2 pr-4">Started</th>
                    <th className="py-2">Outcome / Details</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {executions.map((ex) => (
                    <tr key={ex.id}>
                      <td className="py-2.5 pr-4 font-medium text-slate-100">
                        {ex.schedule_name}
                        {ex.scheduled_job_id === null && (
                          <span className="ml-1.5 rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">
                            archived schedule
                          </span>
                        )}
                      </td>
                      <td className="py-2.5 pr-4 font-mono text-[11px] text-slate-300">{ex.job_type}</td>
                      <td className="py-2.5 pr-4 text-slate-300">{ex.trigger_type}</td>
                      <td className="py-2.5 pr-4">
                        <StatusBadge status={ex.execution_status} />
                      </td>
                      <td className="py-2.5 pr-4 text-slate-300">{ex.retry_count}</td>
                      <td className="py-2.5 pr-4 font-mono text-[11px] text-slate-400">
                        {formatDate(ex.started_at || ex.created_at)}
                      </td>
                      <td className="py-2.5 text-[11px] text-slate-300">
                        {ex.error_details ? (
                          <span className="text-rose-300">{ex.error_details}</span>
                        ) : (
                          <span className="font-mono text-slate-400">
                            {JSON.stringify(ex.result_summary || {})}
                          </span>
                        )}
                        {ex.agent_task_id && (
                          <Link href="/agent" className="ml-2 text-indigo-300 underline">
                            View Agent Run
                          </Link>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </SectionCard>
      </div>
    </AppShell>
  );
}
