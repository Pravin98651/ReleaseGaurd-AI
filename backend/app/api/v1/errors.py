"""Standard error envelope and global FastAPI exception handlers."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.agents.gemini_client import GeminiConfigurationError
from app.core.security import redact_dict, scrub_secrets
from app.integrations.flagsmith_client import FlagsmithAPIError
from app.integrations.github_client import GitHubAPIError
from app.services.policy_engine import (
    ActionHashMismatchError,
    ApprovalConflictError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
    PolicyViolationError,
    PreconditionDriftError,
    VerificationFailedError,
)


class APIException(Exception):
    """Application exception rendered as standard {"error": {...}} envelope."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def _request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if rid:
        return str(rid)
    return f"req_{uuid.uuid4().hex[:12]}"


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": scrub_secrets(str(message)),
                "details": redact_dict(details or {}),
                "request_id": _request_id(request),
            }
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach canonical JSON error envelope handlers to FastAPI app."""

    @app.exception_handler(APIException)
    async def handle_api_exception(request: Request, exc: APIException) -> JSONResponse:
        return _error_response(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            return _error_response(
                request,
                status_code=exc.status_code,
                code=str(detail["code"]),
                message=str(detail.get("message") or "HTTP error"),
                details=detail.get("details") if isinstance(detail.get("details"), dict) else {},
            )
        return _error_response(
            request,
            status_code=exc.status_code,
            code=f"HTTP_{exc.status_code}",
            message=str(detail) if detail else "Request failed",
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=422,
            code="VALIDATION_ERROR",
            message="Request validation failed.",
            details={"errors": exc.errors()},
        )

    @app.exception_handler(PolicyViolationError)
    async def handle_policy_violation(
        request: Request, exc: PolicyViolationError
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=403,
            code="POLICY_VIOLATION",
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(ApprovalNotFoundError)
    async def handle_approval_not_found(
        request: Request, exc: ApprovalNotFoundError
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=404,
            code="APPROVAL_NOT_FOUND",
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(ActionHashMismatchError)
    async def handle_hash_mismatch(
        request: Request, exc: ActionHashMismatchError
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=409,
            code="APPROVAL_ACTION_HASH_MISMATCH",
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(ApprovalExpiredError)
    async def handle_approval_expired(
        request: Request, exc: ApprovalExpiredError
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=409,
            code="APPROVAL_EXPIRED",
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(ApprovalConflictError)
    async def handle_approval_conflict(
        request: Request, exc: ApprovalConflictError
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=409,
            code="APPROVAL_CONFLICT",
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(PreconditionDriftError)
    async def handle_precondition_drift(
        request: Request, exc: PreconditionDriftError
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=409,
            code="APPROVAL_REVALIDATION_FAILED",
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(VerificationFailedError)
    async def handle_verification_failed(
        request: Request, exc: VerificationFailedError
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=502,
            code="FLAGSMITH_VERIFICATION_FAILED",
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(GitHubAPIError)
    async def handle_github_error(request: Request, exc: GitHubAPIError) -> JSONResponse:
        status = 502 if exc.status_code >= 500 else (exc.status_code if exc.status_code >= 400 else 502)
        return _error_response(
            request,
            status_code=status,
            code="GITHUB_API_ERROR",
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(FlagsmithAPIError)
    async def handle_flagsmith_error(request: Request, exc: FlagsmithAPIError) -> JSONResponse:
        status = 502 if exc.status_code >= 500 else (exc.status_code if exc.status_code >= 400 else 502)
        return _error_response(
            request,
            status_code=status,
            code="FLAGSMITH_API_ERROR",
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(GeminiConfigurationError)
    async def handle_gemini_unconfigured(
        request: Request, exc: GeminiConfigurationError
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=503,
            code="GEMINI_NOT_CONFIGURED",
            message=str(exc),
        )

    @app.exception_handler(ValueError)
    async def handle_value_error(request: Request, exc: ValueError) -> JSONResponse:
        return _error_response(
            request,
            status_code=400,
            code="BAD_REQUEST",
            message=str(exc),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        return _error_response(
            request,
            status_code=500,
            code="INTERNAL_SERVER_ERROR",
            message=f"Unexpected server error: {type(exc).__name__}",
        )
