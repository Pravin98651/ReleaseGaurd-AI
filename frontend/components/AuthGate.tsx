"use client";

import React, { useEffect, useState } from "react";
import { GitBranch, Lock, ShieldCheck } from "lucide-react";
import {
  completeGithubAuthCallback,
  getErrorMessage,
  loginWithPassword,
  startGithubAuthLogin,
} from "@/lib/api";
import type { AuthStatus } from "@/types/releaseguard";
import { ErrorBanner } from "@/components/ui/StatePrimitives";

export function AuthGate({
  authStatus,
  onAuthenticated,
  children,
}: {
  authStatus: AuthStatus | null;
  onAuthenticated: () => Promise<void> | void;
  children: React.ReactNode;
}) {
  const [email, setEmail] = useState("operator@releaseguard.local");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Handle GitHub OAuth sign-in callback if arriving unauthenticated with ?code=&state=
  useEffect(() => {
    if (
      typeof window === "undefined" ||
      !authStatus?.require_auth ||
      authStatus.authenticated
    ) {
      return;
    }
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const state = params.get("state");
    if (!code || !state) return;

    let cancelled = false;
    void (async () => {
      setBusy(true);
      setError(null);
      try {
        await completeGithubAuthCallback({ code, state });
        window.history.replaceState({}, "", window.location.pathname);
        if (!cancelled) {
          await onAuthenticated();
        }
      } catch (err) {
        if (!cancelled) {
          setError(getErrorMessage(err));
        }
      } finally {
        if (!cancelled) setBusy(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [authStatus?.authenticated, authStatus?.require_auth, onAuthenticated]);

  if (!authStatus?.require_auth || authStatus.authenticated) {
    return <>{children}</>;
  }

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await loginWithPassword({ email, password });
      await onAuthenticated();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const handleGithubLogin = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await startGithubAuthLogin();
      window.location.href = res.authorize_url;
    } catch (err) {
      setError(getErrorMessage(err));
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-[75vh] items-center justify-center p-4">
      <div className="w-full max-w-md rounded-2xl border border-slate-800 bg-[#0d1119] p-6 shadow-2xl space-y-5">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-indigo-500/40 bg-indigo-500/15 text-indigo-300">
            <ShieldCheck className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-base font-semibold text-slate-100">
              ReleaseGuard AI Sign In
            </h1>
            <p className="text-xs text-slate-400">
              RBAC-protected Release Governance Control Plane
            </p>
          </div>
        </div>

        <ErrorBanner error={error} onDismiss={() => setError(null)} />

        <form onSubmit={handleLogin} className="space-y-3 text-xs">
          <label className="block space-y-1">
            <span className="text-slate-300">Operator Email</span>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 text-slate-100 focus:border-indigo-500 focus:outline-none"
            />
          </label>
          <label className="block space-y-1">
            <span className="text-slate-300">Password</span>
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Configured AUTH_OPERATOR_PASSWORD"
              className="w-full rounded-lg border border-slate-700 bg-[#090c12] px-3 py-2 text-slate-100 placeholder:text-slate-600 focus:border-indigo-500 focus:outline-none"
            />
          </label>
          <button
            type="submit"
            disabled={busy}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-500 px-4 py-2.5 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
          >
            <Lock className="h-3.5 w-3.5" />
            {busy ? "Signing in..." : "Sign In to Control Plane"}
          </button>
        </form>

        {authStatus.github_oauth_enabled && (
          <div className="border-t border-slate-800 pt-4">
            <button
              type="button"
              disabled={busy}
              onClick={() => void handleGithubLogin()}
              className="flex w-full items-center justify-center gap-2 rounded-lg border border-slate-700 bg-slate-900 px-4 py-2 text-xs font-semibold text-slate-200 hover:bg-slate-800 disabled:opacity-50"
            >
              <GitBranch className="h-3.5 w-3.5" />
              Continue with GitHub OAuth
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
