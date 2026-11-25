"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  FolderGit2,
  GitBranch,
  GitPullRequest,
  Lock,
  Plus,
  RefreshCw,
  SearchCode,
} from "lucide-react";
import {
  connectRepository,
  getErrorMessage,
  listRepositories,
  listRepositoryBranches,
  scanRepositoryFlags,
  syncRepoPullRequests,
  syncRepositories,
} from "@/lib/api";
import type { BranchInfo, RepoScanResult, Repository } from "@/types/releaseguard";
import { cn, formatTimestamp } from "@/lib/utils";
import {
  EmptyState,
  ErrorBanner,
  LoadingSkeleton,
  PageHeader,
  SectionCard,
  SuccessBanner,
} from "@/components/ui/StatePrimitives";

export default function RepositoriesPage() {
  const [repos, setRepos] = useState<Repository[]>([]);
  const [selectedRepo, setSelectedRepo] = useState<Repository | null>(null);
  const [branches, setBranches] = useState<BranchInfo[]>([]);
  const [scanResult, setScanResult] = useState<RepoScanResult | null>(null);

  const [owner, setOwner] = useState("");
  const [name, setName] = useState("");
  const [flagsmithProjectId, setFlagsmithProjectId] = useState("");

  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadRepoDetails = useCallback(async (repo: Repository) => {
    setSelectedRepo(repo);
    setScanResult(null);
    setBranches([]);
    try {
      const branchList = await listRepositoryBranches(repo.id);
      setBranches(branchList);
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }, []);

  const loadRepos = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listRepositories({ limit: 50 });
      setRepos(res.items);
      if (res.items.length > 0 && !selectedRepo) {
        await loadRepoDetails(res.items[0]);
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [loadRepoDetails, selectedRepo]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void loadRepos();
    }, 0);
    return () => clearTimeout(timer);
  }, [loadRepos]);

  const handleConnect = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!owner.trim() || !name.trim()) return;
    setBusyAction("connect");
    setError(null);
    setNotice(null);
    try {
      const created = await connectRepository({
        owner: owner.trim(),
        name: name.trim(),
        flagsmith_project_id: flagsmithProjectId.trim()
          ? Number(flagsmithProjectId.trim())
          : undefined,
      });
      setOwner("");
      setName("");
      setFlagsmithProjectId("");
      setRepos((prev) => [created, ...prev.filter((r) => r.id !== created.id)]);
      setNotice(`Connected repository ${created.full_name}.`);
      await loadRepoDetails(created);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleSyncAll = async () => {
    setBusyAction("sync");
    setError(null);
    setNotice(null);
    try {
      const res = await syncRepositories();
      const updated = await listRepositories({ limit: 50 });
      setRepos(updated.items);
      setNotice(
        `Synced ${res.synced_count ?? updated.items.length} GitHub repositories.`
      );
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleScanFlags = async (repo: Repository) => {
    setBusyAction("scan");
    setError(null);
    setNotice(null);
    try {
      const res = await scanRepositoryFlags(repo.id);
      const flattened =
        res.references ??
        (res.references_by_flag
          ? Object.values(res.references_by_flag).flat()
          : []);
      const normalized: RepoScanResult = {
        ...res,
        scanned_files: res.files_scanned ?? res.scanned_files ?? 0,
        matched_flags: res.references_found ?? res.matched_flags ?? flattened.length,
        references: flattened,
      };
      setScanResult(normalized);
      setNotice(
        `Scanned ${repo.full_name}: found ${normalized.matched_flags ?? 0} Flagsmith SDK references across ${normalized.scanned_files ?? 0} files.`
      );
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  const handleSyncPrs = async (repo: Repository) => {
    setBusyAction("sync_prs");
    setError(null);
    setNotice(null);
    try {
      const res = await syncRepoPullRequests(repo.id);
      setNotice(
        `Synced ${res.synced_count ?? res.items?.length ?? 0} pull requests for ${repo.full_name}.`
      );
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Repositories & Code Scanner"
        description="Connect GitHub repositories, inspect protected branches, sync Pull Requests, and scan for Flagsmith SDK references."
        actions={
          <button
            type="button"
            disabled={busyAction !== null}
            onClick={handleSyncAll}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-semibold text-slate-200 hover:bg-slate-800 disabled:opacity-50"
          >
            <RefreshCw
              className={cn("h-3.5 w-3.5", busyAction === "sync" && "animate-spin")}
            />
            Sync GitHub Repos
          </button>
        }
      />

      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <SuccessBanner message={notice} onDismiss={() => setNotice(null)} />

      {/* Connect Repo Form */}
      <SectionCard
        title="Connect GitHub Repository"
        subtitle="Verifies access via GitHub REST API and optionally binds a Flagsmith project ID"
      >
        <form onSubmit={handleConnect} className="grid gap-3 sm:grid-cols-4">
          <input
            type="text"
            required
            value={owner}
            onChange={(e) => setOwner(e.target.value)}
            placeholder="Owner / Org (e.g. octocat)"
            className="rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
          />
          <input
            type="text"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Repo name (e.g. checkout-app)"
            className="rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
          />
          <input
            type="number"
            value={flagsmithProjectId}
            onChange={(e) => setFlagsmithProjectId(e.target.value)}
            placeholder="Flagsmith Project ID (optional)"
            className="rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
          />
          <button
            type="submit"
            disabled={busyAction !== null}
            className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-indigo-500 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
          >
            <Plus className="h-3.5 w-3.5" />
            {busyAction === "connect" ? "Connecting..." : "Connect Repository"}
          </button>
        </form>
      </SectionCard>

      <div className="grid gap-6 lg:grid-cols-12">
        {/* Repository List */}
        <SectionCard
          className="lg:col-span-5"
          title="Connected Repositories"
          subtitle={`${repos.length} repositories registered`}
        >
          {loading ? (
            <LoadingSkeleton rows={3} />
          ) : repos.length === 0 ? (
            <EmptyState
              icon={<FolderGit2 className="h-5 w-5" />}
              title="No repositories connected"
              description="Connect your GitHub PAT/OAuth in Settings, then sync or add a repository above."
            />
          ) : (
            <div className="space-y-2">
              {repos.map((repo) => (
                <button
                  key={repo.id}
                  type="button"
                  onClick={() => void loadRepoDetails(repo)}
                  className={cn(
                    "w-full rounded-xl border p-3.5 text-left transition-colors",
                    selectedRepo?.id === repo.id
                      ? "border-indigo-500/45 bg-indigo-500/10"
                      : "border-slate-800 bg-[#0d1119] hover:border-slate-700"
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-sm font-semibold text-slate-100">
                      {repo.full_name}
                    </span>
                    <div className="flex items-center gap-1.5">
                      {repo.is_private && (
                        <span className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 text-[10px] text-slate-300">
                          <Lock className="h-2.5 w-2.5" />
                          Private
                        </span>
                      )}
                      {repo.language && (
                        <span className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 text-[10px] text-cyan-300">
                          {repo.language}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-3 text-[11px] text-slate-400">
                    <span className="font-mono">base: {repo.default_branch}</span>
                    {repo.flagsmith_project_id && (
                      <span className="font-mono">
                        Flagsmith #{repo.flagsmith_project_id}
                      </span>
                    )}
                    <span className="tabular-nums">
                      Synced: {formatTimestamp(repo.last_synced_at)}
                    </span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </SectionCard>

        {/* Repository Inspector: Branches + SDK Reference Scan */}
        <div className="space-y-6 lg:col-span-7">
          {!selectedRepo ? (
            <SectionCard title="Repository Inspector">
              <EmptyState
                icon={<GitBranch className="h-5 w-5" />}
                title="Select a repository"
                description="Choose a connected repository to inspect live GitHub branches and run Flagsmith SDK code scanning."
              />
            </SectionCard>
          ) : (
            <>
              <SectionCard
                title={selectedRepo.full_name}
                subtitle={`Default branch: ${selectedRepo.default_branch}`}
                actions={
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      disabled={busyAction !== null}
                      onClick={() => void handleSyncPrs(selectedRepo)}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800 disabled:opacity-50"
                    >
                      <GitPullRequest className="h-3.5 w-3.5" />
                      {busyAction === "sync_prs" ? "Syncing PRs..." : "Sync PRs"}
                    </button>
                    <button
                      type="button"
                      disabled={busyAction !== null}
                      onClick={() => void handleScanFlags(selectedRepo)}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-cyan-500/20 border border-cyan-500/40 px-3 py-1.5 text-xs font-semibold text-cyan-200 hover:bg-cyan-500/30 disabled:opacity-50"
                    >
                      <SearchCode className="h-3.5 w-3.5" />
                      {busyAction === "scan" ? "Scanning..." : "Scan Flagsmith SDK"}
                    </button>
                    <Link
                      href={`/pull-requests?repository_id=${encodeURIComponent(selectedRepo.id)}`}
                      className="inline-flex items-center rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400"
                    >
                      Open PRs →
                    </Link>
                  </div>
                }
              >
                <h3 className="mb-2.5 text-xs font-semibold uppercase tracking-wider text-slate-400">
                  Branches ({branches.length})
                </h3>
                {branches.length === 0 ? (
                  <p className="text-xs text-slate-400">
                    No branches returned yet. Ensure GitHub integration is active.
                  </p>
                ) : (
                  <div className="divide-y divide-slate-800 rounded-lg border border-slate-800 bg-[#0d1119]">
                    {branches.map((b) => (
                      <div
                        key={b.name}
                        className="flex items-center justify-between gap-3 px-3.5 py-2 text-xs"
                      >
                        <div className="flex items-center gap-2">
                          <GitBranch className="h-3.5 w-3.5 text-indigo-400" />
                          <span className="font-mono text-slate-100">{b.name}</span>
                          {b.protected && (
                            <span className="rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-300">
                              protected
                            </span>
                          )}
                        </div>
                        <span className="font-mono text-[11px] text-slate-400">
                          {(b.commit_sha || b.sha || "").slice(0, 10) || "—"}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </SectionCard>

              {scanResult && (
                <SectionCard
                  title="Flagsmith SDK Reference Scan Results"
                  subtitle={`Scanned ${scanResult.scanned_files ?? 0} files • ${scanResult.matched_flags ?? scanResult.references?.length ?? 0} flag matches`}
                >
                  {scanResult.summary && (
                    <p className="mb-3 text-xs text-slate-300">{scanResult.summary}</p>
                  )}
                  {!scanResult.references || scanResult.references.length === 0 ? (
                    <p className="text-xs text-slate-400">
                      Zero Flagsmith SDK call sites detected (`is_feature_enabled`,
                      `get_feature_value`, `hasFeature`, `getValue`).
                    </p>
                  ) : (
                    <div className="space-y-2">
                      {scanResult.references.map((ref, idx) => (
                        <div
                          key={`${ref.file_path}-${ref.line_number}-${idx}`}
                          className="rounded-lg border border-slate-800 bg-[#0d1119] p-3 text-xs"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-mono text-cyan-300">
                              {ref.flag_key}
                            </span>
                            <span className="font-mono text-[11px] text-slate-400">
                              {ref.file_path}:{ref.line_number}
                            </span>
                          </div>
                          <pre className="mt-1.5 overflow-x-auto rounded bg-[#090c12] p-2 font-mono text-[11px] text-slate-300">
                            {ref.snippet}
                          </pre>
                        </div>
                      ))}
                    </div>
                  )}
                </SectionCard>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
