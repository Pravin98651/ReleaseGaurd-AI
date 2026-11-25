"""Security primitives: Fernet encryption, canonical action_hash, OAuth HMAC, XML sandbox, scrubbing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

_FERNET_TOKEN_RE = re.compile(r"gAAAAA[A-Za-z0-9_=-]{16,}")
_AUTH_HEADER_RE = re.compile(
    r"(?i)(authorization\s*[:=]\s*(?:bearer|token)\s+)([^\s,;\"']+)"
)
_BEARER_TOKEN_RE = re.compile(r"(?i)\b(bearer|token)\s+([A-Za-z0-9._~+/-]{12,}=*)")
_HEADER_KEY_RE = re.compile(
    r"(?i)((?:x-environment-key|x-ingest-key|x-api-key|api_key|apikey)\s*[:=]\s*)([^\s,;\"']+)"
)
_GITHUB_TOKEN_RE = re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{16,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b")
_GOOGLE_KEY_RE = re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b")

_SENSITIVE_DICT_KEYS = frozenset(
    {
        "token",
        "access_token",
        "refresh_token",
        "access_token_encrypted",
        "refresh_token_encrypted",
        "org_token",
        "pat",
        "secret",
        "client_secret",
        "secret_key",
        "encryption_key",
        "password",
        "api_key",
        "gemini_api_key",
        "authorization",
        "x-environment-key",
        "x-ingest-key",
        "ingest_key",
        "environment_key",
    }
)

_UNTRUSTED_TAG_OPEN_RE = re.compile(r"<\s*untrusted_external_data\b", re.IGNORECASE)
_UNTRUSTED_TAG_CLOSE_RE = re.compile(r"<\s*/\s*untrusted_external_data\s*>", re.IGNORECASE)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

MAX_DIFF_CHARS = 32000
MAX_FILE_CHARS = 16000


def get_fernet(encryption_key: str | None = None) -> Fernet:
    """Build a Fernet cipher from explicit key or active settings."""
    key = (encryption_key if encryption_key is not None else get_settings().ENCRYPTION_KEY).strip()
    if not key:
        raise ValueError("ENCRYPTION_KEY is empty and cannot initialize Fernet.")
    return Fernet(key.encode("ascii"))


def encrypt_token(plaintext: str, encryption_key: str | None = None) -> str:
    """Encrypt an integration secret for DB storage using Fernet."""
    if not plaintext:
        raise ValueError("Cannot encrypt an empty token.")
    fernet = get_fernet(encryption_key)
    return fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_token(ciphertext: str, encryption_key: str | None = None) -> str:
    """Decrypt a stored Fernet-encrypted integration token."""
    if not ciphertext:
        raise ValueError("Cannot decrypt an empty ciphertext.")
    fernet = get_fernet(encryption_key)
    try:
        return fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Failed to decrypt token: invalid key or corrupted ciphertext.") from exc


def make_token_hint(token: str) -> str:
    """Return a masked suffix suitable for UI display without leaking secret material."""
    cleaned = token.strip()
    if len(cleaned) <= 4:
        return "••••"
    return f"••••{cleaned[-4:]}"


def compute_action_hash(
    action_type: str,
    target_resource_type: str,
    target_resource_id: str,
    environment_name: str | None,
    action_payload: dict[str, Any],
) -> str:
    """Compute canonical lowercase hex SHA-256 hash per docs/database-and-policies.md Sec 5.3."""
    canonical_obj = {
        "action_type": action_type,
        "target_resource_type": target_resource_type,
        "target_resource_id": str(target_resource_id),
        "environment_name": environment_name or "",
        "action_payload": action_payload,
    }
    serialized = json.dumps(
        canonical_obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def sign_oauth_state(
    payload: dict[str, Any] | None = None,
    secret_key: str | None = None,
    ttl_seconds: int = 600,
) -> str:
    """Sign an HMAC-SHA256 OAuth state token with nonce and expiration timestamp."""
    key = (secret_key if secret_key is not None else get_settings().SECRET_KEY).encode("utf-8")
    now = int(time.time())
    body = {
        "nonce": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + ttl_seconds,
        "data": payload or {},
    }
    raw_bytes = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(raw_bytes).decode("ascii").rstrip("=")
    sig = hmac.new(key, b64_payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{b64_payload}.{sig}"


def verify_oauth_state(state: str, secret_key: str | None = None) -> dict[str, Any]:
    """Verify HMAC-SHA256 OAuth state signature and TTL, returning decoded payload."""
    if not state or "." not in state:
        raise ValueError("Malformed OAuth state parameter.")
    b64_payload, provided_sig = state.split(".", 1)
    key = (secret_key if secret_key is not None else get_settings().SECRET_KEY).encode("utf-8")
    expected_sig = hmac.new(key, b64_payload.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(provided_sig, expected_sig):
        raise ValueError("Invalid OAuth state signature.")

    padded = b64_payload + "=" * (-len(b64_payload) % 4)
    try:
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception as exc:
        raise ValueError("Corrupted OAuth state payload.") from exc

    exp = int(decoded.get("exp", 0))
    if time.time() > exp:
        raise ValueError("OAuth state token has expired.")
    return decoded


def verify_ingest_key(provided_key: str | None, expected_key: str | None = None) -> bool:
    """Constant-time check for X-Ingest-Key event ingestion authentication."""
    expected = expected_key if expected_key is not None else get_settings().EVENT_INGEST_KEY
    if not provided_key or not expected:
        return False
    return hmac.compare_digest(provided_key.strip(), expected.strip())


def scrub_secrets(text: str, extra_secrets: list[str] | None = None) -> str:
    """Redact tokens, Fernet ciphertexts, API keys, and configured secret values from text."""
    if not text:
        return text

    scrubbed = _FERNET_TOKEN_RE.sub("[REDACTED_FERNET]", text)
    scrubbed = _GITHUB_TOKEN_RE.sub("[REDACTED_GITHUB_TOKEN]", scrubbed)
    scrubbed = _GOOGLE_KEY_RE.sub("[REDACTED_API_KEY]", scrubbed)
    scrubbed = _AUTH_HEADER_RE.sub(r"\1[REDACTED]", scrubbed)
    scrubbed = _HEADER_KEY_RE.sub(r"\1[REDACTED]", scrubbed)
    scrubbed = _BEARER_TOKEN_RE.sub(r"\1 [REDACTED]", scrubbed)

    secrets_to_mask = list(get_settings().secret_values_for_scrubbing())
    if extra_secrets:
        secrets_to_mask.extend(s for s in extra_secrets if s and len(s) >= 6)

    for secret in sorted(set(secrets_to_mask), key=len, reverse=True):
        if secret in scrubbed:
            scrubbed = scrubbed.replace(secret, "[REDACTED]")
    return scrubbed


def redact_dict(data: Any, extra_secrets: list[str] | None = None) -> Any:
    """Recursively redact sensitive keys and secret patterns in JSON-like structures."""
    if isinstance(data, dict):
        redacted: dict[str, Any] = {}
        for k, v in data.items():
            key_lower = str(k).lower()
            if key_lower in _SENSITIVE_DICT_KEYS or (
                any(part in key_lower for part in ("secret", "password", "encrypted"))
                and "hint" not in key_lower
            ):
                redacted[k] = "[REDACTED]"
            else:
                redacted[k] = redact_dict(v, extra_secrets=extra_secrets)
        return redacted
    if isinstance(data, list):
        return [redact_dict(item, extra_secrets=extra_secrets) for item in data]
    if isinstance(data, tuple):
        return [redact_dict(item, extra_secrets=extra_secrets) for item in data]
    if isinstance(data, str):
        return scrub_secrets(data, extra_secrets=extra_secrets)
    return data


def _escape_xml_attr(value: Any) -> str:
    text = str(value)
    return (
        text.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def wrap_untrusted(
    source: str,
    metadata: dict[str, Any] | None = None,
    text: str = "",
    max_chars: int = MAX_DIFF_CHARS,
) -> str:
    """Wrap untrusted external content in hardened <untrusted_external_data> XML tags.

    Implements the prompt-injection defense contract in docs/architecture.md Sec 4.4:
    - Escapes literal <untrusted_external_data / </untrusted_external_data> tags
    - Strips ASCII control characters except newline and tab
    - Caps payload size to max_chars
    """
    sanitized = _CONTROL_CHARS_RE.sub("", text or "")
    sanitized = _UNTRUSTED_TAG_CLOSE_RE.sub("[ESCAPED_CLOSE_UNTRUSTED_TAG]", sanitized)
    sanitized = _UNTRUSTED_TAG_OPEN_RE.sub("[ESCAPED_OPEN_UNTRUSTED_TAG", sanitized)

    if len(sanitized) > max_chars:
        sanitized = sanitized[:max_chars] + "\n... [TRUNCATED]"

    attrs = [f'source="{_escape_xml_attr(source)}"']
    if metadata:
        for key, value in metadata.items():
            if value is not None:
                attrs.append(f'{_escape_xml_attr(key)}="{_escape_xml_attr(value)}"')
    attrs.append('trust="UNTRUSTED"')
    attr_str = " ".join(attrs)
    return f"<untrusted_external_data {attr_str}>\n{sanitized}\n</untrusted_external_data>"
