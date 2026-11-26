"""FastAPI entrypoint for NovaShop Checkout v1 vs v2 Demo Backend (port 8001)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from app.checkout import process_checkout
from app.config import DemoSettings, get_settings
from app.features import compute_recommendations, execute_product_search
from app.flagsmith_service import FlagsmithService
from app.schemas import (
    CheckoutRequest,
    CheckoutResponse,
    FlagEvaluationResponse,
    HealthResponse,
    SimulateRequest,
    SimulateResponse,
    TelemetryRecentResponse,
)
from app.simulator import run_simulation
from app.telemetry import TelemetryEmitter, pseudonymize_user_id


def _get_flagsmith(request: Request) -> FlagsmithService:
    return request.app.state.flagsmith_service


def _get_telemetry(request: Request) -> TelemetryEmitter:
    return request.app.state.telemetry


def _get_cfg(request: Request) -> DemoSettings:
    return request.app.state.settings


FlagsmithDep = Annotated[FlagsmithService, Depends(_get_flagsmith)]
TelemetryDep = Annotated[TelemetryEmitter, Depends(_get_telemetry)]
ConfigDep = Annotated[DemoSettings, Depends(_get_cfg)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and cleanly tear down Flagsmith and telemetry resources."""
    settings = get_settings()
    if not hasattr(app.state, "flagsmith_service"):
        app.state.flagsmith_service = FlagsmithService(settings)
    if not hasattr(app.state, "telemetry"):
        app.state.telemetry = TelemetryEmitter(settings)
    try:
        yield
    finally:
        telemetry: TelemetryEmitter | None = getattr(app.state, "telemetry", None)
        if telemetry is not None:
            await telemetry.aclose()


def create_app(
    settings: DemoSettings | None = None,
    flagsmith_service: FlagsmithService | None = None,
    telemetry: TelemetryEmitter | None = None,
) -> FastAPI:
    """Create and configure the NovaShop demo FastAPI application."""
    cfg = settings or get_settings()
    app = FastAPI(
        title="NovaShop Checkout Demo API",
        version="1.0.0",
        description="Checkout v1 vs v2 Flagsmith-gated demo target for ReleaseGuard AI.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins_list or ["http://localhost:3000", "http://localhost:3001"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.settings = cfg
    app.state.flagsmith_service = flagsmith_service or FlagsmithService(cfg)
    app.state.telemetry = telemetry or TelemetryEmitter(cfg)

    @app.get("/api/health", response_model=HealthResponse)
    async def health(
        fs: FlagsmithDep,
        app_cfg: ConfigDep,
    ) -> HealthResponse:
        return HealthResponse(
            status="ok",
            flagsmith_connected=fs.flagsmith_connected,
            environment_name=app_cfg.FLAGSMITH_ENVIRONMENT_NAME,
            flag_key=app_cfg.CHECKOUT_FLAG_KEY,
            releaseguard_api_url=app_cfg.RELEASEGUARD_API_URL,
        )

    @app.get("/api/flags", response_model=FlagEvaluationResponse)
    async def evaluate_flags(
        fs: FlagsmithDep,
        telem: TelemetryDep,
        app_cfg: ConfigDep,
        user_id: Annotated[str, Query(min_length=1, max_length=128)],
        environment_name: Annotated[str | None, Query(max_length=64)] = None,
        session_key: Annotated[str | None, Query(max_length=128)] = None,
        plan: Annotated[str | None, Query(max_length=64)] = None,
        user_type: Annotated[str | None, Query(max_length=64)] = None,
        app_version: Annotated[str | None, Query(max_length=32)] = None,
        is_internal_tester: Annotated[bool | None, Query()] = None,
        organization_id: Annotated[str | None, Query(max_length=64)] = None,
    ) -> FlagEvaluationResponse:
        traits: dict[str, Any] = {}
        if plan:
            traits["plan"] = plan
            traits["subscription_plan"] = plan
        if user_type:
            traits["user_type"] = user_type
        if app_version:
            traits["app_version"] = app_version
        if is_internal_tester is not None:
            traits["is_internal_tester"] = is_internal_tester
        if organization_id:
            traits["organization_id"] = organization_id

        eval_res = await fs.evaluate_checkout_flag(user_id=user_id, traits=traits or None)
        env = environment_name or app_cfg.FLAGSMITH_ENVIRONMENT_NAME
        user_identifier = pseudonymize_user_id(user_id, app_cfg.PSEUDONYM_SALT)

        event = telem.build_event(
            event_type="feature_variation_assigned",
            user_id=user_id,
            variation_key=eval_res.variation_key,
            environment_name=env,
            session_key=session_key or f"browser_{user_id}",
            properties={
                "evaluation_source": eval_res.evaluation_source,
                "checkout_version": eval_res.checkout_version,
                "traits": traits,
            },
        )
        await telem.emit_events([event])
        if eval_res.evaluation_source == "flagsmith_sdk":
            await fs.track_exposure(
                user_id=user_id,
                flag_key=eval_res.flag_key,
                variation=eval_res.variation_key,
            )

        return FlagEvaluationResponse(
            user_id=user_id,
            user_identifier=user_identifier,
            flag_key=eval_res.flag_key,
            environment_name=env,
            enabled=eval_res.enabled,
            value=eval_res.value,
            variation_key=eval_res.variation_key,
            checkout_version=eval_res.checkout_version,
            flagsmith_connected=eval_res.flagsmith_connected,
            evaluation_source=eval_res.evaluation_source,
            event_emitted=True,
            error=eval_res.error,
        )

    @app.get("/api/recommendations")
    async def recommendations(
        fs: FlagsmithDep,
        telem: TelemetryDep,
        app_cfg: ConfigDep,
        user_id: Annotated[str, Query(min_length=1, max_length=128)] = "shopper_42",
        cart_skus: Annotated[str | None, Query(max_length=300)] = None,
        subscription_plan: Annotated[str | None, Query(max_length=64)] = None,
        user_type: Annotated[str | None, Query(max_length=64)] = None,
    ) -> dict[str, Any]:
        traits = {
            k: v
            for k, v in {
                "subscription_plan": subscription_plan,
                "user_type": user_type,
            }.items()
            if v
        }
        rec_flag = await fs.evaluate_named_flag(
            user_id=user_id,
            flag_key="recommendation_engine_v2",
            default_enabled=False,
            safe_default_value="control",
            traits=traits or None,
        )
        kill_flag = await fs.evaluate_named_flag(
            user_id=user_id,
            flag_key="enable_ai_recommendations",
            default_enabled=True,
            safe_default_value=True,
            traits=traits or None,
        )
        kill_enabled = bool(kill_flag.get("enabled", True))
        raw_var = str(rec_flag.get("value") or "control") if rec_flag.get("enabled") else "control"
        algo, products = compute_recommendations(
            variation=raw_var,
            kill_switch_enabled=kill_enabled,
            cart_skus=cart_skus.split(",") if cart_skus else [],
            traits=traits,
        )
        ev = telem.build_event(
            event_type="feature_variation_assigned",
            user_id=user_id,
            variation_key=algo,
            environment_name=app_cfg.FLAGSMITH_ENVIRONMENT_NAME,
            properties={
                "flag_key": "recommendation_engine_v2",
                "kill_switch_enabled": kill_enabled,
                "evaluation_source": rec_flag.get("evaluation_source"),
            },
        )
        ev["flag_key"] = "recommendation_engine_v2"
        await telem.emit_events([ev])
        if rec_flag.get("evaluation_source") == "flagsmith_sdk":
            await fs.track_exposure(
                user_id=user_id, flag_key="recommendation_engine_v2", variation=algo
            )
        return {
            "user_id": user_id,
            "flag_key": "recommendation_engine_v2",
            "variation_key": algo,
            "kill_switch_enabled": kill_enabled,
            "evaluation_source": rec_flag.get("evaluation_source"),
            "products": products,
        }

    @app.get("/api/search")
    async def search(
        fs: FlagsmithDep,
        telem: TelemetryDep,
        app_cfg: ConfigDep,
        q: Annotated[str, Query(max_length=120)] = "",
        user_id: Annotated[str, Query(min_length=1, max_length=128)] = "shopper_42",
    ) -> dict[str, Any]:
        search_flag = await fs.evaluate_named_flag(
            user_id=user_id,
            flag_key="smart_product_search",
            default_enabled=False,
            safe_default_value="control",
        )
        kill_flag = await fs.evaluate_named_flag(
            user_id=user_id,
            flag_key="enable_new_search_backend",
            default_enabled=True,
            safe_default_value=True,
        )
        kill_enabled = bool(kill_flag.get("enabled", True))
        raw_var = str(search_flag.get("value") or "control") if search_flag.get("enabled") else "control"
        algo, matches = execute_product_search(
            query=q, variation=raw_var, kill_switch_enabled=kill_enabled
        )
        ev = telem.build_event(
            event_type="feature_variation_assigned",
            user_id=user_id,
            variation_key=algo,
            environment_name=app_cfg.FLAGSMITH_ENVIRONMENT_NAME,
            properties={
                "flag_key": "smart_product_search",
                "query": q,
                "kill_switch_enabled": kill_enabled,
            },
        )
        ev["flag_key"] = "smart_product_search"
        await telem.emit_events([ev])
        if search_flag.get("evaluation_source") == "flagsmith_sdk":
            await fs.track_exposure(user_id=user_id, flag_key="smart_product_search", variation=algo)
        return {
            "query": q,
            "user_id": user_id,
            "flag_key": "smart_product_search",
            "variation_key": algo,
            "kill_switch_enabled": kill_enabled,
            "results": matches,
        }

    @app.post("/api/events/track")
    async def track_storefront_event(
        payload: dict[str, Any],
        fs: FlagsmithDep,
        telem: TelemetryDep,
        app_cfg: ConfigDep,
    ) -> dict[str, Any]:
        user_id = str(payload.get("user_id") or "shopper_42")
        event_type = str(payload.get("event_type") or "")
        flag_key = str(payload.get("flag_key") or app_cfg.CHECKOUT_FLAG_KEY)
        variation_key = str(payload.get("variation_key") or "control")
        sku = payload.get("sku")
        ev = telem.build_event(
            event_type=event_type,
            user_id=user_id,
            variation_key=variation_key,
            environment_name=app_cfg.FLAGSMITH_ENVIRONMENT_NAME,
            properties={"sku": sku, "source": "interactive_storefront"},
        )
        ev["flag_key"] = flag_key
        res = await telem.emit_events([ev])
        sdk_tracked = await fs.track_conversion(
            user_id=user_id,
            event_name=event_type,
            metadata={"flag_key": flag_key, "variation": variation_key, "sku": sku},
        )
        return {"tracked": True, "sdk_tracked": sdk_tracked, "ingest": res}

    @app.post("/api/checkout", response_model=CheckoutResponse)
    async def checkout(
        req: CheckoutRequest,
        fs: FlagsmithDep,
        telem: TelemetryDep,
        app_cfg: ConfigDep,
    ) -> CheckoutResponse:
        eval_res = await fs.evaluate_checkout_flag(user_id=req.user_id, traits=req.traits)
        pay_flag = await fs.evaluate_named_flag(
            user_id=req.user_id,
            flag_key="new_payment_flow",
            default_enabled=False,
            safe_default_value="control",
            traits=req.traits,
        )
        pay_kill = await fs.evaluate_named_flag(
            user_id=req.user_id,
            flag_key="enable_payment_v2",
            default_enabled=True,
            safe_default_value=True,
            traits=req.traits,
        )
        res = await process_checkout(
            req=req,
            flag_eval=eval_res,
            telemetry=telem,
            salt=app_cfg.PSEUDONYM_SALT,
            default_environment=app_cfg.FLAGSMITH_ENVIRONMENT_NAME,
        )
        pay_algo = (
            str(pay_flag.get("value") or "control")
            if (pay_flag.get("enabled") and pay_kill.get("enabled", True))
            else "control"
        )
        if eval_res.evaluation_source == "flagsmith_sdk":
            event_name = (
                "checkout_completed"
                if res.status == "completed"
                else ("checkout_abandoned" if res.status == "abandoned" else "payment_failed")
            )
            await fs.track_conversion(
                user_id=req.user_id,
                event_name=event_name,
                value=req.cart_total if res.status == "completed" else None,
                metadata={"checkout_version": res.checkout_version, "payment_flow": pay_algo},
            )
            if res.status == "completed":
                await fs.track_conversion(
                    user_id=req.user_id,
                    event_name="payment_completed",
                    value=req.cart_total,
                    metadata={"payment_flow": pay_algo},
                )
        return res

    @app.post("/api/simulate", response_model=SimulateResponse)
    async def simulate(
        req: SimulateRequest,
        fs: FlagsmithDep,
        telem: TelemetryDep,
        app_cfg: ConfigDep,
    ) -> SimulateResponse:
        return await run_simulation(
            req=req,
            flagsmith_service=fs,
            telemetry=telem,
            default_environment=app_cfg.FLAGSMITH_ENVIRONMENT_NAME,
        )

    @app.get("/api/telemetry/recent", response_model=TelemetryRecentResponse)
    async def recent_telemetry(
        telem: TelemetryDep,
        app_cfg: ConfigDep,
        limit: Annotated[int, Query(ge=1, le=250)] = 50,
    ) -> TelemetryRecentResponse:
        return TelemetryRecentResponse(
            events=telem.get_recent(limit=limit),
            total_buffered=telem.total_buffered,
            releaseguard_api_url=app_cfg.RELEASEGUARD_API_URL,
        )

    return app


app = create_app()
