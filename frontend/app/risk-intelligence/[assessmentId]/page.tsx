"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  GitBranch,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import {
  getErrorMessage,
  getRiskAssessmentDetail,
  reanalyzeRiskAssessment,
  updateRiskMitigation,
  verifyRiskMitigation,
} from "@/lib/api";
import type { RiskAssessmentDetail } from "@/types/releaseguard";
import { formatTimestamp } from "@/lib/utils";
import {
  ErrorBanner,
  LoadingSkeleton,
  PageHeader,
  SectionCard,
  SuccessBanner,
} from "@/components/ui/StatePrimitives";
import { EnvironmentBadge, RiskLevelBadge, StatusBadge } from "@/components/StatusBadges";

export default function RiskAssessmentDetailPage() {
  const params = useParams<{ assessmentId: string }>();
  const router = useRouter();
  const assessmentId = params?.assessmentId ?? "";

  const [detail, setDetail] = useState<RiskAssessmentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [tab, setTab] = useState<"dimensions" | "findings" | "dependencies" | "scenarios" | "mitigations">(
    "dimensions",
  );
  const [refreshTick, setRefreshTick] = useState(0);

  useEffect(() => {
    if (!assessmentId) return;
    let active = true;
    getRiskAssessmentDetail(assessmentId)
      .then((res) => {
        if (!active) return;
        setDetail(res);
        setError(null);
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
  }, [assessmentId, refreshTick]);

  const handleReanalyze = async () => {
    setBusy("reanalyze");
    setError(null);
    try {
      const next = await reanalyzeRiskAssessment(assessmentId);
      router.push(`/risk-intelligence/${next.assessment.id}`);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const handleMitigationAction = async (
    mitId: string,
    action: "accept" | "verify",
  ) => {
    setBusy(mitId);
    setError(null);
    setNotice(null);
    try {
      if (action === "accept") {
        await updateRiskMitigation(assessmentId, mitId, { status: "accepted" });
        setNotice("Mitigation accepted. Re-analyze after completing verification to update score.");
      } else {
        await verifyRiskMitigation(assessmentId, mitId, "Verified by operator in control plane.");
        setNotice("Mitigation marked completed. Click Re-analyze to recompute risk dimensions.");
      }
      setRefreshTick((n) => n + 1);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  if (loading) return <LoadingSkeleton rows={6} />;

  if (!detail) {
    return (
      <div className="space-y-4">
        <ErrorBanner error={error || "Assessment not found."} onDismiss={() => setError(null)} />
        <Link href="/risk-intelligence" className="text-xs text-indigo-300 hover:underline">
          Back to Risk Intelligence
        </Link>
      </div>
    );
  }

  const { assessment: a, dimensions, findings, evidence, mitigations, scenarios } = detail;
  const depGraph = a.dependency_graph as {
    direct_modules?: string[];
    indirect_consumers?: string[];
    edges?: Array<{ source: string; target: string }>;
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <Link href="/risk-intelligence" className="inline-flex items-center gap-1 hover:text-slate-200">
          <ArrowLeft className="h-3.5 w-3.5" />
          Risk Intelligence
        </Link>
        <span>/</span>
        <span className="font-mono text-indigo-300">{a.assessment_key}</span>
      </div>

      <PageHeader
        title={`${a.assessment_key} — Risk Score: ${a.risk_score ?? "—"}/100`}
        description={`Scoring v${a.scoring_version} · Analyzer v${a.analyzer_version} · Completeness: ${Math.round(a.data_completeness * 100)}%${
          a.assessment_confidence !== null && a.assessment_confidence !== undefined
            ? ` · Confidence: ${Math.round(a.assessment_confidence * 100)}%`
            : ""
        }`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {a.release_id && (
              <Link
                href={`/releases/${a.release_id}`}
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-800"
              >
                Open Release
              </Link>
            )}
            {a.execution_id && (
              <Link
                href={`/command-center/${a.execution_id}`}
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-800"
              >
                Command Center Trace
              </Link>
            )}
            <button
              type="button"
              disabled={busy === "reanalyze"}
              onClick={() => void handleReanalyze()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              {busy === "reanalyze" ? "Re-analyzing..." : "Re-analyze"}
            </button>
          </div>
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <RiskLevelBadge level={a.risk_level} />
        <EnvironmentBadge environment={a.target_environment} />
        <StatusBadge status={a.assessment_status} />
        <span className="rounded-full border border-slate-700 bg-slate-900 px-2.5 py-0.5 text-[11px] text-slate-300">
          Flag Suitability: {a.flag_suitability.replaceAll("_", " ")}
        </span>
        {!a.is_current && (
          <span className="rounded-full border border-amber-500/35 bg-amber-500/10 px-2.5 py-0.5 text-[11px] text-amber-200">
            Superseded / Outdated
          </span>
        )}
      </div>

      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <SuccessBanner message={notice} onDismiss={() => setNotice(null)} />

      {a.critical_overrides.length > 0 && (
        <div className="rounded-xl border border-rose-500/35 bg-rose-500/10 p-4 text-xs text-rose-200">
          <div className="mb-1 flex items-center gap-1.5 font-semibold">
            <ShieldAlert className="h-4 w-4" />
            Critical Risk Overrides Applied
          </div>
          <ul className="list-disc pl-5">
            {a.critical_overrides.map((ov, i) => (
              <li key={i}>{ov.replaceAll("_", " ")}</li>
            ))}
          </ul>
        </div>
      )}

      {a.limitations.length > 0 && (
        <SectionCard title="Assessment Limitations & Unavailable Data">
          <ul className="list-disc space-y-1 pl-5 text-xs text-amber-200/90">
            {a.limitations.map((lim, i) => (
              <li key={i}>{lim}</li>
            ))}
          </ul>
        </SectionCard>
      )}

      <div className="flex flex-wrap gap-2 border-b border-slate-800 pb-2 text-xs">
        {(
          [
            ["dimensions", `Dimensions (${dimensions.length})`],
            ["findings", `Findings & Evidence (${findings.length})`],
            ["dependencies", "Dependency Impact"],
            ["scenarios", `Failure Scenarios (${scenarios.length})`],
            ["mitigations", `Mitigation Plan (${mitigations.length})`],
          ] as const
        ).map(([k, label]) => (
          <button
            key={k}
            type="button"
            onClick={() => setTab(k)}
            className={`rounded-lg px-3 py-1.5 font-medium ${
              tab === k
                ? "border border-indigo-500/40 bg-indigo-500/20 text-indigo-200"
                : "text-slate-400 hover:bg-slate-900 hover:text-slate-200"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "dimensions" && (
        <div className="grid gap-3 md:grid-cols-2">
          {dimensions.map((d) => (
            <SectionCard
              key={d.id}
              title={d.dimension_name}
              subtitle={`Weight: ${Math.round(d.weight * 100)}% · Sufficiency: ${d.data_sufficiency}`}
              actions={
                <div className="flex items-center gap-2">
                  <StatusBadge status={d.status} />
                  <span className="font-mono text-xs font-semibold text-slate-100">
                    {d.score !== null && d.score !== undefined ? `${d.score}/100` : "N/A"}
                  </span>
                </div>
              }
            >
              <div className="space-y-2 text-xs text-slate-300">
                {d.explanations.map((ex, i) => (
                  <p key={i}>{ex}</p>
                ))}
                {d.limitations.length > 0 && (
                  <p className="text-[11px] text-amber-300">{d.limitations.join(" ")}</p>
                )}
              </div>
            </SectionCard>
          ))}
        </div>
      )}

      {tab === "findings" && (
        <SectionCard title={`Findings (${findings.length}) & Traceable Evidence (${evidence.length})`}>
          {findings.length === 0 ? (
            <p className="text-xs text-slate-400">No risk findings recorded.</p>
          ) : (
            <div className="space-y-2.5">
              {findings.map((f) => (
                <div
                  key={f.id}
                  className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 text-xs space-y-1.5"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-semibold text-slate-100">{f.title}</span>
                    <div className="flex items-center gap-2">
                      <span className="rounded border border-slate-700 px-1.5 py-0.5 text-[10px] text-slate-400">
                        {f.is_ai_inferred ? "AI-Inferred" : "Deterministic"}
                      </span>
                      <RiskLevelBadge level={f.severity} />
                    </div>
                  </div>
                  <p className="text-slate-300">{f.description}</p>
                  {f.source_reference && (
                    <div className="font-mono text-[11px] text-indigo-300">
                      Source: {f.source_reference}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      )}

      {tab === "dependencies" && (
        <SectionCard title="Affected Modules & Dependency Relationships">
          <div className="grid gap-4 md:grid-cols-2 text-xs">
            <div>
              <div className="mb-2 font-semibold text-slate-200">
                Directly Modified Modules ({depGraph.direct_modules?.length ?? 0})
              </div>
              <ul className="space-y-1 font-mono text-[11px] text-slate-300">
                {(depGraph.direct_modules ?? []).map((m, i) => (
                  <li key={i} className="rounded bg-[#0d1119] px-2.5 py-1">
                    <GitBranch className="mr-1.5 inline h-3 w-3 text-indigo-400" />
                    {m}
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <div className="mb-2 font-semibold text-slate-200">
                Dependency Edges ({depGraph.edges?.length ?? 0})
              </div>
              <ul className="space-y-1 font-mono text-[11px] text-slate-400">
                {(depGraph.edges ?? []).slice(0, 20).map((e, i) => (
                  <li key={i} className="rounded bg-[#0d1119] px-2.5 py-1">
                    {e.source} → {e.target}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </SectionCard>
      )}

      {tab === "scenarios" && (
        <SectionCard title="Potential Failure Scenarios (Grounded in Findings)">
          {scenarios.length === 0 ? (
            <p className="text-xs text-slate-400">No potential failure scenarios generated.</p>
          ) : (
            <div className="space-y-3">
              {scenarios.map((sc) => (
                <div
                  key={sc.id}
                  className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 text-xs space-y-1.5"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-slate-100">
                      <AlertTriangle className="mr-1.5 inline h-3.5 w-3.5 text-amber-400" />
                      {sc.title}
                    </span>
                    <RiskLevelBadge level={sc.severity} />
                  </div>
                  <p className="text-slate-300">{sc.description}</p>
                  <div className="text-[11px] text-slate-400">
                    <strong>Trigger:</strong> {sc.trigger_condition}
                  </div>
                  <div className="text-[11px] text-slate-400">
                    <strong>Impact:</strong> {sc.potential_impact}
                  </div>
                  {sc.detection_strategy && (
                    <div className="text-[11px] text-sky-300">
                      <strong>Detection:</strong> {sc.detection_strategy}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      )}

      {tab === "mitigations" && (
        <SectionCard title="Recommended Mitigation Plan">
          {mitigations.length === 0 ? (
            <p className="text-xs text-slate-400">No mitigations required.</p>
          ) : (
            <div className="space-y-3">
              {mitigations.map((m) => (
                <div
                  key={m.id}
                  className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 text-xs space-y-2"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <ShieldCheck className="h-3.5 w-3.5 text-emerald-400" />
                      <span className="font-semibold text-slate-100">{m.title}</span>
                      <RiskLevelBadge level={m.priority} />
                    </div>
                    <StatusBadge status={m.status} />
                  </div>
                  <p className="text-slate-300">{m.description}</p>
                  {m.verification_criteria && (
                    <div className="text-[11px] text-slate-400">
                      <strong>Verification Criteria:</strong> {m.verification_criteria}
                    </div>
                  )}
                  {m.status !== "completed" && (
                    <div className="flex gap-2 pt-1">
                      {m.status === "pending" && (
                        <button
                          type="button"
                          disabled={busy === m.id}
                          onClick={() => void handleMitigationAction(m.id, "accept")}
                          className="rounded border border-slate-700 bg-slate-900 px-2.5 py-1 text-[11px] text-slate-200 hover:bg-slate-800"
                        >
                          Accept
                        </button>
                      )}
                      <button
                        type="button"
                        disabled={busy === m.id}
                        onClick={() => void handleMitigationAction(m.id, "verify")}
                        className="inline-flex items-center gap-1 rounded border border-emerald-500/40 bg-emerald-500/10 px-2.5 py-1 text-[11px] text-emerald-200 hover:bg-emerald-500/20"
                      >
                        <CheckCircle2 className="h-3 w-3" />
                        Mark Verified
                      </button>
                    </div>
                  )}
                  {m.completed_at && (
                    <div className="font-mono text-[10px] text-slate-500">
                      Completed: {formatTimestamp(m.completed_at)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      )}
    </div>
  );
}
