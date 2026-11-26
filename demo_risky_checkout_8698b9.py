def compute_checkout_total(amount: float, promo: str | None = None) -> float:
    if promo == "VIP20":
        return round(amount * 0.8, 2)
    return round(amount, 2)
