"""Unit and integration tests for NovaShop Checkout v1 vs v2 Demo App."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx
import pytest
from app.config import DemoSettings
from app.flagsmith_service import FlagsmithService, normalize_flag_state
from app.main import create_app
from app.telemetry import TelemetryEmitter, pseudonymize_user_id
from fastapi.testclient import TestClient

REQUIRED_EVENT_KEYS = {
    "event_id",
    "flag_key",
    "environment_name",
    "variation_key",
    "event_type",
    "user_identifier",
    "occurred_at",
}

ALLOWED_EVENT_TYPES = {
    "feature_variation_assigned",
    "checkout_started",
    "checkout_completed",
    "checkout_abandoned",
    "payment_failed",
}


class _FakeFlag:
    def __init__(self, enabled: bool, value: Any = None) -> None:
        self.enabled = enabled
        self.value = value


class _FakeFlags:
    def __init__(self, mapping: dict[str, _FakeFlag]) -> None:
        self._mapping = mapping

    def get_flag(self, feature_name: str) -> _FakeFlag:
        return self._mapping.get(feature_name, _FakeFlag(enabled=False, value="v1"))


class _FakeFlagsmithClient:
    """Deterministic in-memory stand-in for flagsmith.Flagsmith."""

    def __init__(self, resolver) -> None:
        self._resolver = resolver
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def get_identity_flags(
        self,
        identifier: str,
        traits: dict[str, Any] | None = None,
    ) -> _FakeFlags:
        self.calls.append((identifier, traits))
        return _FakeFlags({"checkout_v2": self._resolver(identifier, traits)})


def _make_mock_ingest_client(captured_requests: list[dict[str, Any]]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        captured_requests.append(
            {
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": body,
            }
        )
        return httpx.Response(
            200,
            json={"accepted": len(body.get("events", [])), "deduplicated": 0},
        )

    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


def _validate_ingest_event_schema(event: dict[str, Any]) -> None:
    assert REQUIRED_EVENT_KEYS.issubset(event.keys())
    assert isinstance(event["event_id"], str) and event["event_id"].startswith("evt_")
    assert event["flag_key"] == "checkout_v2"
    assert isinstance(event["environment_name"], str) and event["environment_name"]
    assert event["variation_key"] in {"control", "treatment", "v1", "v2"}
    assert event["event_type"] in ALLOWED_EVENT_TYPES
    assert isinstance(event["user_identifier"], str) and "@" not in event["user_identifier"]
    parsed_dt = datetime.fromisoformat(event["occurred_at"])
    assert parsed_dt.tzinfo is not None


def test_health_reports_disconnected_without_flagsmith_key() -> None:
    settings = DemoSettings(FLAGSMITH_SERVER_ENV_KEY="")
    app = create_app(settings=settings)
    with TestClient(app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["flagsmith_connected"] is False
        assert data["flag_key"] == "checkout_v2"


def test_normalize_flag_state_handles_boolean_and_multivariate() -> None:
    assert normalize_flag_state(False, None) == ("v1", "control")
    assert normalize_flag_state(True, None) == ("v2", "treatment")
    assert normalize_flag_state(True, "v1") == ("v1", "v1")
    assert normalize_flag_state(True, "v2") == ("v2", "v2")
    assert normalize_flag_state(False, "control") == ("v1", "control")
    assert normalize_flag_state(True, "treatment") == ("v2", "treatment")


def test_pseudonymize_user_id_hides_emails_and_preserves_synthetic_handles() -> None:
    hashed = pseudonymize_user_id("alice.buyer@example.com")
    assert "@" not in hashed
    assert hashed.startswith("usr_")
    assert pseudonymize_user_id("alice.buyer@example.com") == hashed
    assert pseudonymize_user_id("shopper_42") == "shopper_42"


def test_flags_fallback_when_unconfigured_and_emits_valid_event() -> None:
    captured: list[dict[str, Any]] = []
    settings = DemoSettings(
        FLAGSMITH_SERVER_ENV_KEY="",
        RELEASEGUARD_API_URL="http://releaseguard.local:8000",
        RELEASEGUARD_INGEST_KEY="test-ingest-secret",
    )
    telemetry = TelemetryEmitter(settings, http_client=_make_mock_ingest_client(captured))
    app = create_app(settings=settings, telemetry=telemetry)

    with TestClient(app) as client:
        resp = client.get("/api/flags", params={"user_id": "buyer@example.com"})
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["enabled"] is False
        assert payload["checkout_version"] == "v1"
        assert payload["variation_key"] == "v1"
        assert payload["flagsmith_connected"] is False
        assert payload["evaluation_source"] == "fallback_default"
        assert payload["event_emitted"] is True
        assert "@" not in payload["user_identifier"]

    assert len(captured) == 1
    req = captured[0]
    assert req["url"] == "http://releaseguard.local:8000/api/v1/events/ingest"
    assert req["headers"]["x-ingest-key"] == "test-ingest-secret"
    events = req["body"]["events"]
    assert len(events) == 1
    assert events[0]["event_type"] == "feature_variation_assigned"
    _validate_ingest_event_schema(events[0])


def test_checkout_v1_classic_flow_with_mocked_flagsmith_sdk() -> None:
    captured: list[dict[str, Any]] = []
    settings = DemoSettings(
        FLAGSMITH_SERVER_ENV_KEY="ser.fake-key",
        RELEASEGUARD_API_URL="http://releaseguard.local:8000",
        RELEASEGUARD_INGEST_KEY="ingest-123",
    )
    fake_sdk = _FakeFlagsmithClient(lambda _uid, _traits: _FakeFlag(enabled=False, value="v1"))
    fs_service = FlagsmithService(settings, client=fake_sdk)
    telemetry = TelemetryEmitter(settings, http_client=_make_mock_ingest_client(captured))
    app = create_app(settings=settings, flagsmith_service=fs_service, telemetry=telemetry)

    with TestClient(app) as client:
        health = client.get("/api/health").json()
        assert health["flagsmith_connected"] is True

        resp = client.post(
            "/api/checkout",
            json={
                "user_id": "shopper_10",
                "cart_total": 100.0,
                "payment_method": "card",
                "promo_code": "NOVA10",
                "simulate_payment_bug": True,  # v1 must stay immune to v2 bug!
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["checkout_version"] == "v1"
        assert body["variation_key"] == "v1"
        assert body["discount_amount"] == 10.0
        assert body["total_charged"] == 90.0
        assert body["steps_completed"] == ["shipping_address", "payment_method", "order_review"]
        assert body["emitted_events"] == [
            "feature_variation_assigned",
            "checkout_started",
            "checkout_completed",
        ]

    assert fake_sdk.calls[0][0] == "shopper_10"
    emitted = captured[0]["body"]["events"]
    assert len(emitted) == 3
    for ev in emitted:
        _validate_ingest_event_schema(ev)


def test_checkout_v2_express_success_and_payment_bug_regression() -> None:
    captured: list[dict[str, Any]] = []
    settings = DemoSettings(
        FLAGSMITH_SERVER_ENV_KEY="ser.fake-key",
        RELEASEGUARD_API_URL="http://releaseguard.local:8000",
        RELEASEGUARD_INGEST_KEY="ingest-123",
    )
    fake_sdk = _FakeFlagsmithClient(
        lambda _uid, _traits: _FakeFlag(enabled=True, value="treatment")
    )
    fs_service = FlagsmithService(settings, client=fake_sdk)
    telemetry = TelemetryEmitter(settings, http_client=_make_mock_ingest_client(captured))
    app = create_app(settings=settings, flagsmith_service=fs_service, telemetry=telemetry)

    with TestClient(app) as client:
        ok_resp = client.post(
            "/api/checkout",
            json={
                "user_id": "shopper_88",
                "cart_total": 200.0,
                "payment_method": "wallet",
                "wallet_provider": "apple_pay",
                "promo_code": "EXPRESS15",
            },
        )
        assert ok_resp.status_code == 200
        ok_body = ok_resp.json()
        assert ok_body["status"] == "completed"
        assert ok_body["checkout_version"] == "v2"
        assert ok_body["variation_key"] == "treatment"
        assert ok_body["discount_amount"] == 30.0
        assert ok_body["total_charged"] == 170.0
        assert ok_body["steps_completed"] == ["express_one_page_checkout"]

        bug_resp = client.post(
            "/api/checkout",
            json={
                "user_id": "shopper_89",
                "cart_total": 149.0,
                "payment_method": "wallet",
                "promo_code": "PROMO-BUG",
            },
        )
        assert bug_resp.status_code == 200
        bug_body = bug_resp.json()
        assert bug_body["status"] == "payment_failed"
        assert bug_body["checkout_version"] == "v2"
        assert bug_body["error_code"] == "ERR_V2_PROMO_WALLET_GATEWAY_502"
        assert bug_body["emitted_events"] == [
            "feature_variation_assigned",
            "checkout_started",
            "payment_failed",
        ]

    assert len(captured) == 2
    for req in captured:
        for ev in req["body"]["events"]:
            _validate_ingest_event_schema(ev)


def test_checkout_abandoned_and_recent_telemetry_inspector() -> None:
    captured: list[dict[str, Any]] = []
    settings = DemoSettings(FLAGSMITH_SERVER_ENV_KEY="")
    telemetry = TelemetryEmitter(settings, http_client=_make_mock_ingest_client(captured))
    app = create_app(settings=settings, telemetry=telemetry)

    with TestClient(app) as client:
        resp = client.post(
            "/api/checkout",
            json={"user_id": "user_007", "cart_total": 75.0, "abandon": True},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "abandoned"
        assert resp.json()["emitted_events"][-1] == "checkout_abandoned"

        recent = client.get("/api/telemetry/recent", params={"limit": 10}).json()
        assert recent["total_buffered"] == 3
        assert len(recent["events"]) == 3
        assert recent["events"][0]["delivery_status"] == "delivered"


@pytest.mark.parametrize(
    "scenario,expect_high_v2_errors",
    [("healthy_v2", False), ("buggy_v2", True)],
)
def test_simulator_chunks_batches_and_reports_cohort_metrics(
    scenario: str,
    expect_high_v2_errors: bool,
) -> None:
    captured: list[dict[str, Any]] = []
    settings = DemoSettings(
        FLAGSMITH_SERVER_ENV_KEY="ser.fake-key",
        RELEASEGUARD_API_URL="http://releaseguard.local:8000",
        RELEASEGUARD_INGEST_KEY="sim-key",
    )
    # Alternate even/odd user_ids across control vs treatment via mocked Flagsmith SDK
    fake_sdk = _FakeFlagsmithClient(
        lambda uid, _traits: _FakeFlag(
            enabled=(int(uid.split("_")[-1]) % 2 == 0),
            value="treatment" if (int(uid.split("_")[-1]) % 2 == 0) else "control",
        )
    )
    fs_service = FlagsmithService(settings, client=fake_sdk)
    telemetry = TelemetryEmitter(settings, http_client=_make_mock_ingest_client(captured))
    app = create_app(settings=settings, flagsmith_service=fs_service, telemetry=telemetry)

    # 200 users -> 600 events -> 2 ingest batches (500 + 100)
    with TestClient(app) as client:
        resp = client.post(
            "/api/simulate",
            json={"users_count": 200, "scenario": scenario, "seed": 42},
        )
        assert resp.status_code == 200
        report = resp.json()
        assert report["users_count"] == 200
        assert report["evaluation_mode"] == "flagsmith_live"
        assert report["flagsmith_connected"] is True
        assert report["events_generated"] == 600
        assert report["batches_sent"] == 2
        assert report["ingest_accepted"] == 600
        assert report["ingest_failed_batches"] == 0
        assert report["control"]["exposures"] == 100
        assert report["treatment"]["exposures"] == 100

        if expect_high_v2_errors:
            assert report["treatment"]["error_rate"] > 0.08
        else:
            assert report["treatment"]["conversion_rate"] >= report["control"]["conversion_rate"]
            assert report["treatment"]["error_rate"] < 0.05

    assert len(captured) == 2
    assert len(captured[0]["body"]["events"]) == 500
    assert len(captured[1]["body"]["events"]) == 100
    for ev in captured[0]["body"]["events"][:10]:
        _validate_ingest_event_schema(ev)


def test_offline_releaseguard_degrades_gracefully_without_crashing() -> None:
    def failing_handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    settings = DemoSettings(FLAGSMITH_SERVER_ENV_KEY="")
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(failing_handler))
    telemetry = TelemetryEmitter(settings, http_client=http_client)
    app = create_app(settings=settings, telemetry=telemetry)

    with TestClient(app) as client:
        resp = client.post(
            "/api/checkout",
            json={"user_id": "shopper_99", "cart_total": 59.0, "payment_method": "card"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

        recent = client.get("/api/telemetry/recent").json()
        assert recent["total_buffered"] == 3
        assert all(ev["delivery_status"] == "offline" for ev in recent["events"])


def test_flagsmith_sdk_runtime_exception_falls_back_to_v1() -> None:
    class _ExplodingFlagsmith:
        def get_identity_flags(self, identifier: str, traits: dict[str, Any] | None = None):
            raise RuntimeError("Flagsmith edge timeout")

    captured: list[dict[str, Any]] = []
    settings = DemoSettings(FLAGSMITH_SERVER_ENV_KEY="ser.fake-key")
    fs_service = FlagsmithService(settings, client=_ExplodingFlagsmith())
    telemetry = TelemetryEmitter(settings, http_client=_make_mock_ingest_client(captured))
    app = create_app(settings=settings, flagsmith_service=fs_service, telemetry=telemetry)

    with TestClient(app) as client:
        resp = client.get("/api/flags", params={"user_id": "user_500"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["checkout_version"] == "v1"
        assert data["flagsmith_connected"] is False
        assert data["evaluation_source"] == "fallback_default"
        assert "timeout" in (data["error"] or "").lower()

        health = client.get("/api/health").json()
        assert health["flagsmith_connected"] is False


def test_simulator_max_500_users_and_neutral_scenario() -> None:
    captured: list[dict[str, Any]] = []
    settings = DemoSettings(FLAGSMITH_SERVER_ENV_KEY="")
    telemetry = TelemetryEmitter(settings, http_client=_make_mock_ingest_client(captured))
    app = create_app(settings=settings, telemetry=telemetry)

    with TestClient(app) as client:
        resp = client.post(
            "/api/simulate",
            json={"users_count": 500, "scenario": "neutral", "seed": 7},
        )
        assert resp.status_code == 200
        report = resp.json()
        assert report["evaluation_mode"] == "simulated_split"
        assert report["flagsmith_connected"] is False
        assert report["events_generated"] == 1500
        assert report["batches_sent"] == 3
        assert report["ingest_accepted"] == 1500
        assert report["control"]["exposures"] == 250
        assert report["treatment"]["exposures"] == 250

    assert [len(req["body"]["events"]) for req in captured] == [500, 500, 500]

