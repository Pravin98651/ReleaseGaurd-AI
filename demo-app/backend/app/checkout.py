"""Checkout v1 (classic 3-step) and Checkout v2 (express 1-page) business logic."""

from __future__ import annotations

from uuid import uuid4

from app.flagsmith_service import FlagEvaluationResult
from app.schemas import CheckoutRequest, CheckoutResponse
from app.telemetry import TelemetryEmitter, pseudonymize_user_id

_V1_PROMOS: dict[str, float] = {
    "NOVA10": 0.10,
}

_V2_PROMOS: dict[str, float] = {
    "NOVA10": 0.10,
    "EXPRESS15": 0.15,
}


async def process_checkout(
    *,
    req: CheckoutRequest,
    flag_eval: FlagEvaluationResult,
    telemetry: TelemetryEmitter,
    salt: str,
    default_environment: str,
) -> CheckoutResponse:
    """Execute Checkout v1 or v2 flow and emit lifecycle telemetry events."""
    env_name = req.environment_name or default_environment
    session_key = f"chk_{uuid4().hex[:12]}"
    order_id = f"ord_{uuid4().hex[:10].upper()}"
    user_identifier = pseudonymize_user_id(req.user_id, salt)

    promo_upper = req.promo_code.strip().upper() if req.promo_code else None
    subtotal = round(req.cart_total, 2)
    discount_amount = 0.0
    promo_applied: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    if flag_eval.checkout_version == "v1":
        steps_completed = ["shipping_address", "payment_method", "order_review"]
        if promo_upper and promo_upper in _V1_PROMOS:
            discount_amount = round(subtotal * _V1_PROMOS[promo_upper], 2)
            promo_applied = promo_upper
        total_charged = round(max(subtotal - discount_amount, 0.0), 2)

        if req.abandon:
            outcome_status = "abandoned"
            outcome_event = "checkout_abandoned"
            message = "Shopper exited Checkout v1 during multi-step review."
        elif req.payment_method.strip().lower() == "declined_card":
            outcome_status = "payment_failed"
            outcome_event = "payment_failed"
            error_code = "ERR_CARD_DECLINED"
            error_message = "Card issuer declined authorization in classic checkout."
            message = "Checkout v1 payment authorization failed."
        else:
            outcome_status = "completed"
            outcome_event = "checkout_completed"
            message = "Order completed via Classic 3-Step Checkout (v1)."
    else:
        steps_completed = ["express_one_page_checkout"]
        if promo_upper and promo_upper in _V2_PROMOS:
            discount_amount = round(subtotal * _V2_PROMOS[promo_upper], 2)
            promo_applied = promo_upper
        total_charged = round(max(subtotal - discount_amount, 0.0), 2)

        triggers_bug = req.simulate_payment_bug or promo_upper == "PROMO-BUG"
        if req.abandon:
            outcome_status = "abandoned"
            outcome_event = "checkout_abandoned"
            message = "Shopper exited Express Checkout v2 before payment submission."
        elif triggers_bug:
            outcome_status = "payment_failed"
            outcome_event = "payment_failed"
            error_code = "ERR_V2_PROMO_WALLET_GATEWAY_502"
            error_message = (
                "Checkout v2 Express Gateway threw 502 during promo-wallet token reconciliation."
            )
            message = "Checkout v2 regression triggered: payment gateway returned 502."
        elif req.payment_method.strip().lower() == "declined_card":
            outcome_status = "payment_failed"
            outcome_event = "payment_failed"
            error_code = "ERR_CARD_DECLINED"
            error_message = "Card issuer declined authorization in express checkout."
            message = "Checkout v2 payment authorization failed."
        else:
            outcome_status = "completed"
            outcome_event = "checkout_completed"
            message = "Order completed via Express 1-Page Checkout (v2)."

    base_props = {
        "checkout_version": flag_eval.checkout_version,
        "payment_method": req.payment_method,
        "wallet_provider": req.wallet_provider,
        "promo_code": promo_upper,
        "flagsmith_connected": flag_eval.flagsmith_connected,
    }

    events = [
        telemetry.build_event(
            event_type="feature_variation_assigned",
            user_id=req.user_id,
            variation_key=flag_eval.variation_key,
            environment_name=env_name,
            session_key=session_key,
            numeric_value=subtotal,
            properties={
                "evaluation_source": flag_eval.evaluation_source,
                "checkout_version": flag_eval.checkout_version,
            },
        ),
        telemetry.build_event(
            event_type="checkout_started",
            user_id=req.user_id,
            variation_key=flag_eval.variation_key,
            environment_name=env_name,
            session_key=session_key,
            numeric_value=subtotal,
            properties={**base_props, "step": steps_completed[0]},
        ),
        telemetry.build_event(
            event_type=outcome_event,
            user_id=req.user_id,
            variation_key=flag_eval.variation_key,
            environment_name=env_name,
            session_key=session_key,
            numeric_value=total_charged if outcome_status == "completed" else subtotal,
            properties={
                **base_props,
                "order_id": order_id,
                "discount_amount": discount_amount,
                "error_code": error_code,
            },
        ),
    ]

    await telemetry.emit_events(events)

    return CheckoutResponse(
        order_id=order_id,
        session_key=session_key,
        status=outcome_status,
        checkout_version=flag_eval.checkout_version,
        variation_key=flag_eval.variation_key,
        flag_key=flag_eval.flag_key,
        environment_name=env_name,
        user_identifier=user_identifier,
        subtotal=subtotal,
        discount_amount=discount_amount,
        total_charged=total_charged,
        promo_applied=promo_applied,
        payment_method=req.payment_method,
        steps_completed=steps_completed,
        message=message,
        error_code=error_code,
        error_message=error_message,
        flagsmith_connected=flag_eval.flagsmith_connected,
        emitted_events=[e["event_type"] for e in events],
    )
