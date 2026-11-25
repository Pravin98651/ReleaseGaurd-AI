"use client";

import { useState } from "react";
import { AlertTriangle, CreditCard, Sparkles, Tag, Wallet, Zap } from "lucide-react";

interface CheckoutV2ExpressProps {
  subtotal: number;
  submitting: boolean;
  onSubmit: (payload: {
    payment_method: string;
    wallet_provider?: string;
    promo_code?: string;
    simulate_payment_bug?: boolean;
    abandon?: boolean;
  }) => Promise<void>;
}

const WALLETS = [
  { id: "apple_pay", label: "Apple Pay" },
  { id: "google_pay", label: "Google Pay" },
  { id: "novapay_wallet", label: "NovaPay 1-Click" },
] as const;

export function CheckoutV2Express({
  subtotal,
  submitting,
  onSubmit,
}: CheckoutV2ExpressProps) {
  const [paymentMode, setPaymentMode] = useState<"wallet" | "card">("wallet");
  const [walletProvider, setWalletProvider] = useState<string>("apple_pay");
  const [promoCode, setPromoCode] = useState("EXPRESS15");
  const [simulateBug, setSimulateBug] = useState(false);

  const normalizedPromo = promoCode.trim().toUpperCase();
  const discountRate =
    normalizedPromo === "EXPRESS15" ? 0.15 : normalizedPromo === "NOVA10" ? 0.1 : 0;
  const discount = Number((subtotal * discountRate).toFixed(2));
  const total = Math.max(0, Number((subtotal - discount).toFixed(2)));
  const bugArmed = simulateBug || normalizedPromo === "PROMO-BUG";

  async function handlePay(abandon = false) {
    await onSubmit({
      payment_method: paymentMode,
      wallet_provider: paymentMode === "wallet" ? walletProvider : undefined,
      promo_code: normalizedPromo || undefined,
      simulate_payment_bug: simulateBug,
      abandon,
    });
  }

  return (
    <div className="rounded-xl border border-indigo-500/40 bg-gradient-to-b from-slate-900 to-slate-950 p-5 shadow-lg shadow-indigo-950/20">
      <div className="mb-4 flex items-center justify-between border-b border-slate-800 pb-3">
        <div>
          <span className="inline-flex items-center gap-1 rounded-md border border-indigo-500/40 bg-indigo-500/15 px-2 py-0.5 font-mono text-[11px] font-medium text-indigo-300">
            <Sparkles className="h-3 w-3" /> CHECKOUT v2 · EXPRESS 1-PAGE
          </span>
          <h2 className="mt-1.5 text-base font-semibold text-white">
            Express Instant Wallet &amp; Promo Checkout
          </h2>
        </div>
        <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 font-mono text-[11px] text-emerald-300">
          1-Step Flow
        </span>
      </div>

      {/* Instant Wallet Selector */}
      <div className="mb-4 space-y-2">
        <div className="flex items-center justify-between text-xs text-slate-400">
          <span className="font-medium">Payment Method</span>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setPaymentMode("wallet")}
              className={`rounded px-2 py-0.5 text-[11px] font-medium ${
                paymentMode === "wallet"
                  ? "bg-indigo-600 text-white"
                  : "bg-slate-800 text-slate-400"
              }`}
            >
              Instant Wallet
            </button>
            <button
              type="button"
              onClick={() => setPaymentMode("card")}
              className={`rounded px-2 py-0.5 text-[11px] font-medium ${
                paymentMode === "card"
                  ? "bg-indigo-600 text-white"
                  : "bg-slate-800 text-slate-400"
              }`}
            >
              Express Card
            </button>
          </div>
        </div>

        {paymentMode === "wallet" ? (
          <div className="grid grid-cols-3 gap-2">
            {WALLETS.map((w) => (
              <button
                key={w.id}
                type="button"
                onClick={() => setWalletProvider(w.id)}
                className={`flex items-center justify-center gap-1.5 rounded-lg border px-2.5 py-2.5 text-xs font-medium transition ${
                  walletProvider === w.id
                    ? "border-indigo-400 bg-indigo-500/20 text-white"
                    : "border-slate-800 bg-slate-900 text-slate-400 hover:border-slate-700"
                }`}
              >
                <Wallet className="h-3.5 w-3.5" />
                {w.label}
              </button>
            ))}
          </div>
        ) : (
          <div className="flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-900/90 px-3 py-2.5 font-mono text-xs text-slate-300">
            <CreditCard className="h-4 w-4 text-indigo-400" />
            <span>Saved Visa •••• 4242 · Exp 09/28 (1-Click Tokenized)</span>
          </div>
        )}
      </div>

      {/* Promo Code + Regression Trigger */}
      <div className="mb-4 space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <label className="text-xs font-medium text-slate-400">Express Promo Code</label>
          <div className="flex items-center gap-2 font-mono text-[11px]">
            <button
              type="button"
              onClick={() => setPromoCode("EXPRESS15")}
              className="inline-flex items-center gap-1 text-emerald-400 hover:text-emerald-300"
            >
              <Tag className="h-3 w-3" /> EXPRESS15 (15%)
            </button>
            <button
              type="button"
              onClick={() => setPromoCode("PROMO-BUG")}
              className="inline-flex items-center gap-1 text-rose-400 hover:text-rose-300"
            >
              <AlertTriangle className="h-3 w-3" /> PROMO-BUG
            </button>
          </div>
        </div>
        <input
          type="text"
          value={promoCode}
          onChange={(e) => setPromoCode(e.target.value)}
          placeholder="EXPRESS15, NOVA10, or PROMO-BUG"
          className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-sm text-white outline-none focus:border-indigo-500"
        />
      </div>

      <label className="mb-4 flex items-start gap-2.5 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2.5 text-xs text-rose-200 cursor-pointer">
        <input
          type="checkbox"
          checked={simulateBug}
          onChange={(e) => setSimulateBug(e.target.checked)}
          className="mt-0.5 rounded border-rose-500"
        />
        <span>
          <strong className="font-semibold">Simulate Payment Bug (502 Regression)</strong>
          <span className="block text-[11px] text-rose-300/80">
            Reproduces Checkout v2 promo-wallet gateway failure so ReleaseGuard detects the error spike.
          </span>
        </span>
      </label>

      {/* Summary & Action */}
      <div className="mb-4 rounded-lg border border-slate-800 bg-slate-950/70 p-3 text-xs space-y-1.5">
        <div className="flex justify-between text-slate-400">
          <span>Subtotal</span>
          <span className="font-mono">${subtotal.toFixed(2)}</span>
        </div>
        {discount > 0 && (
          <div className="flex justify-between text-emerald-400">
            <span>Express Discount ({normalizedPromo})</span>
            <span className="font-mono">-${discount.toFixed(2)}</span>
          </div>
        )}
        <div className="flex justify-between border-t border-slate-800 pt-1.5 text-sm font-semibold text-white">
          <span>Total Due</span>
          <span className="font-mono">${total.toFixed(2)}</span>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          disabled={submitting}
          onClick={() => handlePay(true)}
          className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs font-medium text-amber-300 hover:bg-amber-500/20 disabled:opacity-50"
        >
          Abandon Cart
        </button>
        <button
          type="button"
          disabled={submitting}
          onClick={() => handlePay(false)}
          className={`inline-flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-semibold text-white transition disabled:opacity-50 ${
            bugArmed
              ? "bg-rose-600 hover:bg-rose-500"
              : "bg-indigo-600 hover:bg-indigo-500"
          }`}
        >
          <Zap className="h-4 w-4" />
          {submitting
            ? "Authorizing..."
            : bugArmed
              ? `Pay Express ($${total.toFixed(2)}) · Trigger Bug`
              : `Pay Express ($${total.toFixed(2)})`}
        </button>
      </div>
    </div>
  );
}
