"""Pydantic v2 request and response schemas for the NovaShop demo backend."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Health and Flagsmith connectivity status response."""

    status: str = "ok"
    flagsmith_connected: bool
    environment_name: str
    flag_key: str
    releaseguard_api_url: str


class FlagEvaluationResponse(BaseModel):
    """Result of evaluating the checkout_v2 flag for a shopper."""

    user_id: str
    user_identifier: str
    flag_key: str
    environment_name: str
    enabled: bool
    value: Any = None
    variation_key: str
    checkout_version: Literal["v1", "v2"]
    flagsmith_connected: bool
    evaluation_source: Literal["flagsmith_sdk", "fallback_default"]
    event_emitted: bool
    error: str | None = None


class CheckoutRequest(BaseModel):
    """Incoming checkout execution payload."""

    user_id: str = Field(..., min_length=1, max_length=128)
    cart_total: float = Field(..., gt=0, le=100000)
    payment_method: str = Field(default="card", min_length=1, max_length=64)
    promo_code: str | None = Field(default=None, max_length=64)
    wallet_provider: str | None = Field(default=None, max_length=64)
    simulate_payment_bug: bool = False
    abandon: bool = False
    environment_name: str | None = Field(default=None, max_length=64)
    traits: dict[str, Any] | None = None
    items: list[dict[str, Any]] | None = None
    shipping: dict[str, Any] | None = None


class CheckoutResponse(BaseModel):
    """Structured checkout result across v1 and v2 flows."""

    order_id: str
    session_key: str
    status: Literal["completed", "abandoned", "payment_failed"]
    checkout_version: Literal["v1", "v2"]
    variation_key: str
    flag_key: str
    environment_name: str
    user_identifier: str
    subtotal: float
    discount_amount: float
    total_charged: float
    promo_applied: str | None = None
    payment_method: str
    steps_completed: list[str]
    message: str
    error_code: str | None = None
    error_message: str | None = None
    flagsmith_connected: bool
    emitted_events: list[str]


class SimulateRequest(BaseModel):
    """Simulation parameters for generating cohort experiment journeys."""

    users_count: int = Field(default=120, ge=1, le=500)
    scenario: Literal["healthy_v2", "buggy_v2", "neutral"] = "healthy_v2"
    environment_name: str | None = Field(default=None, max_length=64)
    split_mode: Literal["auto", "flagsmith", "simulated_50_50"] = "auto"
    seed: int = 42


class VariantMetrics(BaseModel):
    """Aggregated conversion and error summary for one cohort variant."""

    variation_key: str
    exposures: int
    started: int
    completed: int
    abandoned: int
    payment_failed: int
    conversion_rate: float
    error_rate: float
    avg_order_value: float


class SimulateResponse(BaseModel):
    """Aggregated simulation report returned by POST /api/simulate."""

    scenario: Literal["healthy_v2", "buggy_v2", "neutral"]
    users_count: int
    evaluation_mode: Literal["flagsmith_live", "simulated_split"]
    flagsmith_connected: bool
    environment_name: str
    flag_key: str
    events_generated: int
    batches_sent: int
    ingest_accepted: int
    ingest_deduplicated: int
    ingest_failed_batches: int
    ingest_error: str | None = None
    control: VariantMetrics
    treatment: VariantMetrics
    sample_events: list[dict[str, Any]]


class TelemetryEventRecord(BaseModel):
    """Recorded telemetry event and delivery status for UI inspection."""

    event_id: str
    flag_key: str
    environment_name: str
    variation_key: str
    event_type: str
    user_identifier: str
    session_key: str | None = None
    numeric_value: float | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    occurred_at: str
    delivery_status: Literal["delivered", "offline", "error"]
    delivery_detail: str | None = None


class TelemetryRecentResponse(BaseModel):
    """Response wrapper for recent telemetry events."""

    events: list[TelemetryEventRecord]
    total_buffered: int
    releaseguard_api_url: str
