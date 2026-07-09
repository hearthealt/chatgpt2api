from __future__ import annotations

from pathlib import Path
from threading import Event, Thread

from fastapi import HTTPException, Request

from services.account_service import account_service
from services.auth_service import auth_service
from services.config import config
from services.session_token import verify_token
from services.user_service import user_service

BASE_DIR = Path(__file__).resolve().parents[1]
WEB_DIST_DIR = BASE_DIR / "web_dist"


def extract_bearer_token(authorization: str | None) -> str:
    scheme, _, value = str(authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return ""
    return value.strip()


def _legacy_admin_identity(token: str) -> dict[str, object] | None:
    auth_key = str(config.auth_key or "").strip()
    if auth_key and token == auth_key:
        return {"id": "admin", "name": "管理员", "role": "admin"}
    return None


def _session_identity(token: str) -> dict[str, object] | None:
    """校验 Web 会话令牌（sess.xxx）并映射为 identity。"""
    payload = verify_token(token)
    if not payload:
        return None
    user_id = str(payload.get("uid") or "").strip()
    if not user_id:
        return None
    user = user_service.get_user(user_id)
    if not user or not bool(user.get("enabled", True)):
        return None
    try:
        current_pv = int(user.get("password_version") or 1)
        token_pv = int(payload.get("pv") or 0)
    except (TypeError, ValueError):
        return None
    if token_pv != current_pv:
        return None
    return {
        "id": str(user.get("id") or ""),
        "name": str(user.get("username") or ""),
        "role": str(user.get("role") or "user"),
    }


def _key_owner_enabled(identity: dict[str, object] | None) -> bool:
    """API key 归属用户若被禁用，则其密钥也应失效。"""
    if identity is None:
        return False
    owner_user_id = str(identity.get("owner_user_id") or "").strip()
    if not owner_user_id:
        return True  # 无归属（历史密钥）不受影响
    user = user_service.get_user(owner_user_id)
    if user is None:
        return True  # 归属用户已删除，密钥应已被连带删除；这里放行不影响
    return bool(user.get("enabled", True))


def require_identity(authorization: str | None) -> dict[str, object]:
    token = extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail={"error": "密钥无效或已失效，请重新登录"})
    legacy = _legacy_admin_identity(token)
    if legacy is not None:
        return legacy
    session_identity = _session_identity(token)
    if session_identity is not None:
        return session_identity
    key_identity = auth_service.authenticate(token)
    if key_identity is not None and _key_owner_enabled(key_identity):
        return {
            "id": key_identity.get("id"),
            "name": key_identity.get("name"),
            "role": key_identity.get("role"),
        }
    raise HTTPException(status_code=401, detail={"error": "密钥无效或已失效，请重新登录"})


def require_auth_key(authorization: str | None) -> None:
    require_identity(authorization)


def require_admin(authorization: str | None) -> dict[str, object]:
    identity = require_identity(authorization)
    if identity.get("role") != "admin":
        raise HTTPException(status_code=403, detail={"error": "需要管理员权限才能执行这个操作"})
    return identity


def resolve_image_base_url(request: Request) -> str:
    return config.base_url or f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"


def raise_image_quota_error(exc: Exception) -> None:
    message = str(exc)
    status_code = int(getattr(exc, "status_code", 0) or 0)
    code = str(getattr(exc, "code", "") or getattr(exc, "kind", "") or "").strip()
    if status_code:
        raise HTTPException(status_code=status_code, detail={"error": code or message}) from exc
    lower = message.lower()
    if "image_account_selection:quota_exhausted" in lower or "insufficient_quota" in lower:
        raise HTTPException(status_code=429, detail={"error": "insufficient_quota"}) from exc
    if "image_account_selection:" in lower or "no available image quota" in lower:
        raise HTTPException(status_code=503, detail={"error": message}) from exc
    raise HTTPException(status_code=502, detail={"error": message}) from exc


def sanitize_cpa_pool(pool: dict | None) -> dict | None:
    if not isinstance(pool, dict):
        return None
    return {key: value for key, value in pool.items() if key != "secret_key"}


def sanitize_cpa_pools(pools: list[dict]) -> list[dict]:
    return [sanitized for pool in pools if (sanitized := sanitize_cpa_pool(pool)) is not None]


def sanitize_sub2api_server(server: dict | None) -> dict | None:
    if not isinstance(server, dict):
        return None
    sanitized = {key: value for key, value in server.items() if key not in {"password", "api_key"}}
    sanitized["has_api_key"] = bool(str(server.get("api_key") or "").strip())
    return sanitized


def sanitize_sub2api_servers(servers: list[dict]) -> list[dict]:
    return [sanitized for server in servers if (sanitized := sanitize_sub2api_server(server)) is not None]


def start_limited_account_watcher(stop_event: Event) -> Thread:
    interval_seconds = config.refresh_account_interval_minute * 60

    def worker() -> None:
        while not stop_event.is_set():
            try:
                limited_tokens = account_service.list_limited_tokens()
                normal_tokens = account_service.list_normal_tokens()
                expiring_tokens = account_service.list_expiring_access_tokens()
                keepalive_tokens = account_service.list_refresh_token_keepalive_tokens()
                tokens = list(dict.fromkeys([*limited_tokens, *normal_tokens, *expiring_tokens]))
                expiring_token_set = set(expiring_tokens)
                keepalive_tokens = [token for token in keepalive_tokens if token not in expiring_token_set]
                if tokens:
                    print(
                        "[account-watcher] checking "
                        f"{len(limited_tokens)} limited accounts, "
                        f"{len(normal_tokens)} normal accounts, "
                        f"{len(expiring_tokens)} expiring access tokens"
                    )
                    account_service.refresh_accounts(tokens)
                if keepalive_tokens:
                    print(f"[account-watcher] keepalive {len(keepalive_tokens)} refresh tokens")
                    result = account_service.keepalive_refresh_tokens(keepalive_tokens)
                    if result.get("errors"):
                        print(f"[account-watcher] keepalive errors: {result['errors']}")
            except Exception as exc:
                print(f"[account-watcher] fail {exc}")
            stop_event.wait(interval_seconds)

    thread = Thread(target=worker, name="account-watcher", daemon=True)
    thread.start()
    return thread


def resolve_web_asset(requested_path: str) -> Path | None:
    if not WEB_DIST_DIR.exists():
        return None
    clean_path = requested_path.strip("/")
    base_dir = WEB_DIST_DIR.resolve()
    candidates = [base_dir / "index.html"] if not clean_path else [
        base_dir / Path(clean_path),
        base_dir / clean_path / "index.html",
        base_dir / f"{clean_path}.html",
    ]
    for candidate in candidates:
        try:
            candidate.resolve().relative_to(base_dir)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None
