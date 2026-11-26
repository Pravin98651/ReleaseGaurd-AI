"use client";

import { useState } from "react";
import { Lock, LogIn, UserCheck, X } from "lucide-react";

export interface ShopperSession {
  authenticated: boolean;
  user_id: string;
  email: string;
  display_name: string;
  user_type: string;
  subscription_plan: string;
}

const DEMO_PERSONAS: Array<{
  label: string;
  email: string;
  user_type: string;
  subscription_plan: string;
}> = [
  { label: "Consumer (shopper_42)", email: "shopper_42@novashop.demo", user_type: "consumer", subscription_plan: "free" },
  { label: "Internal Tester", email: "alex@novashop.demo", user_type: "internal", subscription_plan: "pro" },
  { label: "Beta Subscriber", email: "beta@novashop.demo", user_type: "qa", subscription_plan: "beta" },
  { label: "Enterprise VIP", email: "enterprise@novashop.demo", user_type: "enterprise", subscription_plan: "enterprise" },
];

export function ShopperLoginModal({
  open,
  demoApiUrl,
  onClose,
  onLoggedIn,
}: {
  open: boolean;
  demoApiUrl: string;
  onClose: () => void;
  onLoggedIn: (session: ShopperSession) => void;
}) {
  const [emailOrId, setEmailOrId] = useState("shopper_42@novashop.demo");
  const [password, setPassword] = useState("novashop123");
  const [userType, setUserType] = useState("consumer");
  const [subscriptionPlan, setSubscriptionPlan] = useState("free");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const resp = await fetch(`${demoApiUrl.replace(/\/+$/, "")}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email_or_id: emailOrId,
          password,
          user_type: userType,
          subscription_plan: subscriptionPlan,
        }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || `Login failed (${resp.status})`);
      }
      const data = (await resp.json()) as ShopperSession;
      onLoggedIn(data);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Shopper sign-in failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-2xl border border-white/5 bg-[#171c2a] p-6 shadow-[7px_7px_15px_rgba(6,8,14,0.85),-5px_-5px_12px_rgba(36,44,66,0.42)] space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-indigo-500/40 bg-indigo-500/15 text-indigo-300">
              <UserCheck className="h-4 w-4" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-white">NovaShop Shopper Sign In</h2>
              <p className="text-[11px] text-slate-400">
                Authenticate a shopper identity for Flagsmith targeting
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:text-white"
            aria-label="Close login modal"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex flex-wrap gap-1.5">
          {DEMO_PERSONAS.map((p) => (
            <button
              key={p.email}
              type="button"
              onClick={() => {
                setEmailOrId(p.email);
                setUserType(p.user_type);
                setSubscriptionPlan(p.subscription_plan);
              }}
              className="rounded-full border border-white/5 bg-slate-900 px-2.5 py-1 text-[11px] text-slate-300 hover:text-indigo-200"
            >
              {p.label}
            </button>
          ))}
        </div>

        {error && (
          <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-3 text-xs">
          <label className="block space-y-1">
            <span className="text-slate-300">Shopper Email or ID</span>
            <input
              type="text"
              required
              value={emailOrId}
              onChange={(e) => setEmailOrId(e.target.value)}
              className="w-full rounded-lg px-3 py-2 text-slate-100 focus:outline-none"
            />
          </label>
          <label className="block space-y-1">
            <span className="text-slate-300">Password</span>
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg px-3 py-2 text-slate-100 focus:outline-none"
            />
          </label>
          <div className="grid grid-cols-2 gap-2">
            <label className="block space-y-1">
              <span className="text-slate-300">User Type Trait</span>
              <select
                value={userType}
                onChange={(e) => setUserType(e.target.value)}
                className="w-full rounded-lg px-2.5 py-2 text-slate-100 focus:outline-none"
              >
                <option value="consumer">consumer</option>
                <option value="internal">internal</option>
                <option value="qa">qa</option>
                <option value="enterprise">enterprise</option>
              </select>
            </label>
            <label className="block space-y-1">
              <span className="text-slate-300">Plan Trait</span>
              <select
                value={subscriptionPlan}
                onChange={(e) => setSubscriptionPlan(e.target.value)}
                className="w-full rounded-lg px-2.5 py-2 text-slate-100 focus:outline-none"
              >
                <option value="free">free</option>
                <option value="beta">beta</option>
                <option value="pro">pro</option>
                <option value="enterprise">enterprise</option>
              </select>
            </label>
          </div>
          <button
            type="submit"
            disabled={busy}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-500 px-4 py-2.5 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
          >
            {busy ? <Lock className="h-3.5 w-3.5" /> : <LogIn className="h-3.5 w-3.5" />}
            {busy ? "Signing in..." : "Sign In to NovaShop"}
          </button>
        </form>
      </div>
    </div>
  );
}
