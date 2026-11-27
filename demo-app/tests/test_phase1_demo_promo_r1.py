"""Unit test for Phase 1 live proof demo promo helper (Run 1)."""

from app.phase1_demo_promo_r1 import apply_express_checkout_promo

def test_express_promo_discount() -> None:
    out = apply_express_checkout_promo(100.0, promo_code='EXPRESS15', payment_method='apple_pay')
    assert out['discount'] == 15.0
    assert out['final_total'] == 85.0
