"use client";

import React, { useEffect } from "react";
import Link from "next/link";
import { AlertTriangle, RefreshCw } from "lucide-react";

export default function RouteErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("ReleaseGuard UI route error:", error);
  }, [error]);

  return (
    <div className="mx-auto my-12 max-w-xl rounded-xl border border-rose-500/40 bg-rose-500/10 p-6 text-slate-100">
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-rose-400" />
        <div className="space-y-2">
          <h2 className="text-base font-semibold">Something went wrong on this view</h2>
          <p className="text-xs text-slate-300">
            {error.message || "An unexpected client rendering error occurred."}
          </p>
          {error.digest && (
            <p className="font-mono text-[11px] text-slate-400">
              Digest: {error.digest}
            </p>
          )}
          <div className="flex flex-wrap gap-2 pt-2">
            <button
              type="button"
              onClick={reset}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Retry View
            </button>
            <Link
              href="/dashboard"
              className="inline-flex items-center rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800"
            >
              Return to Dashboard
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
