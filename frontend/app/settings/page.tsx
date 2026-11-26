"use client";

import React, { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  FlaskConical,
  GitBranch,
  KeyRound,
  Save,
  Shield,
  Sliders,
  Trash2,
  Users,
} from "lucide-react";
import {
  completeGithubOAuthCallback,
  connectFlagsmith,
  connectGithubPat,
  createWorkspaceUser,
  disconnectIntegration,
  getErrorMessage,
  getWorkspaceSettings,
  listIntegrations,
  listWorkspaceUsers,
  probeFlagsmithCapability,
  startGithubOAuth,
  updateWorkspaceSettings,
} from "@/lib/api";
import type {
  AuthUser,
  GuardrailThresholds,
  HealthThresholds,
  IntegrationConnection,
  WorkspaceSettings,
} from "@/types/releaseguard";
import { formatTimestamp } from "@/lib/utils";
import {
  ErrorBanner,
  LoadingSkeleton,
  PageHeader,
  SectionCard,
  SuccessBanner,
} from "@/components/ui/StatePrimitives";
import { StatusBadge } from "@/components/StatusBadges";
import { triggerControlPlaneRefresh } from "@/hooks/use-control-plane-status";

const GEMINI_MODELS = [
  "gemini-3.5-flash-lite",
  "gemini-3.8-flash",
  "gemini-3.1-flash-lite",
];

function SettingsInner() {
  const searchParams = useSearchParams();
  const router = useRouter();

  const [settings, setSettings] = useState<WorkspaceSettings | null>(null);
  const [integrations, setIntegrations] = useState<IntegrationConnection[]>([]);
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [newUserEmail, setNewUserEmail] = useState("");
  const [newUserName, setNewUserName] = useState("");
  const [newUserPassword, setNewUserPassword] = useState("");
  const [newUserRole, setNewUserRole] = useState("engineer");

  // GitHub forms
  const [patToken, setPatToken] = useState("");
  const [patLabel, setPatLabel] = useState("");
  const [oauthPrivate, setOauthPrivate] = useState(false);

  // Flagsmith form
  const [fsOrgToken, setFsOrgToken] = useState("");
  const [fsProjectId, setFsProjectId] = useState("");
  const [fsDevKey, setFsDevKey] = useState("");
  const [fsStagingKey, setFsStagingKey] = useState("");
  const [fsProdKey, setFsProdKey] = useState("");

  // Workspace Policy & Thresholds state
  const [geminiModel, setGeminiModel] = useState("gemini-3.5-flash-lite");
  const [catBConfirm, setCatBConfirm] = useState(true);
  const [ttlSeconds, setTtlSeconds] = useState(900);
  const [guardrails, setGuardrails] = useState<GuardrailThresholds>({
    min_exposures: 100,
    max_expected_loss: 0.005,
    guardrail_max_error_rate: 0.05,
    soft_error_delta: 0.015,
    hard_error_delta: 0.03,
  });
  const [healthThresholds, setHealthThresholds] = useState<HealthThresholds>({
    stale_days: 30,
    unused_days: 14,
    always_on_days: 30,
    always_off_days: 30,
  });

  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [ws, intList, userList] = await Promise.all([
        getWorkspaceSettings(),
        listIntegrations().catch(() => []),
        listWorkspaceUsers().catch(() => []),
      ]);
      setSettings(ws);
      setIntegrations(intList.length > 0 ? intList : (ws.integrations ?? []));
      setUsers(userList);
      setGeminiModel(ws.gemini_model || "gemini-3.5-flash-lite");
      setCatBConfirm(ws.category_b_requires_confirmation);
      setTtlSeconds(ws.approval_ttl_seconds || 900);
      if (ws.default_guardrails) {
        setGuardrails((prev) => ({ ...prev, ...ws.default_guardrails }));
      }
      if (ws.health_thresholds) {
        setHealthThresholds((prev) => ({ ...prev, ...ws.health_thresholds }));
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const isCb = searchParams.get("github_callback");
    const code = searchParams.get("code");
    const state = searchParams.get("state");

    const timer = setTimeout(() => {
      if (isCb && code && state) {
        void (async () => {
          setBusyAction("oauth_cb");
          try {
            const conn = await completeGithubOAuthCallback({ code, state });
            setNotice(
              `GitHub OAuth connected (${conn.external_account_name || conn.token_hint}).`
            );
            triggerControlPlaneRefresh();
            router.replace("/settings");
          } catch (err) {
            setError(getErrorMessage(err));
          } finally {
            setBusyAction(null);
            await loadAll();
          }
        })();
      } else {
        void loadAll();
      }
    }, 0);

    return () => clearTimeout(timer);
  }, [loadAll, router, searchParams]);

  const handleStartOAuth = async () => {
    setBusyAction("oauth_start");
    setError(null);
    try {
      const res = await startGithubOAuth(oauthPrivate);
      window.location.href = res.authorize_url;
    } catch (err) {
      setError(getErrorMessage(err));
      setBusyAction(null);
    }
  };

  const handleConnectPat = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!patToken.trim()) return;
    setBusyAction("pat");
    setError(null);
    setNotice(null);
    try {
      const conn = await connectGithubPat({
        token: patToken.trim(),
        account_label: patLabel.trim() || undefined,
      });
      setPatToken("");
      setPatLabel("");
      setNotice(
        `GitHub PAT verified & encrypted (${conn.external_account_name || conn.token_hint}).`
      );
      triggerControlPlaneRefresh();
      await loadAll();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleConnectFlagsmith = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!fsOrgToken.trim() || !fsProjectId.trim()) return;
    setBusyAction("flagsmith");
    setError(null);
    setNotice(null);
    try {
      const envKeys: Record<string, string> = {};
      if (fsDevKey.trim()) envKeys.development = fsDevKey.trim();
      if (fsStagingKey.trim()) envKeys.staging = fsStagingKey.trim();
      if (fsProdKey.trim()) envKeys.production = fsProdKey.trim();

      const conn = await connectFlagsmith({
        org_token: fsOrgToken.trim(),
        project_id: Number(fsProjectId.trim()),
        environment_keys: envKeys,
      });
      setFsOrgToken("");
      setNotice(
        `Flagsmith connected & encrypted (token ${conn.token_hint}).`
      );
      triggerControlPlaneRefresh();
      await loadAll();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleProbeCapability = async () => {
    setBusyAction("probe");
    setError(null);
    setNotice(null);
    try {
      const res = await probeFlagsmithCapability({
        environment_key: fsDevKey.trim() || fsStagingKey.trim() || undefined,
      });
      setNotice(
        `Flagsmith capability probe complete: analytics_mode = ${res.analytics_mode}`
      );
      triggerControlPlaneRefresh();
      await loadAll();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleDisconnect = async (provider: string) => {
    setBusyAction(`disconnect:${provider}`);
    setError(null);
    setNotice(null);
    try {
      await disconnectIntegration(provider);
      setNotice(`Disconnected ${provider} integration and revoked stored credentials.`);
      triggerControlPlaneRefresh();
      await loadAll();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleSaveSettings = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusyAction("save_settings");
    setError(null);
    setNotice(null);
    try {
      await updateWorkspaceSettings({
        gemini_model: geminiModel,
        category_b_requires_confirmation: catBConfirm,
        approval_ttl_seconds: Number(ttlSeconds),
        default_guardrails: guardrails,
        health_thresholds: healthThresholds,
      });
      setNotice("Saved workspace policy, Gemini model, and guardrail thresholds.");
      await loadAll();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleCreateUser = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newUserEmail.trim() || !newUserName.trim() || !newUserPassword) return;
    setBusyAction("create_user");
    setError(null);
    setNotice(null);
    try {
      const created = await createWorkspaceUser({
        email: newUserEmail.trim(),
        display_name: newUserName.trim(),
        password: newUserPassword,
        role: newUserRole,
      });
      setUsers((prev) => [...prev, created]);
      setNewUserEmail("");
      setNewUserName("");
      setNewUserPassword("");
      setNotice(`Created RBAC operator ${created.email} (${created.role}).`);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const githubConn = integrations.find((i) => i.provider === "github");
  const flagsmithConn = integrations.find((i) => i.provider === "flagsmith");

  return (
    <div className="space-y-6">
      <PageHeader
        title="Integrations & Governance Settings"
        description="Connect GitHub (OAuth / Fine-Grained PAT) and Flagsmith with Fernet at-rest encryption (only masked token_hint displayed), probe Experiment capability, and configure Policy & Bayesian thresholds."
      />

      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <SuccessBanner message={notice} onDismiss={() => setNotice(null)} />

      {loading ? (
        <LoadingSkeleton rows={4} />
      ) : (
        <>
          {/* Connected Integrations Summary (masked token_hint only) */}
          <div className="grid gap-4 md:grid-cols-2">
            {[
              { provider: "github", label: "GitHub", conn: githubConn },
              { provider: "flagsmith", label: "Flagsmith", conn: flagsmithConn },
            ].map(({ provider, label, conn }) => (
              <div
                key={provider}
                className="flex flex-col justify-between rounded-xl border border-slate-800 bg-[#111622] p-4 space-y-3"
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="text-sm font-semibold text-slate-100">
                      {label} Connection
                    </div>
                    <div className="mt-0.5 text-xs text-slate-400">
                      {conn
                        ? `${conn.auth_mode.toUpperCase()} • ${conn.external_account_name || "Connected"}`
                        : "Not configured"}
                    </div>
                  </div>
                  <StatusBadge status={conn?.status || "disconnected"} />
                </div>

                {conn && (
                  <div className="space-y-1 rounded-lg border border-slate-800 bg-[#0d1119] p-3 font-mono text-[11px] text-slate-300">
                    <div>token_hint: {conn.token_hint}</div>
                    {conn.scopes?.length > 0 && (
                      <div>scopes: {conn.scopes.join(", ")}</div>
                    )}
                    <div>
                      last_verified: {formatTimestamp(conn.last_verified_at)}
                    </div>
                  </div>
                )}

                {conn && conn.status === "connected" && (
                  <div className="flex justify-end">
                    <button
                      type="button"
                      disabled={busyAction !== null}
                      onClick={() => void handleDisconnect(provider)}
                      className="inline-flex items-center gap-1 rounded-lg border border-rose-500/40 bg-rose-500/10 px-2.5 py-1 text-xs font-medium text-rose-200 hover:bg-rose-500/20 disabled:opacity-50"
                    >
                      <Trash2 className="h-3 w-3" />
                      Disconnect
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* GitHub OAuth & PAT Forms */}
          <div className="grid gap-6 lg:grid-cols-2">
            <SectionCard
              title="GitHub Authentication"
              subtitle="Least-privilege OAuth Web Flow or Fine-Grained PAT (encrypted via Fernet)"
            >
              <div className="space-y-5">
                <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3.5 space-y-3">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 text-xs font-semibold text-slate-200">
                      <GitBranch className="h-4 w-4 text-slate-300" />
                      <span>GitHub OAuth Web Flow</span>
                    </div>
                    <button
                      type="button"
                      disabled={busyAction !== null}
                      onClick={handleStartOAuth}
                      className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-semibold text-slate-100 hover:bg-slate-800 disabled:opacity-50"
                    >
                      Authorize via GitHub
                    </button>
                  </div>
                  <label className="inline-flex items-center gap-2 text-xs text-slate-400">
                    <input
                      type="checkbox"
                      checked={oauthPrivate}
                      onChange={(e) => setOauthPrivate(e.target.checked)}
                    />
                    <span>Request private repository access (`repo` scope)</span>
                  </label>
                </div>

                <form onSubmit={handleConnectPat} className="space-y-3">
                  <div className="flex items-center gap-2 text-xs font-semibold text-slate-200">
                    <KeyRound className="h-4 w-4 text-indigo-400" />
                    <span>Connect Fine-Grained PAT (Recommended)</span>
                  </div>
                  <input
                    type="password"
                    required
                    value={patToken}
                    onChange={(e) => setPatToken(e.target.value)}
                    placeholder="github_pat_... or ghp_..."
                    className="w-full rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 font-mono text-xs text-slate-100"
                  />
                  <input
                    type="text"
                    value={patLabel}
                    onChange={(e) => setPatLabel(e.target.value)}
                    placeholder="Optional account label (e.g. release-bot)"
                    className="w-full rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 text-xs text-slate-100"
                  />
                  <button
                    type="submit"
                    disabled={busyAction !== null || !patToken.trim()}
                    className="rounded-lg bg-indigo-500 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
                  >
                    {busyAction === "pat" ? "Verifying..." : "Verify & Save PAT"}
                  </button>
                </form>
              </div>
            </SectionCard>

            {/* Flagsmith Connection & Capability Probe */}
            <SectionCard
              title="Flagsmith Control & Capability Probe"
              subtitle="Management API Org Token, Project ID, and Edge Environment Keys"
              actions={
                <button
                  type="button"
                  disabled={busyAction !== null}
                  onClick={handleProbeCapability}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-cyan-500/40 bg-cyan-500/15 px-3 py-1.5 text-xs font-semibold text-cyan-200 hover:bg-cyan-500/25 disabled:opacity-50"
                >
                  <FlaskConical className="h-3.5 w-3.5" />
                  {busyAction === "probe" ? "Probing..." : "Probe Experiments API"}
                </button>
              }
            >
              <form onSubmit={handleConnectFlagsmith} className="space-y-3">
                <div className="rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2 text-xs">
                  <span className="text-slate-400">Detected Analytics Mode: </span>
                  <span className="font-mono font-semibold text-cyan-300">
                    {settings?.flagsmith_analytics_mode ?? "tracked_event_fallback"}
                  </span>
                  {settings?.flagsmith_capability_probed_at && (
                    <span className="ml-2 text-[11px] text-slate-500 tabular-nums">
                      (probed {formatTimestamp(settings.flagsmith_capability_probed_at)})
                    </span>
                  )}
                </div>

                <div className="grid gap-3 sm:grid-cols-2">
                  <input
                    type="password"
                    required
                    value={fsOrgToken}
                    onChange={(e) => setFsOrgToken(e.target.value)}
                    placeholder="Flagsmith Org API Token"
                    className="rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 font-mono text-xs text-slate-100"
                  />
                  <input
                    type="number"
                    required
                    value={fsProjectId}
                    onChange={(e) => setFsProjectId(e.target.value)}
                    placeholder="Flagsmith Project ID (numeric)"
                    className="rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 font-mono text-xs text-slate-100"
                  />
                </div>

                <div className="grid gap-2 sm:grid-cols-3">
                  <input
                    type="text"
                    value={fsDevKey}
                    onChange={(e) => setFsDevKey(e.target.value)}
                    placeholder="development env key"
                    className="rounded-lg border border-slate-700 bg-[#090c12] px-2.5 py-1.5 font-mono text-xs text-slate-100"
                  />
                  <input
                    type="text"
                    value={fsStagingKey}
                    onChange={(e) => setFsStagingKey(e.target.value)}
                    placeholder="staging env key"
                    className="rounded-lg border border-slate-700 bg-[#090c12] px-2.5 py-1.5 font-mono text-xs text-slate-100"
                  />
                  <input
                    type="text"
                    value={fsProdKey}
                    onChange={(e) => setFsProdKey(e.target.value)}
                    placeholder="production env key"
                    className="rounded-lg border border-slate-700 bg-[#090c12] px-2.5 py-1.5 font-mono text-xs text-slate-100"
                  />
                </div>

                <button
                  type="submit"
                  disabled={busyAction !== null}
                  className="rounded-lg bg-indigo-500 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
                >
                  {busyAction === "flagsmith"
                    ? "Verifying Environments..."
                    : "Save & Verify Flagsmith"}
                </button>
              </form>
            </SectionCard>
          </div>

          {/* Policy Engine, Gemini Model, Bayesian Guardrails & Flag Health Thresholds */}
          <SectionCard
            title="AI Model, Policy Gates & Analytics Thresholds"
            subtitle="Persisted in workspace_settings and logged as settings.updated"
          >
            <form onSubmit={handleSaveSettings} className="space-y-6">
              <div className="grid gap-4 md:grid-cols-3">
                <div>
                  <label className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-slate-300">
                    <Sliders className="h-3.5 w-3.5 text-indigo-400" />
                    Gemini Model (AFC Disabled)
                  </label>
                  <select
                    value={GEMINI_MODELS.includes(geminiModel) ? geminiModel : "custom"}
                    onChange={(e) => {
                      if (e.target.value !== "custom") setGeminiModel(e.target.value);
                    }}
                    className="w-full rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 font-mono text-xs text-slate-100"
                  >
                    {GEMINI_MODELS.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                    <option value="custom">Custom Model ID</option>
                  </select>
                  <input
                    type="text"
                    value={geminiModel}
                    onChange={(e) => setGeminiModel(e.target.value)}
                    className="mt-2 w-full rounded-lg border border-slate-800 bg-[#090c12] px-3 py-1.5 font-mono text-[11px] text-slate-300"
                  />
                </div>

                <div>
                  <label className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-slate-300">
                    <Shield className="h-3.5 w-3.5 text-amber-400" />
                    Category B Confirmation Gate
                  </label>
                  <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-slate-800 bg-[#0d1119] p-3 text-xs">
                    <input
                      type="checkbox"
                      checked={catBConfirm}
                      onChange={(e) => setCatBConfirm(e.target.checked)}
                      className="mt-0.5"
                    />
                    <span className="text-slate-300">
                      Require human confirmation for Category B actions (Draft PRs,
                      non-prod flag rollouts).{" "}
                      <strong className="text-rose-300">
                        Category C prod gates are always enforced.
                      </strong>
                    </span>
                  </label>
                </div>

                <div>
                  <label className="mb-1.5 block text-xs font-medium text-slate-300">
                    Approval TTL (seconds, 60–86400)
                  </label>
                  <input
                    type="number"
                    min={60}
                    max={86400}
                    value={ttlSeconds}
                    onChange={(e) => setTtlSeconds(Number(e.target.value))}
                    className="w-full rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 font-mono text-xs text-slate-100"
                  />
                </div>
              </div>

              <div className="grid gap-6 md:grid-cols-2">
                {/* Default Bayesian Guardrails */}
                <div className="rounded-xl border border-slate-800 bg-[#0d1119] p-4 space-y-3">
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
                    Default Bayesian Guardrails
                  </h4>
                  <div className="grid gap-3 sm:grid-cols-2 text-xs">
                    <label className="space-y-1">
                      <span className="text-slate-400">min_exposures</span>
                      <input
                        type="number"
                        value={guardrails.min_exposures}
                        onChange={(e) =>
                          setGuardrails({
                            ...guardrails,
                            min_exposures: Number(e.target.value),
                          })
                        }
                        className="w-full rounded border border-slate-700 bg-[#090c12] px-2.5 py-1.5 font-mono text-slate-100"
                      />
                    </label>
                    <label className="space-y-1">
                      <span className="text-slate-400">max_expected_loss</span>
                      <input
                        type="number"
                        step="0.001"
                        value={guardrails.max_expected_loss}
                        onChange={(e) =>
                          setGuardrails({
                            ...guardrails,
                            max_expected_loss: Number(e.target.value),
                          })
                        }
                        className="w-full rounded border border-slate-700 bg-[#090c12] px-2.5 py-1.5 font-mono text-slate-100"
                      />
                    </label>
                    <label className="space-y-1">
                      <span className="text-slate-400">guardrail_max_error_rate</span>
                      <input
                        type="number"
                        step="0.005"
                        value={guardrails.guardrail_max_error_rate}
                        onChange={(e) =>
                          setGuardrails({
                            ...guardrails,
                            guardrail_max_error_rate: Number(e.target.value),
                          })
                        }
                        className="w-full rounded border border-slate-700 bg-[#090c12] px-2.5 py-1.5 font-mono text-slate-100"
                      />
                    </label>
                    <label className="space-y-1">
                      <span className="text-slate-400">soft_error_delta</span>
                      <input
                        type="number"
                        step="0.005"
                        value={guardrails.soft_error_delta}
                        onChange={(e) =>
                          setGuardrails({
                            ...guardrails,
                            soft_error_delta: Number(e.target.value),
                          })
                        }
                        className="w-full rounded border border-slate-700 bg-[#090c12] px-2.5 py-1.5 font-mono text-slate-100"
                      />
                    </label>
                    <label className="space-y-1">
                      <span className="text-slate-400">hard_error_delta</span>
                      <input
                        type="number"
                        step="0.005"
                        value={guardrails.hard_error_delta}
                        onChange={(e) =>
                          setGuardrails({
                            ...guardrails,
                            hard_error_delta: Number(e.target.value),
                          })
                        }
                        className="w-full rounded border border-slate-700 bg-[#090c12] px-2.5 py-1.5 font-mono text-slate-100"
                      />
                    </label>
                  </div>
                </div>

                {/* Flag Health Thresholds */}
                <div className="rounded-xl border border-slate-800 bg-[#0d1119] p-4 space-y-3">
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
                    Flag Health Monitor Thresholds (days)
                  </h4>
                  <div className="grid gap-3 sm:grid-cols-2 text-xs">
                    <label className="space-y-1">
                      <span className="text-slate-400">stale_days</span>
                      <input
                        type="number"
                        value={healthThresholds.stale_days}
                        onChange={(e) =>
                          setHealthThresholds({
                            ...healthThresholds,
                            stale_days: Number(e.target.value),
                          })
                        }
                        className="w-full rounded border border-slate-700 bg-[#090c12] px-2.5 py-1.5 font-mono text-slate-100"
                      />
                    </label>
                    <label className="space-y-1">
                      <span className="text-slate-400">unused_days</span>
                      <input
                        type="number"
                        value={healthThresholds.unused_days}
                        onChange={(e) =>
                          setHealthThresholds({
                            ...healthThresholds,
                            unused_days: Number(e.target.value),
                          })
                        }
                        className="w-full rounded border border-slate-700 bg-[#090c12] px-2.5 py-1.5 font-mono text-slate-100"
                      />
                    </label>
                    <label className="space-y-1">
                      <span className="text-slate-400">always_on_days</span>
                      <input
                        type="number"
                        value={healthThresholds.always_on_days}
                        onChange={(e) =>
                          setHealthThresholds({
                            ...healthThresholds,
                            always_on_days: Number(e.target.value),
                          })
                        }
                        className="w-full rounded border border-slate-700 bg-[#090c12] px-2.5 py-1.5 font-mono text-slate-100"
                      />
                    </label>
                    <label className="space-y-1">
                      <span className="text-slate-400">always_off_days</span>
                      <input
                        type="number"
                        value={healthThresholds.always_off_days}
                        onChange={(e) =>
                          setHealthThresholds({
                            ...healthThresholds,
                            always_off_days: Number(e.target.value),
                          })
                        }
                        className="w-full rounded border border-slate-700 bg-[#090c12] px-2.5 py-1.5 font-mono text-slate-100"
                      />
                    </label>
                  </div>
                </div>
              </div>

              <div className="flex justify-end">
                <button
                  type="submit"
                  disabled={busyAction !== null}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-500 px-4 py-2 text-xs font-semibold text-slate-950 hover:bg-emerald-400 disabled:opacity-50"
                >
                  <Save className="h-3.5 w-3.5" />
                  {busyAction === "save_settings"
                    ? "Saving..."
                    : "Save Workspace Settings"}
                </button>
              </div>
            </form>
          </SectionCard>

          {/* RBAC Operators (visible when signed in as ADMIN or dev mode) */}
          <SectionCard
            title="RBAC Workspace Operators"
            subtitle="Role-based backend access control: ADMIN, RELEASE_MANAGER, ENGINEER, VIEWER"
            actions={<Users className="h-4 w-4 text-indigo-400" />}
          >
            <div className="grid gap-6 lg:grid-cols-2">
              <div className="space-y-2 text-xs">
                {users.length === 0 ? (
                  <p className="text-slate-400">
                    Sign in as an ADMIN operator to inspect and provision RBAC workspace roles.
                  </p>
                ) : (
                  users.map((u) => (
                    <div
                      key={u.id}
                      className="flex items-center justify-between rounded-lg border border-slate-800 bg-[#0d1119] px-3 py-2"
                    >
                      <div>
                        <div className="font-semibold text-slate-100">{u.display_name}</div>
                        <div className="font-mono text-[11px] text-slate-400">{u.email}</div>
                      </div>
                      <span className="rounded border border-indigo-500/40 bg-indigo-500/10 px-2 py-0.5 font-mono text-[10px] uppercase text-indigo-300">
                        {u.role}
                      </span>
                    </div>
                  ))
                )}
              </div>

              <form onSubmit={handleCreateUser} className="space-y-3 text-xs">
                <div className="grid gap-2 sm:grid-cols-2">
                  <input
                    type="text"
                    placeholder="Display name"
                    value={newUserName}
                    onChange={(e) => setNewUserName(e.target.value)}
                    className="rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 text-slate-100 placeholder:text-slate-500"
                  />
                  <input
                    type="email"
                    placeholder="Email address"
                    value={newUserEmail}
                    onChange={(e) => setNewUserEmail(e.target.value)}
                    className="rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 text-slate-100 placeholder:text-slate-500"
                  />
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  <input
                    type="password"
                    minLength={8}
                    placeholder="Password (min 8 chars)"
                    value={newUserPassword}
                    onChange={(e) => setNewUserPassword(e.target.value)}
                    className="rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 text-slate-100 placeholder:text-slate-500"
                  />
                  <select
                    value={newUserRole}
                    onChange={(e) => setNewUserRole(e.target.value)}
                    className="rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 font-mono text-slate-100"
                  >
                    <option value="viewer">VIEWER (read-only)</option>
                    <option value="engineer">ENGINEER / DEVELOPER</option>
                    <option value="release_manager">RELEASE_MANAGER</option>
                    <option value="admin">ADMIN</option>
                  </select>
                </div>
                <button
                  type="submit"
                  disabled={
                    busyAction !== null ||
                    !newUserEmail.trim() ||
                    !newUserName.trim() ||
                    newUserPassword.length < 8
                  }
                  className="rounded-lg bg-indigo-500 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
                >
                  {busyAction === "create_user" ? "Creating Operator..." : "Add RBAC Operator"}
                </button>
              </form>
            </div>
          </SectionCard>
        </>
      )}
    </div>
  );
}

export default function SettingsPage() {
  return (
    <Suspense fallback={<LoadingSkeleton rows={4} />}>
      <SettingsInner />
    </Suspense>
  );
}
