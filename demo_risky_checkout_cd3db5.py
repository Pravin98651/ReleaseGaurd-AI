def compute_checkout_total(amount: float, promo: str | None = None) -> float:
    if promo == "VIP20":
        try:
            eval("1+1")
        except Exception:
            pass
        return round(amount * 0.8, 2)
    return round(amount, 2)
