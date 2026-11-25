"""Official Flagsmith Python SDK wrapper with graceful deterministic fallback."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Literal

import requests
from flagsmith import Flagsmith
from flagsmith.exceptions import FlagsmithClientError
from flagsmith.models import DefaultFlag

from app.config import DemoSettings

logger = logging.getLogger(__name__)

_TREATMENT_VALUES = {"v2", "treatment", "checkout_v2", "express", "true", "1"}
_CONTROL_VALUES = {"v1", "control", "checkout_v1", "classic", "false", "0"}


@dataclass(frozen=True)
class FlagEvaluationResult:
    """Normalized checkout flag evaluation output."""

    flag_key: str
    enabled: bool
    value: Any
    variation_key: str
    checkout_version: Literal["v1", "v2"]
    flagsmith_connected: bool
    evaluation_source: Literal["flagsmith_sdk", "fallback_default"]
    error: str | None = None


def normalize_flag_state(enabled: bool, value: Any) -> tuple[Literal["v1", "v2"], str]:
    """Map boolean or multivariate Flagsmith flag values into version and variation_key."""
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in _TREATMENT_VALUES:
            canonical_key = cleaned if cleaned in {"v2", "treatment"} else "treatment"
            return "v2", canonical_key
        if cleaned in _CONTROL_VALUES:
            canonical_key = cleaned if cleaned in {"v1", "control"} else "control"
            return "v1", canonical_key

    if enabled:
        return "v2", "treatment"
    return "v1", "control"


class FlagsmithService:
    """Evaluates identity flags via Flagsmith SDK with fail-safe fallback to v1."""

    def __init__(self, settings: DemoSettings, client: Any | None = None) -> None:
        self._settings = settings
        self._injected_client = client is not None
        self._client: Any | None = client
        self._init_error: str | None = None
        self._last_call_failed: bool = False

        if self._client is None and self.is_configured:
            try:
                self._client = Flagsmith(
                    environment_key=settings.FLAGSMITH_SERVER_ENV_KEY.strip(),
                    api_url=settings.FLAGSMITH_EDGE_API_URL.strip(),
                    request_timeout_seconds=5,
                    default_flag_handler=lambda _name: DefaultFlag(enabled=False, value="v1"),
                )
            except (
                FlagsmithClientError,
                requests.RequestException,
                OSError,
                RuntimeError,
                ValueError,
                TypeError,
            ) as exc:  # pragma: no cover - defensive guard
                self._init_error = str(exc)
                self._last_call_failed = True
                logger.warning("Failed to initialize Flagsmith SDK client: %s", exc)

    @property
    def flag_key(self) -> str:
        """Configured checkout feature flag key."""
        return self._settings.CHECKOUT_FLAG_KEY

    @property
    def is_configured(self) -> bool:
        """Return True if an environment key was supplied or a mock client was injected."""
        return self._injected_client or bool(self._settings.FLAGSMITH_SERVER_ENV_KEY.strip())

    @property
    def flagsmith_connected(self) -> bool:
        """Return True when SDK client is configured and healthy."""
        return self.is_configured and self._client is not None and not self._last_call_failed

    async def evaluate_checkout_flag(
        self,
        user_id: str,
        traits: dict[str, Any] | None = None,
    ) -> FlagEvaluationResult:
        """Evaluate checkout_v2 for the given user_id using Flagsmith identity flags."""
        flag_key = self._settings.CHECKOUT_FLAG_KEY

        if not self.is_configured or self._client is None:
            return FlagEvaluationResult(
                flag_key=flag_key,
                enabled=False,
                value="v1",
                variation_key="v1",
                checkout_version="v1",
                flagsmith_connected=False,
                evaluation_source="fallback_default",
                error=self._init_error or "FLAGSMITH_SERVER_ENV_KEY is not configured.",
            )

        try:
            flags = await asyncio.to_thread(
                self._client.get_identity_flags,
                identifier=user_id,
                traits=traits or {},
            )
            flag = flags.get_flag(flag_key)
            enabled = bool(getattr(flag, "enabled", False))
            value = getattr(flag, "value", None)
            checkout_version, variation_key = normalize_flag_state(enabled=enabled, value=value)
            self._last_call_failed = False
            return FlagEvaluationResult(
                flag_key=flag_key,
                enabled=enabled,
                value=value if value is not None else ("v2" if checkout_version == "v2" else "v1"),
                variation_key=variation_key,
                checkout_version=checkout_version,
                flagsmith_connected=True,
                evaluation_source="flagsmith_sdk",
                error=None,
            )
        except (
            FlagsmithClientError,
            requests.RequestException,
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
        ) as exc:
            self._last_call_failed = True
            logger.warning("Flagsmith evaluation failed for user %s: %s", user_id, exc)
            return FlagEvaluationResult(
                flag_key=flag_key,
                enabled=False,
                value="v1",
                variation_key="v1",
                checkout_version="v1",
                flagsmith_connected=False,
                evaluation_source="fallback_default",
                error=str(exc),
            )

    async def track_exposure(
        self,
        *,
        user_id: str,
        flag_key: str,
        variation: str,
    ) -> bool:
        """Emit an exposure event to Flagsmith SDK when supported (safe no-op offline)."""
        if not self.is_configured or self._client is None:
            return False
        fn = getattr(self._client, "track_exposure_event", None)
        if not callable(fn):
            return False
        try:
            await asyncio.to_thread(fn, identifier=user_id, feature_name=flag_key, value=variation)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("Flagsmith track_exposure_event skipped: %s", exc)
            return False

    async def track_conversion(
        self,
        *,
        user_id: str,
        event_name: str,
        value: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Emit a conversion/metric event to Flagsmith SDK and flush when supported."""
        if not self.is_configured or self._client is None:
            return False
        fn = getattr(self._client, "track_event", None)
        if not callable(fn):
            return False
        try:
            kwargs: dict[str, Any] = {"identifier": user_id, "event_name": event_name}
            if value is not None:
                kwargs["value"] = value
            if metadata:
                kwargs["metadata"] = metadata
            await asyncio.to_thread(fn, **kwargs)
            flush_fn = getattr(self._client, "flush_events", None)
            if callable(flush_fn):
                await asyncio.to_thread(flush_fn)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("Flagsmith track_event skipped: %s", exc)
            return False
