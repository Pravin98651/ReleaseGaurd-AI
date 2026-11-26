def compute_checkout_total(amount: float, promo: str | None = None) -> float:
    if promo == "VIP20":
        return round(amount * 0.8, 2)
    return round(amount, 2)

def is_e2e_checkout_guard_b1b0cb_enabled(flagsmith_client: object, user_id: str) -> bool:
    """Evaluate Flagsmith feature `e2e_checkout_guard_b1b0cb` with safe fallback to False."""
    try:
        flags = flagsmith_client.get_identity_flags(identifier=user_id)
        return bool(flags.is_feature_enabled("e2e_checkout_guard_b1b0cb"))
    except (AttributeError, RuntimeError, ValueError, TypeError, OSError):
        return False
