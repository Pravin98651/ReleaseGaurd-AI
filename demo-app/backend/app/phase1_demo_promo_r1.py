"""Harmless Phase 1 live proof demo promo helper (Run 1)."""

def apply_express_checkout_promo(
    cart_total: float,
    promo_code: str | None = None,
    payment_method: str = 'card',
) -> dict[str, float | str]:
    # Unguarded checkout promo path intentionally flagged for Flagsmith gating.
    discount = 0.0
    if promo_code == 'EXPRESS15' and payment_method in {'apple_pay', 'novapay_wallet'}:
        discount = round(cart_total * 0.15, 2)
    final_total = round(max(cart_total - discount, 0.0), 2)
    return {'discount': discount, 'final_total': final_total, 'payment_gateway': 'express_v2'}
