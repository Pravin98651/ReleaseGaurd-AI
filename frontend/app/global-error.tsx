"use client";

import React from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

export default function GlobalErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body className="bg-[#090c12] text-slate-100 antialiased">
        <div className="mx-auto my-16 max-w-xl rounded-xl border border-rose-500/40 bg-rose-500/10 p-6">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-rose-400" />
            <div className="space-y-2">
              <h2 className="text-base font-semibold">Application Error</h2>
              <p className="text-xs text-slate-300">
                {error.message || "Unexpected application failure."}
              </p>
              <button
                type="button"
                onClick={reset}
                className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Reload View
              </button>
            </div>
          </div>
        </div>
      </body>
    </html>
  );
}
