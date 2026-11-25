"use client";

import React, { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { accessSharedReport, getErrorMessage } from "@/lib/api";
import type { ReleaseReadinessReportRecord } from "@/types/releaseguard";
import { formatTimestamp } from "@/lib/utils";
import { ErrorBanner, LoadingSkeleton, PageHeader, SectionCard } from "@/components/ui/StatePrimitives";
import { StatusBadge } from "@/components/StatusBadges";

export default function SharedReadinessReportPage() {
  const params = useParams<{ token: string }>();
  const token = params?.token ?? "";
  const [report, setReport] = useState<ReleaseReadinessReportRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    let active = true;
    accessSharedReport(token)
      .then((res) => {
        if (!active) return;
        setReport(res);
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
  }, [token]);

  if (loading) return <LoadingSkeleton rows={5} />;

  if (!report) {
    return (
      <div className="mx-auto max-w-3xl py-10">
        <ErrorBanner error={error || "This shareable report link is invalid, expired, or revoked."} />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 py-6">
      <PageHeader
        title={report.title}
        description={`Report Key: ${report.report_key} | Generated ${formatTimestamp(report.created_at)}`}
        actions={<StatusBadge status={report.readiness_outcome} />}
      />
      <SectionCard title="Release Readiness Summary" subtitle="Sanitized stakeholder view">
        <pre className="whitespace-pre-wrap font-mono text-xs text-slate-200">
          {report.markdown_content}
        </pre>
      </SectionCard>
    </div>
  );
}
