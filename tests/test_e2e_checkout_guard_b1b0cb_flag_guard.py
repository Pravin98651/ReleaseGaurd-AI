"""Isolated tests for `e2e_checkout_guard_b1b0cb` Flagsmith boolean wrapper."""

from demo_risky_checkout_b1b0cb import is_e2e_checkout_guard_b1b0cb_enabled


class _FakeFlags:
    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    def is_feature_enabled(self, _key: str) -> bool:
        return self._enabled


class _FakeClient:
    def __init__(self, enabled: bool) -> None:
        self._flags = _FakeFlags(enabled)

    def get_identity_flags(self, identifier: str) -> _FakeFlags:
        return self._flags


class _BrokenClient:
    def get_identity_flags(self, identifier: str) -> _FakeFlags:
        raise RuntimeError("Flagsmith unreachable")


def test_e2e_checkout_guard_b1b0cb_enabled_and_fallback() -> None:
    assert is_e2e_checkout_guard_b1b0cb_enabled(_FakeClient(True), "u1") is True
    assert is_e2e_checkout_guard_b1b0cb_enabled(_FakeClient(False), "u1") is False
    assert is_e2e_checkout_guard_b1b0cb_enabled(_BrokenClient(), "u1") is False
