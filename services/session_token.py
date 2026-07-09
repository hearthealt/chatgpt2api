from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from services.config import config

TOKEN_PREFIX = "sess."
_DEFAULT_TTL_DAYS = 7


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _secret() -> bytes:
    """会话令牌签名密钥，从部署管理员密钥派生（无需额外配置）。"""
    auth_key = str(config.auth_key or "").strip()
    return hashlib.sha256(f"session:{auth_key}".encode("utf-8")).digest()


def _sign(payload_bytes: bytes) -> str:
    signature = hmac.new(_secret(), payload_bytes, hashlib.sha256).digest()
    return _b64url_encode(signature)


def _ttl_seconds() -> int:
    try:
        days = int(config.get_user_access_settings().get("session_ttl_days", _DEFAULT_TTL_DAYS))
    except (TypeError, ValueError):
        days = _DEFAULT_TTL_DAYS
    days = max(1, min(days, 365))
    return days * 24 * 3600


def issue_token(user: dict[str, Any]) -> str:
    """为用户签发一个无状态签名会话令牌。"""
    now = int(time.time())
    payload = {
        "uid": str(user.get("id") or ""),
        "role": str(user.get("role") or "user"),
        "pv": int(user.get("password_version") or 1),
        "iat": now,
        "exp": now + _ttl_seconds(),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"{TOKEN_PREFIX}{_b64url_encode(payload_bytes)}.{_sign(payload_bytes)}"


def verify_token(token: str) -> dict[str, Any] | None:
    """校验令牌签名与有效期，返回 payload（含 uid/role/pv）或 None。

    注意：调用方仍需比对 pv 与用户当前 password_version、并校验 enabled。
    """
    candidate = str(token or "").strip()
    if not candidate.startswith(TOKEN_PREFIX):
        return None
    body = candidate[len(TOKEN_PREFIX):]
    parts = body.split(".")
    if len(parts) != 2:
        return None
    payload_part, signature_part = parts
    try:
        payload_bytes = _b64url_decode(payload_part)
    except (ValueError, base64.binascii.Error):
        return None
    expected_signature = _sign(payload_bytes)
    if not hmac.compare_digest(expected_signature, signature_part):
        return None
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        exp = int(payload.get("exp") or 0)
    except (TypeError, ValueError):
        return None
    if exp and exp < int(time.time()):
        return None
    return payload
