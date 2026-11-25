"use client";

import { useState } from "react";
import { ArrowLeft, ArrowRight, CheckCircle2, CreditCard, Lock, MapPin, Tag } from "lucide-react";

interface CheckoutV1FormProps {
  subtotal: number;
  submitting: boolean;
  onSubmit: (payload: {
    payment_method: string;
    promo_code?: string;
    abandon?: boolean;
    shipping: Record<string, string>;
  }) => Promise<void>;
}

const STEPS = [
  { id: 1, label: "Shipping", icon: MapPin },
  { id: 2, label: "Payment", icon: CreditCard },
  { id: 3, label: "Confirm", icon: CheckCircle2 },
] as const;

export function CheckoutV1Form({ subtotal, submitting, onSubmit }: CheckoutV1FormProps) {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [fullName, setFullName] = useState("Jordan Mercer");
  const [address, setAddress] = useState("742 Evergreen Terrace, Suite 4B");
  const [city, setCity] = useState("San Francisco");
  const [postalCode, setPostalCode] = useState("94107");
  const [cardNumber, setCardNumber] = useState("4242 •••• •••• 4242");
  const [expiry, setExpiry] = useState("09/28");
  const [cvc, setCvc] = useState("842");
  const [promoCode, setPromoCode] = useState("");
  const [declineCard, setDeclineCard] = useState(false);

  const normalizedPromo = promoCode.trim().toUpperCase();
  const discount = normalizedPromo === "NOVA10" ? Number((subtotal * 0.1).toFixed(2)) : 0;
  const total = Math.max(0, Number((subtotal - discount).toFixed(2)));

  async function handleConfirm(abandon = false) {
    await onSubmit({
      payment_method: declineCard ? "declined_card" : "card",
      promo_code: normalizedPromo || undefined,
      abandon,
      shipping: { fullName, address, city, postalCode },
    });
  }

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5 shadow-lg">
      <div className="mb-4 flex items-center justify-between border-b border-slate-800 pb-3">
        <div>
          <span className="inline-flex items-center rounded-md border border-slate-700 bg-slate-800 px-2 py-0.5 font-mono text-[11px] font-medium text-slate-300">
            CHECKOUT v1 · CLASSIC 3-STEP
          </span>
          <h2 className="mt-1.5 text-base font-semibold text-white">
            Standard Multi-Step Checkout
          </h2>
        </div>
        <div className="text-right font-mono text-xs text-slate-400">
          Step {step} of 3
        </div>
      </div>

      {/* Stepper */}
      <div className="mb-5 grid grid-cols-3 gap-2">
        {STEPS.map((item) => {
          const Icon = item.icon;
          const active = step === item.id;
          const done = step > item.id;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => setStep(item.id)}
              className={`flex items-center justify-center gap-1.5 rounded-lg border px-2.5 py-2 text-xs font-medium transition ${
                active
                  ? "border-sky-500/60 bg-sky-500/15 text-sky-300"
                  : done
                    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                    : "border-slate-800 bg-slate-950/50 text-slate-400 hover:border-slate-700"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              <span>
                {item.id}. {item.label}
              </span>
            </button>
          );
        })}
      </div>

      {step === 1 && (
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-400">Full Name</label>
            <input
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white outline-none focus:border-sky-500"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-400">
              Street Address
            </label>
            <input
              type="text"
              value={address}
              onChange={(e) => setAddress(e.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white outline-none focus:border-sky-500"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-400">City</label>
              <input
                type="text"
                value={city}
                onChange={(e) => setCity(e.target.value)}
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white outline-none focus:border-sky-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-400">Postal Code</label>
              <input
                type="text"
                value={postalCode}
                onChange={(e) => setPostalCode(e.target.value)}
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white outline-none focus:border-sky-500"
              />
            </div>
          </div>
          <div className="pt-2 flex justify-end">
            <button
              type="button"
              onClick={() => setStep(2)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 transition"
            >
              Continue to Payment
              <ArrowRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-400">Card Number</label>
            <input
              type="text"
              value={cardNumber}
              onChange={(e) => setCardNumber(e.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-sm text-white outline-none focus:border-sky-500"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-400">Expiry</label>
              <input
                type="text"
                value={expiry}
                onChange={(e) => setExpiry(e.target.value)}
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-sm text-white outline-none focus:border-sky-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-400">CVC</label>
              <input
                type="text"
                value={cvc}
                onChange={(e) => setCvc(e.target.value)}
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-sm text-white outline-none focus:border-sky-500"
              />
            </div>
          </div>

          <div>
            <div className="mb-1 flex items-center justify-between">
              <label className="text-xs font-medium text-slate-400">Promo Code (v1: NOVA10)</label>
              <button
                type="button"
                onClick={() => setPromoCode("NOVA10")}
                className="inline-flex items-center gap-1 font-mono text-[11px] text-sky-400 hover:text-sky-300"
              >
                <Tag className="h-3 w-3" /> Apply NOVA10
              </button>
            </div>
            <input
              type="text"
              placeholder="Optional promo code"
              value={promoCode}
              onChange={(e) => setPromoCode(e.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-sm text-white outline-none focus:border-sky-500"
            />
          </div>

          <label className="flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={declineCard}
              onChange={(e) => setDeclineCard(e.target.checked)}
              className="rounded border-slate-600"
            />
            Simulate declined test card
          </label>

          <div className="flex items-center justify-between pt-2">
            <button
              type="button"
              onClick={() => setStep(1)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:bg-slate-800"
            >
              <ArrowLeft className="h-4 w-4" />
              Back
            </button>
            <button
              type="button"
              onClick={() => setStep(3)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 transition"
            >
              Review Order
              <ArrowRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="space-y-4">
          <div className="rounded-lg border border-slate-800 bg-slate-950/70 p-3.5 text-xs space-y-2">
            <div className="flex justify-between text-slate-400">
              <span>Ship to</span>
              <span className="text-slate-200">{fullName} · {city} {postalCode}</span>
            </div>
            <div className="flex justify-between text-slate-400">
              <span>Payment</span>
              <span className="font-mono text-slate-200">
                {declineCard ? "Test Declined Card" : cardNumber}
              </span>
            </div>
            {discount > 0 && (
              <div className="flex justify-between text-emerald-400">
                <span>Promo ({normalizedPromo})</span>
                <span className="font-mono">-${discount.toFixed(2)}</span>
              </div>
            )}
            <div className="flex justify-between border-t border-slate-800 pt-2 text-sm font-semibold text-white">
              <span>Total Charged</span>
              <span className="font-mono">${total.toFixed(2)}</span>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2">
            <button
              type="button"
              onClick={() => setStep(2)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:bg-slate-800"
            >
              <ArrowLeft className="h-4 w-4" />
              Edit Payment
            </button>
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={submitting}
                onClick={() => handleConfirm(true)}
                className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs font-medium text-amber-300 hover:bg-amber-500/20 disabled:opacity-50"
              >
                Abandon Checkout
              </button>
              <button
                type="button"
                disabled={submitting}
                onClick={() => handleConfirm(false)}
                className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                <Lock className="h-3.5 w-3.5" />
                {submitting ? "Processing..." : `Place Order ($${total.toFixed(2)})`}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
