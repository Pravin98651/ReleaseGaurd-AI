"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, ShieldAlert } from "lucide-react";
import { getErrorMessage, getRepositoryIntelligence } from "@/lib/api";
import type { RepositoryIntelligenceProfile } from "@/types/releaseguard";
import { formatTimestamp } from "@/lib/utils";
import {
  ErrorBanner,
  LoadingSkeleton,
  MetricCard,
  PageHeader,
  SectionCard,
} from "@/components/ui/StatePrimitives";

export default function RepositoryIntelligenceDetailPage() {
  const params = useParams<{ repositoryId: string }>();
  const repositoryId = params?.repositoryId ?? "";
  const [profile, setProfile] = useState<RepositoryIntelligenceProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!repositoryId) return;
    let active = true;
    getRepositoryIntelligence(repositoryId)
      .then((res) => {
        if (!active) return;
        setProfile(res);
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
  }, [repositoryId]);

  if (loading) return <LoadingSkeleton rows={5} />;

  if (!profile) {
    return (
      <div className="space-y-4">
        <ErrorBanner error={error || "Repository profile not found."} />
        <Link href="/intelligence" className="text-xs text-indigo-400 hover:underline">
          Back to Release Intelligence
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <Link href="/intelligence" className="inline-flex items-center gap-1 hover:text-slate-200">
          <ArrowLeft className="h-3.5 w-3.5" />
          Release Intelligence
        </Link>
        <span>/</span>
        <span className="text-slate-200">{profile.repository_name || profile.repository_id}</span>
      </div>

      <PageHeader
        title={`Repository Intelligence: ${profile.repository_name || profile.repository_id}`}
        description={`Longitudinal release profile v${profile.version} | Last analyzed ${formatTimestamp(profile.last_analyzed_at)}`}
      />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard label="Qualifying Releases" value={profile.release_count} />
        <MetricCard label="Failures" value={profile.failure_count} />
        <MetricCard label="Rollbacks" value={profile.rollback_count} />
        <MetricCard
          label="Historical Failure Rate"
          value={`${(profile.historical_failure_rate * 100).toFixed(1)}%`}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <SectionCard title="High-Risk Modules" subtitle="Modules touched across historical releases">
          {profile.high_risk_modules.length === 0 ? (
            <p className="text-xs text-slate-400">No module-level PR history recorded yet.</p>
          ) : (
            <div className="space-y-2">
              {profile.high_risk_modules.map((m) => (
                <div
                  key={m.module}
                  className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/50 px-3 py-2 text-xs"
                >
                  <code className="font-semibold text-slate-100">{m.module}/</code>
                  <span className="font-mono text-slate-300">
                    {m.failure_count}/{m.release_count} failed ({(m.failure_rate * 100).toFixed(1)}%)
                  </span>
                </div>
              ))}
            </div>
          )}
        </SectionCard>

        <SectionCard
          title="Repository-Specific Recommendations"
          subtitle="Evidence-backed mitigations derived from historical outcomes"
        >
          {profile.recommendations.length === 0 ? (
            <p className="text-xs text-slate-400">No repository-specific changes recommended yet.</p>
          ) : (
            <div className="space-y-2.5">
              {profile.recommendations.map((rec, idx) => (
                <div
                  key={idx}
                  className="rounded-lg border border-slate-800 bg-slate-900/50 p-3 text-xs"
                >
                  <div className="flex items-center gap-2 font-semibold text-slate-100">
                    <ShieldAlert className="h-4 w-4 text-amber-400" />
                    <span>{rec.title}</span>
                  </div>
                  <p className="mt-1 text-slate-300">{rec.rationale}</p>
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      </div>
    </div>
  );
}
