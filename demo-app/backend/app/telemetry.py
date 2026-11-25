"""Async telemetry client emitting schema-compliant events to ReleaseGuard AI."""

from __future__ import annotations

import hashlib
import logging
import re
from collections import deque
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

import httpx

from app.config import DemoSettings
from app.schemas import TelemetryEventRecord

logger = logging.getLogger(__name__)

_SAFE_PSEUDONYM_RE = re.compile(r"^(?:shopper|user|usr|cust|demo)_[a-zA-Z0-9_-]{1,32}$")

ALLOWED_EVENT_TYPES = frozenset(
    {
        "feature_variation_assigned",
        "checkout_started",
        "checkout_completed",
        "checkout_abandoned",
        "payment_failed",
        "payment_completed",
        "product_clicked_from_recommendation",
        "add_to_cart_from_recommendation",
        "search_result_clicked",
        "product_added_after_search",
    }
)


def pseudonymize_user_id(user_id: str, salt: str = "novashop-releaseguard-demo") -> str:
    """Return a privacy-safe pseudonymous identifier without leaking PII such as emails."""
    cleaned = user_id.strip()
    if _SAFE_PSEUDONYM_RE.fullmatch(cleaned):
        return cleaned
    digest = hashlib.sha256(f"{salt}:{cleaned}".encode()).hexdigest()[:16]
    return f"usr_{digest}"


def utc_iso_now(dt: datetime | None = None) -> str:
    """Format a UTC datetime as ISO-8601 with trailing Z."""
    target = dt or datetime.now(UTC)
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    return target.astimezone(UTC).isoformat().replace("+00:00", "Z")


class TelemetryEmitter:
    """Buffers and dispatches demo checkout events to /api/v1/events/ingest."""

    def __init__(
        self,
        settings: DemoSettings,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=5.0)
        self._recent: deque[TelemetryEventRecord] = deque(maxlen=250)

    async def aclose(self) -> None:
        """Close the owned AsyncClient on shutdown."""
        if self._owns_client:
            await self._client.aclose()

    def build_event(
        self,
        *,
        event_type: str,
        user_id: str,
        variation_key: str,
        environment_name: str | None = None,
        session_key: str | None = None,
        numeric_value: float | None = None,
        properties: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Construct a canonical ReleaseGuard /api/v1/events/ingest event dict."""
        if event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError(f"Unsupported event_type: {event_type}")

        return {
            "event_id": f"evt_{uuid4().hex}",
            "flag_key": self._settings.CHECKOUT_FLAG_KEY,
            "environment_name": environment_name or self._settings.FLAGSMITH_ENVIRONMENT_NAME,
            "variation_key": variation_key,
            "event_type": event_type,
            "user_identifier": pseudonymize_user_id(user_id, self._settings.PSEUDONYM_SALT),
            "session_key": session_key,
            "numeric_value": round(numeric_value, 2) if numeric_value is not None else None,
            "properties": properties or {},
            "occurred_at": utc_iso_now(occurred_at),
        }

    async def emit_events(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Post events in <=500-item chunks to ReleaseGuard backend without crashing on downtime."""
        if not events:
            return {
                "batches_sent": 0,
                "accepted": 0,
                "deduplicated": 0,
                "failed_batches": 0,
                "error": None,
            }

        ingest_url = f"{self._settings.RELEASEGUARD_API_URL.rstrip('/')}/api/v1/events/ingest"
        headers = {
            "X-Ingest-Key": self._settings.RELEASEGUARD_INGEST_KEY,
            "Content-Type": "application/json",
        }

        batches_sent = 0
        accepted_total = 0
        deduplicated_total = 0
        failed_batches = 0
        last_error: str | None = None

        chunk_size = 500
        for idx in range(0, len(events), chunk_size):
            batch = events[idx : idx + chunk_size]
            batches_sent += 1
            delivery_status: Literal["delivered", "offline", "error"] = "delivered"
            delivery_detail: str | None = None

            try:
                response = await self._client.post(
                    ingest_url,
                    json={"events": batch},
                    headers=headers,
                )
                if 200 <= response.status_code < 300:
                    payload = response.json() if response.content else {}
                    accepted_total += int(payload.get("accepted", len(batch)))
                    deduplicated_total += int(payload.get("deduplicated", 0))
                    delivery_detail = f"HTTP {response.status_code}"
                else:
                    failed_batches += 1
                    delivery_status = "error"
                    delivery_detail = f"HTTP {response.status_code}: {response.text[:160]}"
                    last_error = delivery_detail
            except httpx.HTTPError as exc:
                failed_batches += 1
                delivery_status = "offline"
                delivery_detail = f"ReleaseGuard unreachable: {exc}"
                last_error = delivery_detail
                logger.debug("Telemetry delivery skipped (%s)", exc)

            for item in batch:
                self._recent.appendleft(
                    TelemetryEventRecord(
                        event_id=item["event_id"],
                        flag_key=item["flag_key"],
                        environment_name=item["environment_name"],
                        variation_key=item["variation_key"],
                        event_type=item["event_type"],
                        user_identifier=item["user_identifier"],
                        session_key=item.get("session_key"),
                        numeric_value=item.get("numeric_value"),
                        properties=item.get("properties") or {},
                        occurred_at=item["occurred_at"],
                        delivery_status=delivery_status,
                        delivery_detail=delivery_detail,
                    )
                )

        return {
            "batches_sent": batches_sent,
            "accepted": accepted_total,
            "deduplicated": deduplicated_total,
            "failed_batches": failed_batches,
            "error": last_error,
        }

    def get_recent(self, limit: int = 50) -> list[TelemetryEventRecord]:
        """Return the most recent buffered telemetry events."""
        return list(self._recent)[: max(1, min(limit, 250))]

    @property
    def total_buffered(self) -> int:
        """Number of buffered events currently held in memory."""
        return len(self._recent)
