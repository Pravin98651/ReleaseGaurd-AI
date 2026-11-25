"use client";

import React, { useState } from "react";
import { CheckCircle2, Columns2, FileCode2, Rows3, ShieldCheck, XCircle, MinusCircle } from "lucide-react";
import type { FileDiffItem, GeneratedTestFile, ValidationReport } from "@/types/releaseguard";
import { cn } from "@/lib/utils";
import { StatusBadge } from "@/components/StatusBadges";

function DiffLineBlock({ diff }: { diff: string }) {
  const lines = (diff || "").split(/\r?\n/);
  return (
    <pre className="overflow-x-auto bg-[#0a0e16] p-3 font-mono text-xs leading-relaxed">
      {lines.map((line, idx) => {
        const isAdd = line.startsWith("+") && !line.startsWith("+++");
        const isDel = line.startsWith("-") && !line.startsWith("---");
        const isHunk = line.startsWith("@@");
        return (
          <div
            key={idx}
            className={cn(
              "px-2 whitespace-pre",
              isAdd && "bg-emerald-500/12 text-emerald-200",
              isDel && "bg-rose-500/12 text-rose-200",
              isHunk && "bg-indigo-500/10 text-indigo-300",
              !isAdd && !isDel && !isHunk && "text-slate-300"
            )}
          >
            {line || " "}
          </div>
        );
      })}
    </pre>
  );
}

export function ValidationChecklist({
  status,
  report,
}: {
  status: string;
  report?: ValidationReport | null;
}) {
  const defaultChecks = [
    { name: "Path & Traversal Safety", ok: report?.path_safety_ok },
    { name: "Overwrite Precondition", ok: report?.overwrite_ok },
    { name: "AST / Syntax Parse", ok: report?.ast_ok },
    {
      name: "Ruff Lint",
      ok: report?.ruff_ok,
      skipped: report?.skipped_reasons?.ruff,
    },
    {
      name: "Pytest Isolated Run",
      ok: report?.pytest_ok,
      skipped: report?.skipped_reasons?.pytest,
    },
    {
      name: "Node / ESLint",
      ok: report?.eslint_ok,
      skipped: report?.skipped_reasons?.eslint,
    },
  ];

  const hasExplicitChecks = Array.isArray(report?.checks) && report.checks.length > 0;

  return (
    <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-3.5 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-xs font-semibold text-slate-200">
          <ShieldCheck className="h-4 w-4 text-cyan-400" />
          <span>Isolated Temp-Dir Patch Validator</span>
        </div>
        <StatusBadge status={status} />
      </div>

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {hasExplicitChecks
          ? report?.checks?.map((chk, idx) => {
              const st = chk.status.toLowerCase();
              return (
                <div
                  key={idx}
                  className="flex items-start gap-2 rounded-md border border-slate-800/80 bg-[#111622] p-2.5 text-xs"
                >
                  {st === "passed" ? (
                    <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" />
                  ) : st === "failed" ? (
                    <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-400" />
                  ) : (
                    <MinusCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400" />
                  )}
                  <div className="min-w-0">
                    <div className="font-medium text-slate-200">{chk.name}</div>
                    {(chk.skipped_reason || chk.detail || chk.message) && (
                      <div className="mt-0.5 truncate text-[11px] text-slate-400">
                        {chk.skipped_reason || chk.detail || chk.message}
                      </div>
                    )}
                  </div>
                </div>
              );
            })
          : defaultChecks.map((chk) => (
              <div
                key={chk.name}
                className="flex items-start gap-2 rounded-md border border-slate-800/80 bg-[#111622] p-2.5 text-xs"
              >
                {chk.ok === true ? (
                  <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" />
                ) : chk.ok === false ? (
                  <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-400" />
                ) : (
                  <MinusCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-500" />
                )}
                <div className="min-w-0">
                  <div className="font-medium text-slate-200">{chk.name}</div>
                  <div className="mt-0.5 text-[11px] text-slate-400">
                    {chk.ok === true
                      ? "Passed"
                      : chk.ok === false
                        ? "Failed"
                        : chk.skipped || "Not run / Skipped"}
                  </div>
                </div>
              </div>
            ))}
      </div>

      {(report?.stdout || report?.stderr) && (
        <details className="rounded-md border border-slate-800 bg-[#090c12] text-xs">
          <summary className="cursor-pointer px-3 py-2 font-medium text-slate-300 hover:text-slate-100">
            Subprocess Logs (scrubbed environment)
          </summary>
          <div className="space-y-2 border-t border-slate-800 p-3 font-mono text-[11px]">
            {report.stdout && (
              <div>
                <div className="mb-1 text-slate-400">STDOUT</div>
                <pre className="max-h-40 overflow-auto whitespace-pre-wrap text-slate-300">
                  {report.stdout}
                </pre>
              </div>
            )}
            {report.stderr && (
              <div>
                <div className="mb-1 text-rose-300">STDERR</div>
                <pre className="max-h-40 overflow-auto whitespace-pre-wrap text-rose-200">
                  {report.stderr}
                </pre>
              </div>
            )}
          </div>
        </details>
      )}
    </div>
  );
}

export function CodeDiffViewer({
  filesDiff,
  generatedTests = [],
  validationStatus,
  validationReport,
}: {
  filesDiff: FileDiffItem[];
  generatedTests?: GeneratedTestFile[];
  validationStatus?: string;
  validationReport?: ValidationReport | null;
}) {
  const [viewMode, setViewMode] = useState<"split" | "unified">("split");
  const [activeTab, setActiveTab] = useState<"diff" | "tests">("diff");

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1 rounded-lg border border-slate-800 bg-[#0d1119] p-1">
          <button
            type="button"
            onClick={() => setActiveTab("diff")}
            className={cn(
              "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
              activeTab === "diff"
                ? "bg-slate-800 text-slate-100"
                : "text-slate-400 hover:text-slate-200"
            )}
          >
            Code Changes ({filesDiff.length})
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("tests")}
            className={cn(
              "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
              activeTab === "tests"
                ? "bg-slate-800 text-slate-100"
                : "text-slate-400 hover:text-slate-200"
            )}
          >
            Generated Tests ({generatedTests.length})
          </button>
        </div>

        {activeTab === "diff" && (
          <div className="flex items-center gap-1 rounded-lg border border-slate-800 bg-[#0d1119] p-1">
            <button
              type="button"
              onClick={() => setViewMode("split")}
              className={cn(
                "inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium",
                viewMode === "split"
                  ? "bg-indigo-500/20 text-indigo-200"
                  : "text-slate-400 hover:text-slate-200"
              )}
            >
              <Columns2 className="h-3.5 w-3.5" />
              Before / After
            </button>
            <button
              type="button"
              onClick={() => setViewMode("unified")}
              className={cn(
                "inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium",
                viewMode === "unified"
                  ? "bg-indigo-500/20 text-indigo-200"
                  : "text-slate-400 hover:text-slate-200"
              )}
            >
              <Rows3 className="h-3.5 w-3.5" />
              Unified Diff
            </button>
          </div>
        )}
      </div>

      {activeTab === "diff" ? (
        filesDiff.length === 0 ? (
          <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-4 text-xs text-slate-400">
            No file diffs attached to this patch.
          </div>
        ) : (
          <div className="space-y-3">
            {filesDiff.map((file, idx) => (
              <div
                key={`${file.file_path}-${idx}`}
                className="overflow-hidden rounded-lg border border-slate-800 bg-[#0d1119]"
              >
                <div className="flex items-center justify-between border-b border-slate-800 bg-[#131926] px-3.5 py-2">
                  <div className="flex items-center gap-2 font-mono text-xs text-slate-200">
                    <FileCode2 className="h-3.5 w-3.5 text-indigo-400" />
                    <span>{file.file_path}</span>
                  </div>
                  <span className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] uppercase text-slate-300">
                    {file.action}
                  </span>
                </div>

                {viewMode === "unified" ? (
                  <DiffLineBlock diff={file.unified_diff} />
                ) : (
                  <div className="grid divide-y divide-slate-800 lg:grid-cols-2 lg:divide-x lg:divide-y-0">
                    <div className="min-w-0">
                      <div className="border-b border-slate-800/80 bg-rose-500/5 px-3 py-1 text-[11px] font-medium text-rose-300">
                        Before
                      </div>
                      <pre className="max-h-80 overflow-auto bg-[#0a0e16] p-3 font-mono text-xs leading-relaxed text-slate-300">
                        {file.before_code || "// (new file)"}
                      </pre>
                    </div>
                    <div className="min-w-0">
                      <div className="border-b border-slate-800/80 bg-emerald-500/5 px-3 py-1 text-[11px] font-medium text-emerald-300">
                        After (Flagsmith Guarded)
                      </div>
                      <pre className="max-h-80 overflow-auto bg-[#0a0e16] p-3 font-mono text-xs leading-relaxed text-emerald-100">
                        {file.after_code}
                      </pre>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )
      ) : generatedTests.length === 0 ? (
        <div className="rounded-lg border border-slate-800 bg-[#0d1119] p-4 text-xs text-slate-400">
          No generated unit tests included.
        </div>
      ) : (
        <div className="space-y-3">
          {generatedTests.map((t, idx) => (
            <div
              key={`${t.file_path}-${idx}`}
              className="overflow-hidden rounded-lg border border-slate-800 bg-[#0d1119]"
            >
              <div className="border-b border-slate-800 bg-[#131926] px-3.5 py-2 font-mono text-xs text-cyan-300">
                {t.file_path}
              </div>
              <pre className="max-h-80 overflow-auto bg-[#0a0e16] p-3 font-mono text-xs leading-relaxed text-slate-200">
                {t.content}
              </pre>
            </div>
          ))}
        </div>
      )}

      {validationStatus && (
        <ValidationChecklist status={validationStatus} report={validationReport} />
      )}
    </div>
  );
}
