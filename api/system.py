from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from api.support import require_admin, require_identity, resolve_image_base_url
from services.backup_service import BackupError, backup_service
from services.config import config
from services.image_service import (
    compress_images,
    delete_images,
    delete_to_target,
    download_images_zip,
    get_image_download_response,
    get_image_response,
    get_thumbnail_response,
    list_images,
    storage_stats,
)
from services.image_storage_service import ImageStorageError, image_storage_service
from services.image_tags_service import delete_tag, get_all_tags, set_tags
from services.dashboard_metrics_service import dashboard_metrics_service
from services.log_service import LOG_TYPE_CALL, log_service
from services.auth_service import auth_service
from services.invite_service import invite_service
from services.model_catalog_service import get_model_catalog
from services.proxy_service import proxy_settings, test_clearance, test_proxy
from services.quota_service import (
    current_usage,
    default_quota_for_new_user,
    invalidate_usage_cache,
    quota_snapshot,
)
from services.realtime_monitor_service import realtime_monitor_service
from services.runtime_log_service import list_runtime_logs
from services.session_token import issue_token
from services.user_service import user_service
from services.image_task_service import image_task_service
from services.conversation_store import conversation_store
from utils.timezone import beijing_now, parse_to_beijing_naive


class SettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")


class LoginRequest(BaseModel):
    username: str = ""
    password: str = ""


class RegisterRequest(BaseModel):
    username: str = ""
    password: str = ""
    invite_code: str = ""


class ChangePasswordRequest(BaseModel):
    old_password: str = ""
    new_password: str = ""


class ConversationsSaveRequest(BaseModel):
    conversations: list[dict[str, Any]] = Field(default_factory=list)


SETTINGS_UPDATE_KEYS = {
    "proxy",
    "fallback_proxy",
    "proxy_runtime",
    "base_url",
    "refresh_account_interval_minute",
    "image_retention_days",
    "log_retention_days",
    "image_poll_timeout_secs",
    "image_stream_timeout_secs",
    "image_poll_interval_secs",
    "image_poll_initial_wait_secs",
    "image_account_concurrency",
    "image_parallel_generation",
    "image_remove_conversation_after_result",
    "image_error_friendly_enabled",
    "image_error_messages",
    "image_settle_enabled",
    "image_check_before_hit_enabled",
    "image_settle_secs",
    "image_timeout_retry_secs",
    "auto_remove_invalid_accounts",
    "auto_remove_rate_limited_accounts",
    "log_levels",
    "global_system_prompt",
    "sensitive_words",
    "ai_review",
    "public_display",
    "image_generation",
    "quota_limits",
    "user_access",
    "model_catalog",
    "runtime_capacity",
    "image_storage",
    "backup",
    "chat_completion_cache",
    "third_party_apps",
    "providers",
}


class ProxyTestRequest(BaseModel):
    url: str = ""


class ClearanceTestRequest(BaseModel):
    target_url: str = "https://chatgpt.com"


class ProxyProfileRequest(BaseModel):
    id: str = ""
    name: str = ""
    proxy: str = ""
    no_proxy: str = ""
    enabled: bool = True
    notes: str = ""
    create_only: bool = False


class ProxyProfileTestRequest(BaseModel):
    id: str = ""
    url: str = ""


class ProxyGroupRequest(BaseModel):
    id: str = ""
    name: str = ""
    strategy: str = "request_random"
    rotation_interval_minutes: float = 0
    enabled: bool = True
    notes: str = ""
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    create_only: bool = False


class ProxyGroupTestRequest(BaseModel):
    id: str = ""
    node_id: str = ""
    url: str = ""


class ImageDeleteRequest(BaseModel):
    paths: list[str] = []
    start_date: str = ""
    end_date: str = ""
    all_matching: bool = False

class ImageDownloadRequest(BaseModel):
    paths: list[str]

class ImageTagsRequest(BaseModel):
    path: str
    tags: list[str]

class LogDeleteRequest(BaseModel):
    ids: list[str] = []
class BackupDeleteRequest(BaseModel):
    key: str = ""


class ProviderRequest(BaseModel):
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    models: list[str] = Field(default_factory=list)
    image_models: list[str] = Field(default_factory=list)
    enabled: bool = True
    timeout_secs: int = 120
    proxy: str = ""
    original_name: str = ""
    create_only: bool = False


class ProviderTestRequest(BaseModel):
    base_url: str = ""
    api_key: str = ""
    proxy: str = ""
    model: str = ""


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _coerce_proxy_group_rotation_minutes(value: object) -> float:
    try:
        minutes = float(value)
    except (OverflowError, TypeError, ValueError):
        minutes = 0.0
    return max(0.0, min(minutes, 1440.0))


DEFAULT_PROXY_NODE_IMAGE_CONCURRENCY_LIMIT = 30


def _coerce_proxy_node_image_concurrency_limit(value: object, *, default: int = DEFAULT_PROXY_NODE_IMAGE_CONCURRENCY_LIMIT) -> int:
    if value is None or value == "":
        return default
    try:
        limit = int(float(value))
    except (OverflowError, TypeError, ValueError):
        return default
    return max(0, min(limit, 10000))


_NON_MODEL_KEYS = {
    "",
    "-",
    "auto",
    "default",
    "unknown",
    "null",
    "none",
    "low",
    "medium",
    "high",
    "standard",
    "hd",
    "portrait",
    "landscape",
    "square",
    "vertical",
    "horizontal",
    "image",
    "images",
    "text",
    "chat",
    "generation",
    "generations",
    "edit",
    "edits",
}


def _looks_like_model_label(value: object) -> bool:
    label = _clean_text(value)
    key = label.lower()
    if key in _NON_MODEL_KEYS or key.startswith("/"):
        return False
    if re.fullmatch(r"\d+\s*[x×]\s*\d+", key) or re.fullmatch(r"\d+\s*:\s*\d+", key):
        return False
    return bool(label)


def _slug_id(value: object) -> str:
    raw = _clean_text(value).lower()
    chars: list[str] = []
    for char in raw:
        if char.isalnum() or char in {"-", "_"}:
            chars.append(char)
        elif char.isspace():
            chars.append("-")
    return "".join(chars).strip("-_")


def _config_dict_list(key: str) -> list[dict[str, Any]]:
    raw = config.get().get(key)
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _config_write_error_message(exc: OSError) -> str:
    return (
        "保存配置失败：后端无法写入 config.json，"
        "请检查文件权限、Docker volume 挂载或文件是否被其它程序占用。"
        f"原始错误：{exc}"
    )


def _proxy_profile_id(value: object) -> str:
    raw = _clean_text(value)
    if raw.lower().startswith("profile:"):
        raw = raw.split(":", 1)[1]
    return _slug_id(raw)


def _proxy_group_id(value: object) -> str:
    raw = _clean_text(value)
    if raw.lower().startswith("group:"):
        raw = raw.split(":", 1)[1]
    return _slug_id(raw)


def _proxy_profiles_payload() -> dict[str, Any]:
    return {"profiles": _config_dict_list("proxy_profiles")}


def _proxy_groups_payload() -> dict[str, Any]:
    return {"groups": _config_dict_list("proxy_groups")}


def _upsert_proxy_profile(body: ProxyProfileRequest) -> dict[str, Any]:
    profile_id = _proxy_profile_id(body.id or body.name)
    if not profile_id:
        raise ValueError("profile id is required")
    profiles = _config_dict_list("proxy_profiles")
    exists = any(profile.get("id") == profile_id for profile in profiles)
    if body.create_only and exists:
        raise ValueError("proxy profile already exists")
    item = {
        "id": profile_id,
        "name": body.name or profile_id,
        "proxy": body.proxy,
        "no_proxy": body.no_proxy,
        "enabled": body.enabled,
        "notes": body.notes,
    }
    next_profiles = [profile for profile in profiles if profile.get("id") != profile_id]
    next_profiles.append(item)
    updated = config.update({"proxy_profiles": next_profiles})
    profiles = [dict(profile) for profile in updated.get("proxy_profiles", []) if isinstance(profile, dict)]
    return {"profile": item, "profiles": profiles}


def _upsert_proxy_group(body: ProxyGroupRequest) -> dict[str, Any]:
    group_id = _proxy_group_id(body.id or body.name)
    if not group_id:
        raise ValueError("proxy group id is required")
    groups = _config_dict_list("proxy_groups")
    exists = any(group.get("id") == group_id for group in groups)
    if body.create_only and exists:
        raise ValueError("proxy group already exists")
    nodes: list[dict[str, Any]] = []
    for index, node in enumerate(body.nodes):
        if not isinstance(node, dict):
            continue
        node_id = _slug_id(node.get("id") or node.get("name") or f"node-{index + 1}") or f"node-{index + 1}"
        nodes.append(
            {
                **node,
                "id": node_id,
                "name": _clean_text(node.get("name")) or node_id,
                "url": _clean_text(node.get("url")),
                "enabled": bool(node.get("enabled", True)),
                "image_concurrency_limit": _coerce_proxy_node_image_concurrency_limit(
                    node.get("image_concurrency_limit")
                    if node.get("image_concurrency_limit") is not None
                    else node.get("image_concurrency")
                ),
            }
        )
    item = {
        "id": group_id,
        "name": body.name or group_id,
        "strategy": body.strategy or "request_random",
        "rotation_interval_minutes": _coerce_proxy_group_rotation_minutes(body.rotation_interval_minutes),
        "enabled": body.enabled,
        "notes": body.notes,
        "nodes": nodes,
    }
    next_groups = [group for group in groups if group.get("id") != group_id]
    next_groups.append(item)
    updated = config.update({"proxy_groups": next_groups})
    groups = [dict(group) for group in updated.get("proxy_groups", []) if isinstance(group, dict)]
    return {"group": item, "groups": groups}


def _resolve_profile_proxy(profile_id: str) -> str:
    normalized = _proxy_profile_id(profile_id)
    for profile in _config_dict_list("proxy_profiles"):
        if profile.get("id") == normalized and profile.get("enabled", True):
            return _clean_text(profile.get("proxy"))
    return ""


def _providers_list() -> list[dict[str, Any]]:
    return config.get_providers_settings()


def _test_provider_connection(base_url: str, api_key: str, proxy: str) -> dict[str, Any]:
    from curl_cffi import requests

    url = f"{base_url.rstrip('/')}s"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    started = time.monotonic()
    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=20,
            **proxy_settings.build_session_kwargs(proxy=proxy, upstream=True),
        )
    except Exception as exc:
        return {"ok": False, "status": 0, "error": str(exc)}
    latency_ms = int((time.monotonic() - started) * 1000)
    ok = 200 <= response.status_code < 300
    count = 0
    if ok:
        try:
            data = response.json().get("data")
            count = len(data) if isinstance(data, list) else 0
        except Exception:
            count = 0
    return {
        "ok": ok,
        "status": response.status_code,
        "latency_ms": latency_ms,
        "model_count": count,
        "error": None if ok else f"HTTP {response.status_code}",
    }


def _fetch_provider_models(base_url: str, api_key: str, proxy: str) -> dict[str, Any]:
    from curl_cffi import requests

    url = f"{base_url.rstrip('/')}s"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=20,
            **proxy_settings.build_session_kwargs(proxy=proxy, upstream=True),
        )
    except Exception as exc:
        return {"ok": False, "status": 0, "models": [], "error": str(exc)}
    if not 200 <= response.status_code < 300:
        return {"ok": False, "status": response.status_code, "models": [], "error": f"HTTP {response.status_code}"}
    try:
        data = response.json().get("data")
    except Exception as exc:
        return {"ok": False, "status": response.status_code, "models": [], "error": f"解析响应失败：{exc}"}
    models: list[str] = []
    seen: set[str] = set()
    if isinstance(data, list):
        for item in data:
            model_id = _clean_text(item.get("id")) if isinstance(item, dict) else _clean_text(item)
            if model_id and model_id not in seen:
                seen.add(model_id)
                models.append(model_id)
    return {"ok": True, "status": response.status_code, "models": models, "error": None}


def _provider_entry_from_request(body: ProviderRequest) -> dict[str, Any]:
    return {
        "name": _clean_text(body.name),
        "base_url": _clean_text(body.base_url).rstrip("/"),
        "api_key": _clean_text(body.api_key),
        "models": [_clean_text(item) for item in body.models if _clean_text(item)],
        "image_models": [_clean_text(item) for item in body.image_models if _clean_text(item)],
        "enabled": bool(body.enabled),
        "timeout_secs": max(1, int(body.timeout_secs or 120)),
        "proxy": _clean_text(body.proxy),
    }


def _upsert_provider(body: ProviderRequest) -> dict[str, Any]:
    entry = _provider_entry_from_request(body)
    if not entry["name"]:
        raise ValueError("provider name is required")
    if not entry["base_url"]:
        raise ValueError("provider base_url is required")
    previous_name = _clean_text(body.original_name) or entry["name"]
    providers = _config_dict_list("providers")
    name_key = entry["name"].lower()
    exists = any(_clean_text(item.get("name")).lower() == name_key for item in providers)
    if body.create_only and exists:
        raise ValueError("provider already exists")
    remove_keys = {name_key, previous_name.lower()}
    next_providers = [
        item for item in providers
        if _clean_text(item.get("name")).lower() not in remove_keys
    ]
    next_providers.append(entry)
    updated = config.update({"providers": next_providers})
    return {"provider": entry, "providers": updated.get("providers", [])}


def _increment(counter: dict[str, int], key: object, default: str = "unknown") -> None:
    label = _clean_text(key) or default
    counter[label] = counter.get(label, 0) + 1


def _detail_value(item: dict[str, Any], key: str, default: object = "") -> object:
    detail = item.get("detail")
    if isinstance(detail, dict):
        value = detail.get(key)
        if value not in (None, ""):
            return value
    value = item.get(key)
    return default if value in (None, "") else value


def _parse_log_time(value: object) -> datetime | None:
    return parse_to_beijing_naive(value)


def _beijing_now_naive() -> datetime:
    return beijing_now().replace(tzinfo=None)


def _dashboard_log_summary(items: list[dict[str, Any]], *, time_range: str) -> dict[str, Any]:
    total = len(items)
    success = 0
    failed = 0
    by_endpoint: dict[str, int] = {}
    by_model: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_error_code: dict[str, int] = {}
    recent_failures: list[dict[str, Any]] = []

    bucket_count = {"24h": 24, "7d": 7, "30d": 30}.get(time_range, 24)
    bucket_delta = timedelta(hours=1) if time_range == "24h" else timedelta(days=1)
    bucket_format = "%H:00" if time_range == "24h" else "%m-%d"
    raw_now = _beijing_now_naive()
    current_bucket_start = (
        raw_now.replace(minute=0, second=0, microsecond=0)
        if time_range == "24h"
        else raw_now.replace(hour=0, minute=0, second=0, microsecond=0)
    )
    starts = [current_bucket_start - bucket_delta * (bucket_count - 1 - index) for index in range(bucket_count)]
    labels = [start.strftime(bucket_format) for start in starts]
    total_requests = [0] * bucket_count
    success_requests = [0] * bucket_count
    failed_requests = [0] * bucket_count
    rate_limited_requests = [0] * bucket_count
    model_requests: dict[str, list[int]] = {}
    model_total_times: dict[str, list[float]] = {}
    model_time_counts: dict[str, list[int]] = {}

    def bucket_index(dt: datetime | None) -> int | None:
        if dt is None:
            return None
        if time_range == "24h":
            bucket_start = dt.replace(minute=0, second=0, microsecond=0)
            idx = int((bucket_start - starts[0]).total_seconds() // 3600)
        else:
            idx = (dt.date() - starts[0].date()).days
        return idx if 0 <= idx < bucket_count else None

    for item in items:
        status = _clean_text(_detail_value(item, "status", item.get("status"))).lower()
        endpoint = _clean_text(_detail_value(item, "endpoint"))
        model = _clean_text(_detail_value(item, "model"))
        error_code = _clean_text(_detail_value(item, "error_code"))
        dt = _parse_log_time(_detail_value(item, "started_at", item.get("time")))
        idx = bucket_index(dt)

        is_failed = status in {"failed", "error", "fail"} or bool(_detail_value(item, "error"))
        if is_failed:
            failed += 1
            if len(recent_failures) < 10:
                recent_failures.append(
                    {
                        "id": item.get("id"),
                        "time": item.get("time") or _detail_value(item, "started_at"),
                        "summary": item.get("summary"),
                        "endpoint": endpoint,
                        "error_code": error_code,
                        "stage": _detail_value(item, "stage"),
                        "reason": _detail_value(item, "reason", _detail_value(item, "error")),
                        "conversation_id": _detail_value(item, "conversation_id"),
                    }
                )
        else:
            success += 1

        if status:
            _increment(by_status, status)
        if endpoint.startswith("/"):
            _increment(by_endpoint, endpoint)
        if _looks_like_model_label(model):
            _increment(by_model, model)
        if error_code:
            _increment(by_error_code, error_code)

        if idx is not None:
            total_requests[idx] += 1
            if is_failed:
                if error_code in {"rate_limited", "rate_limit", "429"}:
                    rate_limited_requests[idx] += 1
                else:
                    failed_requests[idx] += 1
            else:
                success_requests[idx] += 1
            if _looks_like_model_label(model):
                model_requests.setdefault(model, [0] * bucket_count)[idx] += 1
                duration_raw = _detail_value(item, "duration_ms", None)
                try:
                    duration_ms = max(0.0, float(duration_raw))
                except (TypeError, ValueError):
                    duration_ms = None
                if duration_ms is not None:
                    model_total_times.setdefault(model, [0.0] * bucket_count)[idx] += duration_ms
                    model_time_counts.setdefault(model, [0] * bucket_count)[idx] += 1

    model_avg_times = {
        model: [
            round(total / counts[index], 2) if counts[index] > 0 else 0.0
            for index, total in enumerate(totals)
        ]
        for model, totals in model_total_times.items()
        for counts in [model_time_counts.get(model, [0] * bucket_count)]
    }

    return {
        "total": total,
        "success": success,
        "failed": failed,
        "by_endpoint": by_endpoint,
        "by_model": by_model,
        "by_status": by_status,
        "by_error_code": by_error_code,
        "recent_failures": recent_failures,
        "trend": {
            "labels": labels,
            "total_requests": total_requests,
            "success_requests": success_requests,
            "failed_requests": failed_requests,
            "rate_limited_requests": rate_limited_requests,
            "model_requests": model_requests,
            "model_ttfb_times": {},
            "model_total_times": model_avg_times,
        },
    }


def create_router(app_version: str) -> APIRouter:
    router = APIRouter()

    @router.post("/auth/login")
    async def login(body: LoginRequest | None = None, authorization: str | None = Header(default=None)):
        # 用户名+密码登录：签发会话令牌
        username = (body.username if body else "").strip()
        password = (body.password if body else "")
        if username and password:
            user = user_service.authenticate_password(username, password)
            if user is None:
                raise HTTPException(status_code=401, detail={"error": "用户名或密码不正确，或账号已被禁用"})
            token = issue_token(user)
            return {
                "ok": True,
                "authenticated": True,
                "version": app_version,
                "token": token,
                "role": user.get("role"),
                "subject_id": user.get("id"),
                "name": user.get("username"),
            }
        # 兼容：直接使用管理员密钥 / API key 登录（Authorization 头）
        identity = require_identity(authorization)
        return {
            "ok": True,
            "authenticated": True,
            "version": app_version,
            "role": identity.get("role"),
            "subject_id": identity.get("id"),
            "name": identity.get("name"),
        }

    @router.post("/auth/register")
    async def register(body: RegisterRequest):
        quota_settings = config.get_user_access_settings()
        if not bool(quota_settings.get("open_registration", True)):
            raise HTTPException(status_code=403, detail={"error": "当前未开放自助注册，请联系管理员开通账号"})
        require_invite = bool(quota_settings.get("require_invite", False))
        invite_input = body.invite_code.strip()
        if require_invite:
            if not invite_input:
                raise HTTPException(status_code=400, detail={"error": "注册需要邀请码"})
            # 先校验（不消费），避免用户名重复/密码不合法时白白浪费邀请码
            if not invite_service.validate(invite_input):
                raise HTTPException(status_code=403, detail={"error": "邀请码无效、已停用或已用尽"})
        try:
            user = user_service.create_user(
                body.username, body.password, role="user", quota=default_quota_for_new_user()
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        # 用户创建成功后再消费邀请码
        if require_invite:
            invite_service.consume(invite_input)
        # 为新用户创建一把默认 API key，归属到该用户
        api_key = ""
        try:
            _, api_key = auth_service.create_key(role="user", name=user["username"], owner_user_id=user["id"])
        except ValueError:
            api_key = ""
        full_user = user_service.get_user(user["id"]) or user
        token = issue_token(full_user)
        return {
            "ok": True,
            "token": token,
            "role": user.get("role"),
            "subject_id": user.get("id"),
            "name": user.get("username"),
            "api_key": api_key,
        }

    @router.get("/auth/register-info")
    async def register_info():
        """公开：登录/注册页据此判断是否开放注册、是否需要邀请码。"""
        quota_settings = config.get_user_access_settings()
        return {
            "open_registration": bool(quota_settings.get("open_registration", True)),
            "require_invite": bool(quota_settings.get("require_invite", False)),
        }

    @router.get("/auth/status")
    async def auth_status(authorization: str | None = Header(default=None)):
        try:
            identity = require_identity(authorization)
        except HTTPException:
            return {
                "ok": False,
                "authenticated": False,
                "version": app_version,
            }
        return {
            "ok": True,
            "authenticated": True,
            "version": app_version,
            "role": identity.get("role"),
            "subject_id": identity.get("id"),
            "name": identity.get("name"),
        }

    @router.get("/version")
    async def get_version():
        return {
            "version": app_version,
            "changelog_url": "https://github.com/hearthealt/chatgpt2api/blob/main/CHANGELOG.md"
        }

    def _resolve_me(authorization: str | None) -> dict[str, Any]:
        identity = require_identity(authorization)
        user = user_service.get_user(str(identity.get("id") or ""))
        if user is None:
            raise HTTPException(status_code=404, detail={"error": "当前登录身份不是用户账户"})
        return user

    @router.get("/api/me")
    async def get_me(authorization: str | None = Header(default=None)):
        user = await run_in_threadpool(_resolve_me, authorization)
        keys = auth_service.list_owner_keys(str(user.get("id") or ""))
        return {
            "user": user_service.public_user(user),
            "quota": await run_in_threadpool(quota_snapshot, user),
            "api_keys": keys,
        }

    @router.get("/api/me/usage")
    async def get_my_usage(
        limit: int = Query(default=50, ge=1, le=500),
        authorization: str | None = Header(default=None),
    ):
        user = await run_in_threadpool(_resolve_me, authorization)
        snapshot = await run_in_threadpool(quota_snapshot, user)
        from services.quota_service import owner_key_ids

        key_ids = await run_in_threadpool(owner_key_ids, user)
        raw_logs = await run_in_threadpool(log_service.list, LOG_TYPE_CALL, "", "", 2000)
        history: list[dict[str, Any]] = []
        for item in raw_logs:
            detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
            key_id = _clean_text(detail.get("key_id") or item.get("key_id"))
            if key_id not in key_ids:
                continue
            status = _clean_text(_detail_value(item, "status", item.get("status"))).lower()
            failed = status in {"failed", "error", "fail"} or bool(_detail_value(item, "error"))
            history.append({
                "id": item.get("id"),
                "time": item.get("time") or _detail_value(item, "started_at"),
                "endpoint": _detail_value(item, "endpoint"),
                "model": _detail_value(item, "model"),
                "status": "error" if failed else "success",
                "summary": item.get("summary"),
            })
            if len(history) >= limit:
                break
        return {"quota": snapshot, "history": history}

    @router.get("/api/me/gallery")
    async def get_my_gallery(
        request: Request,
        start_date: str = "",
        end_date: str = "",
        limit: int = Query(default=0, ge=0, le=500),
        offset: int = Query(default=0, ge=0),
        media_type: str = Query(default="all"),
        search: str = Query(default=""),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        owned_paths = await run_in_threadpool(image_task_service.list_owner_image_paths, identity)
        if not owned_paths:
            return {
                "items": [], "groups": [], "total": 0, "total_size": 0,
                "counts": {"all": 0, "image": 0, "video": 0, "music": 0},
                "media_type": media_type, "page": 1, "page_size": limit or 0, "page_count": 0,
            }
        return await run_in_threadpool(
            list_images,
            resolve_image_base_url(request),
            start_date=start_date.strip(),
            end_date=end_date.strip(),
            limit=limit,
            offset=offset,
            media_type=media_type,
            search=search.strip(),
            only_paths=owned_paths,
        )

    @router.get("/api/me/conversations")
    async def get_my_conversations(authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        items = await run_in_threadpool(conversation_store.get, identity)
        return {"conversations": items}

    @router.put("/api/me/conversations")
    async def save_my_conversations(body: ConversationsSaveRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        items = await run_in_threadpool(conversation_store.replace, identity, body.conversations)
        return {"ok": True, "count": len(items)}

    @router.post("/api/me/password")
    async def change_my_password(body: ChangePasswordRequest, authorization: str | None = Header(default=None)):
        user = await run_in_threadpool(_resolve_me, authorization)
        try:
            updated = await run_in_threadpool(
                user_service.verify_and_change_password,
                user["id"],
                body.old_password,
                body.new_password,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        if updated is None:
            raise HTTPException(status_code=404, detail={"error": "用户不存在"})
        invalidate_usage_cache(user["id"])
        # 返回新令牌，让当前会话不掉线
        return {"ok": True, "token": issue_token(updated)}

    @router.post("/api/me/api-key")
    async def regenerate_my_api_key(authorization: str | None = Header(default=None)):
        """重新生成当前用户的调用密钥：删除旧的、创建一把新的，一次性返回原始密钥。"""
        user = await run_in_threadpool(_resolve_me, authorization)
        await run_in_threadpool(auth_service.delete_owner_keys, user["id"])
        try:
            _, raw_key = await run_in_threadpool(
                auth_service.create_key, role="user", name=str(user.get("username") or ""), owner_user_id=user["id"]
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        keys = auth_service.list_owner_keys(str(user.get("id") or ""))
        return {"ok": True, "key": raw_key, "api_keys": keys}

    @router.get("/public/stats")
    async def public_stats():
        from services.account_service import account_service as acct_svc

        stats = acct_svc.get_stats()
        logs = log_service.list(type=LOG_TYPE_CALL, limit=500)
        recent_cutoff = _beijing_now_naive() - timedelta(minutes=1)
        recent = [
            item for item in logs
            if (_parse_log_time(_detail_value(item, "started_at", item.get("time"))) or datetime.min) >= recent_cutoff
        ]
        rpm = len(recent)
        load_status = "high" if rpm >= 60 else "medium" if rpm >= 20 else "low"
        load_color = {"high": "#ef4444", "medium": "#f59e0b", "low": "#22c55e"}[load_status]
        return {
            "total_visitors": int(stats.get("total") or 0),
            "total_requests": len(logs),
            "requests_per_minute": rpm,
            "load_status": load_status,
            "load_color": load_color,
        }

    @router.get("/public/display")
    async def public_display():
        public = config.get().get("public_display")
        data = public if isinstance(public, dict) else {}
        return {
            "logo_url": str(data.get("logo_url") or ""),
            "chat_url": str(data.get("chat_url") or ""),
        }

    @router.get("/public/log")
    async def public_log(limit: int = Query(default=20, ge=1, le=100)):
        items = log_service.list(type=LOG_TYPE_CALL, limit=limit)
        groups = []
        for item in items:
            status = _clean_text(_detail_value(item, "status", item.get("status"))).lower()
            failed = status in {"failed", "error", "fail"} or bool(_detail_value(item, "error"))
            groups.append(
                {
                    "request_id": str(item.get("id") or ""),
                    "start_time": str(item.get("time") or _detail_value(item, "started_at")),
                    "status": "error" if failed else "success",
                    "events": [
                        {
                            "time": str(item.get("time") or _detail_value(item, "started_at")),
                            "type": "complete",
                            "status": "error" if failed else "success",
                            "content": str(item.get("summary") or _detail_value(item, "endpoint") or "调用日志"),
                        }
                    ],
                }
            )
        return {"total": len(groups), "logs": groups}

    @router.get("/public/uptime")
    async def public_uptime(days: int = Query(default=90, ge=1, le=365)):
        storage = config.get_storage_backend()
        try:
            storage_health = storage.health_check()
            storage_ok = bool(storage_health.get("ok", True)) if isinstance(storage_health, dict) else True
        except Exception:
            storage_health = {}
            storage_ok = False
        now = beijing_now().isoformat(timespec="seconds")
        return {
            "updated_at": now,
            "services": {
                "api": {
                    "name": "API",
                    "status": "up",
                    "uptime": 100,
                    "total": 1,
                    "success": 1,
                    "heartbeats": [{"time": now, "success": True, "latency_ms": 0, "level": "up"}],
                },
                "storage": {
                    "name": "Storage",
                    "status": "up" if storage_ok else "warn",
                    "uptime": 100 if storage_ok else 0,
                    "total": 1,
                    "success": 1 if storage_ok else 0,
                    "heartbeats": [{"time": now, "success": storage_ok, "latency_ms": 0, "level": "up" if storage_ok else "warn"}],
                },
            },
        }

    @router.get("/api/settings")
    async def get_settings(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"config": config.get()}

    @router.get("/api/third-party-apps")
    async def get_third_party_apps(authorization: str | None = Header(default=None)):
        require_identity(authorization)
        return {"third_party_apps": config.get_third_party_apps_settings()}

    @router.post("/api/settings")
    async def save_settings(body: SettingsUpdateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            incoming = body.model_dump(mode="python")
            updates = {key: value for key, value in incoming.items() if key in SETTINGS_UPDATE_KEYS}
            if not updates:
                return {"config": config.get()}
            return {"config": config.update(updates)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail={"error": _config_write_error_message(exc)}) from exc

    @router.get("/api/model-catalog")
    async def model_catalog(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return get_model_catalog()

    @router.get("/api/admin/model-catalog")
    async def get_model_catalog_settings(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return config.get_model_catalog_settings()

    @router.post("/api/admin/model-catalog")
    async def update_model_catalog_settings(body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        # drop None fields, merge with current, update
        updates = {k: v for k, v in body.items() if v is not None}
        current = dict(config.get_model_catalog_settings())
        current.update(updates)
        config.update({"model_catalog": current})
        return config.get_model_catalog_settings()

    @router.get("/api/providers")
    async def list_providers(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"providers": _providers_list()}

    @router.post("/api/providers")
    async def save_provider(body: ProviderRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return _upsert_provider(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail={"error": _config_write_error_message(exc)}) from exc

    @router.delete("/api/providers/{name}")
    async def delete_provider(name: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        normalized = _clean_text(name).lower()
        providers = _config_dict_list("providers")
        next_providers = [item for item in providers if _clean_text(item.get("name")).lower() != normalized]
        if len(next_providers) == len(providers):
            raise HTTPException(status_code=404, detail={"error": "provider not found"})
        try:
            updated = config.update({"providers": next_providers})
        except OSError as exc:
            raise HTTPException(status_code=500, detail={"error": _config_write_error_message(exc)}) from exc
        return {"deleted": _clean_text(name), "providers": updated.get("providers", [])}

    @router.post("/api/providers/test")
    async def test_provider(body: ProviderTestRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        base_url = _clean_text(body.base_url).rstrip("/")
        if not base_url:
            raise HTTPException(status_code=400, detail={"error": "base_url is required"})
        return {"result": await run_in_threadpool(_test_provider_connection, base_url, _clean_text(body.api_key), _clean_text(body.proxy))}

    @router.post("/api/providers/models")
    async def fetch_provider_models(body: ProviderTestRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        base_url = _clean_text(body.base_url).rstrip("/")
        if not base_url:
            raise HTTPException(status_code=400, detail={"error": "base_url is required"})
        return await run_in_threadpool(_fetch_provider_models, base_url, _clean_text(body.api_key), _clean_text(body.proxy))

    @router.get("/api/images")
    async def get_images(
        request: Request,
        start_date: str = "",
        end_date: str = "",
        limit: int = Query(default=0, ge=0, le=500),
        offset: int = Query(default=0, ge=0),
        media_type: str = Query(default="all"),
        tag: str = Query(default=""),
        search: str = Query(default=""),
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        return await run_in_threadpool(
            list_images,
            resolve_image_base_url(request),
            start_date=start_date.strip(),
            end_date=end_date.strip(),
            limit=limit,
            offset=offset,
            media_type=media_type,
            tag=tag.strip(),
            search=search.strip(),
            with_owner=True,
        )

    @router.get("/images/{image_path:path}", include_in_schema=False)
    async def get_image(image_path: str):
        return get_image_response(image_path)

    @router.get("/image-thumbnails/{image_path:path}", include_in_schema=False)
    async def get_image_thumbnail(image_path: str):
        return get_thumbnail_response(image_path)

    @router.post("/api/images/delete")
    async def delete_images_endpoint(body: ImageDeleteRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return delete_images(body.paths, start_date=body.start_date.strip(), end_date=body.end_date.strip(), all_matching=body.all_matching)

    @router.post("/api/images/download")
    async def download_images_endpoint(body: ImageDownloadRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        buf = download_images_zip(body.paths)
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="images.zip"'},
        )

    @router.get("/api/images/download/{image_path:path}")
    async def download_single_image_endpoint(image_path: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return get_image_download_response(image_path)

    @router.get("/api/logs")
    async def get_logs(
        type: str = "",
        start_date: str = "",
        end_date: str = "",
        status: str = "",
        endpoint: str = "",
        model: str = "",
        account: str = "",
        conversation_id: str = "",
        search: str = "",
        limit: int = Query(default=200, ge=1, le=20000),
        offset: int = Query(default=0, ge=0),
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        return await run_in_threadpool(
            log_service.list_page,
            type=type.strip(),
            start_date=start_date.strip(),
            end_date=end_date.strip(),
            status=status.strip(),
            endpoint=endpoint.strip(),
            model=model.strip(),
            account=account.strip(),
            conversation_id=conversation_id.strip(),
            search=search.strip(),
            limit=limit,
            offset=offset,
        )

    @router.post("/api/logs/delete")
    async def delete_logs(body: LogDeleteRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return log_service.delete(body.ids)

    @router.get("/api/runtime-logs")
    async def get_runtime_logs(
        level: str = "",
        search: str = "",
        source: str = "",
        limit: int = Query(default=300, ge=1, le=2000),
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        return list_runtime_logs(
            level=level.strip(),
            search=search.strip(),
            source=source.strip(),
            limit=limit,
        )

    @router.get("/api/monitor/realtime")
    async def get_realtime_monitor(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return realtime_monitor_service.snapshot()

    @router.get("/api/monitor/realtime/{call_id}")
    async def get_realtime_monitor_detail(call_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        detail = realtime_monitor_service.detail(call_id)
        if not detail:
            raise HTTPException(status_code=404, detail={"error": "request not found"})
        return {"detail": detail}

    @router.post("/api/monitor/realtime/{call_id}/cancel")
    async def cancel_realtime_monitor_request(call_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        result = realtime_monitor_service.cancel(call_id)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail={"error": result.get("error") or "request not found"})
        return result

    @router.post("/api/proxy/test")
    async def test_proxy_endpoint(body: ProxyTestRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        candidate = (body.url or "").strip() or config.get_proxy_settings()
        if not candidate:
            raise HTTPException(status_code=400, detail={"error": "proxy url is required"})
        return {"result": await run_in_threadpool(test_proxy, candidate)}

    @router.get("/api/proxy/profiles")
    async def list_proxy_profiles(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return _proxy_profiles_payload()

    @router.post("/api/proxy/profiles")
    async def save_proxy_profile(body: ProxyProfileRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return _upsert_proxy_profile(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail={"error": _config_write_error_message(exc)}) from exc

    @router.delete("/api/proxy/profiles/{profile_id}")
    async def delete_proxy_profile(profile_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        normalized = _proxy_profile_id(profile_id)
        profiles = _config_dict_list("proxy_profiles")
        next_profiles = [profile for profile in profiles if profile.get("id") != normalized]
        if len(next_profiles) == len(profiles):
            raise HTTPException(status_code=404, detail={"error": "proxy profile not found"})
        try:
            updated = config.update({"proxy_profiles": next_profiles})
        except OSError as exc:
            raise HTTPException(status_code=500, detail={"error": _config_write_error_message(exc)}) from exc
        return {"deleted": normalized, "profiles": updated.get("proxy_profiles", [])}

    @router.post("/api/proxy/profiles/test")
    async def test_proxy_profile_endpoint(body: ProxyProfileTestRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        candidate = (body.url or "").strip()
        if not candidate and body.id.strip():
            candidate = _resolve_profile_proxy(body.id)
        if not candidate:
            raise HTTPException(status_code=400, detail={"error": "proxy url or profile id is required"})
        return {"result": await run_in_threadpool(test_proxy, candidate)}

    @router.get("/api/proxy/groups")
    async def list_proxy_groups(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return _proxy_groups_payload()

    @router.post("/api/proxy/groups")
    async def save_proxy_group(body: ProxyGroupRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return _upsert_proxy_group(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail={"error": _config_write_error_message(exc)}) from exc

    @router.delete("/api/proxy/groups/{group_id}")
    async def delete_proxy_group(group_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        normalized = _proxy_group_id(group_id)
        groups = _config_dict_list("proxy_groups")
        next_groups = [group for group in groups if group.get("id") != normalized]
        if len(next_groups) == len(groups):
            raise HTTPException(status_code=404, detail={"error": "proxy group not found"})
        try:
            updated = config.update({"proxy_groups": next_groups})
        except OSError as exc:
            raise HTTPException(status_code=500, detail={"error": _config_write_error_message(exc)}) from exc
        return {"deleted": normalized, "groups": updated.get("proxy_groups", [])}

    @router.post("/api/proxy/groups/test")
    async def test_proxy_group_endpoint(body: ProxyGroupTestRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        explicit_url = (body.url or "").strip()
        if explicit_url:
            return {"result": await run_in_threadpool(test_proxy, explicit_url)}

        group_id = _proxy_group_id(body.id)
        if not group_id:
            raise HTTPException(status_code=400, detail={"error": "proxy group id or url is required"})
        group = next((item for item in _config_dict_list("proxy_groups") if item.get("id") == group_id), None)
        if group is None:
            raise HTTPException(status_code=404, detail={"error": "proxy group not found"})
        node_id = _slug_id(body.node_id)
        nodes = [
            node for node in group.get("nodes", [])
            if isinstance(node, dict)
            and node.get("enabled", True)
            and _clean_text(node.get("url"))
            and (not node_id or node.get("id") == node_id)
        ]
        if not nodes:
            raise HTTPException(status_code=400, detail={"error": "proxy group node url is required"})
        results = [
            {"node_id": str(node.get("id") or ""), "result": await run_in_threadpool(test_proxy, _clean_text(node.get("url")))}
            for node in nodes
        ]
        return {
            "results": results,
            "result": results[0]["result"] if len(results) == 1 else None,
            "groups": _config_dict_list("proxy_groups"),
        }

    @router.get("/api/proxy/runtime")
    async def get_proxy_runtime_endpoint(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {
            "runtime": config.get_public_proxy_runtime_settings(),
            "status": proxy_settings.get_runtime_status(),
        }

    @router.post("/api/proxy/runtime")
    async def save_proxy_runtime_endpoint(body: SettingsUpdateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            config.update({"proxy_runtime": body.model_dump(mode="python")})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail={"error": _config_write_error_message(exc)}) from exc
        return {
            "runtime": config.get_public_proxy_runtime_settings(),
            "status": proxy_settings.get_runtime_status(),
        }

    @router.post("/api/proxy/clearance/test")
    async def test_proxy_clearance_endpoint(body: ClearanceTestRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"result": await run_in_threadpool(test_clearance, body.target_url)}

    @router.get("/api/storage/info")
    async def get_storage_info(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        storage = config.get_storage_backend()
        return {
            "backend": storage.get_backend_info(),
            "health": storage.health_check(),
        }

    @router.get("/api/dashboard")
    async def get_dashboard(
        authorization: str | None = Header(default=None),
        log_limit: int = Query(default=5000, ge=1, le=20000),
        time_range: str = Query(default="24h", pattern="^(24h|7d|30d)$"),
    ):
        require_admin(authorization)
        from services.account_service import account_service as acct_svc

        account_stats = acct_svc.get_stats()
        account_healthy = bool(account_stats.get("active")) or bool(account_stats.get("unlimited_quota_count"))
        storage = config.get_storage_backend()
        call_logs = log_service.list(type=LOG_TYPE_CALL, limit=log_limit)
        recent_log_summary = _dashboard_log_summary(call_logs, time_range=time_range)
        await run_in_threadpool(dashboard_metrics_service.backfill_if_empty, call_logs)
        dashboard_logs = await run_in_threadpool(dashboard_metrics_service.summary, time_range)
        dashboard_logs["recent_failures"] = recent_log_summary.get("recent_failures", [])
        image_storage_stats = await run_in_threadpool(storage_stats)
        storage_health = await run_in_threadpool(storage.health_check)
        return {
            "status": "ok" if account_healthy else "degraded",
            "healthy": account_healthy,
            "version": app_version,
            "accounts": {
                **account_stats,
                "healthy": account_healthy,
            },
            "storage": {
                "backend": storage.get_backend_info(),
                "health": storage_health,
                "images": image_storage_stats,
            },
            "logs": dashboard_logs,
        }

    @router.post("/api/backup/test")
    async def test_backup_connection(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"result": await run_in_threadpool(backup_service.test_connection)}
        except BackupError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/image-storage/test")
    async def test_image_storage_endpoint(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"result": await run_in_threadpool(image_storage_service.test_webdav)}

    @router.post("/api/image-storage/sync")
    async def sync_image_storage_endpoint(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"result": await run_in_threadpool(image_storage_service.sync_all)}
        except ImageStorageError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/backups")
    async def get_backups(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {
                "items": await run_in_threadpool(backup_service.list_backups),
                "state": backup_service.get_status(),
                "settings": backup_service.get_settings(),
            }
        except BackupError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/backups/run")
    async def run_backup_endpoint(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"result": await run_in_threadpool(backup_service.run_backup)}
        except BackupError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/backups/delete")
    async def delete_backup_endpoint(body: BackupDeleteRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            await run_in_threadpool(backup_service.delete_backup, body.key)
            return {"ok": True}
        except BackupError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/backups/detail")
    async def get_backup_detail(key: str = "", authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"item": await run_in_threadpool(backup_service.get_backup_detail, key)}
        except BackupError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/backups/download")
    async def download_backup_endpoint(key: str = "", authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            item = await run_in_threadpool(backup_service.download_backup, key)
        except BackupError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        filename = str(item.get("name") or "backup.bin")
        quoted = quote(filename)
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}",
            "Content-Length": str(int(item.get("size") or 0)),
        }
        return Response(
            content=bytes(item.get("payload") or b""),
            media_type=str(item.get("content_type") or "application/octet-stream"),
            headers=headers,
        )


    @router.get("/api/images/tags")
    async def list_image_tags(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"tags": get_all_tags()}

    @router.post("/api/images/tags")
    async def update_image_tags(body: ImageTagsRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        rel = body.path.strip().lstrip("/")
        if not rel:
            raise HTTPException(status_code=400, detail={"error": "path is required"})
        tags = set_tags(rel, body.tags)
        return {"ok": True, "tags": tags}

    @router.delete("/api/images/tags/{tag}")
    async def delete_image_tag(tag: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        count = delete_tag(tag)
        return {"ok": True, "removed_from": count}

    @router.get("/api/images/storage")
    async def get_image_storage(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return storage_stats()

    @router.post("/api/images/storage/compress")
    async def compress_all_images(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return await run_in_threadpool(compress_images)

    @router.post("/api/images/storage/cleanup-to-target")
    async def cleanup_to_target(
        target_free_mb: int = 500,
        dry_run: bool = False,
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        return await run_in_threadpool(delete_to_target, target_free_mb, dry_run)

    @router.get("/health", response_model=None)
    async def health_dashboard(format: str = Query(default="html")):
        from services.account_service import account_service as acct_svc
        stats = acct_svc.get_stats()
        storage = config.get_storage_backend()
        storage_health = storage.health_check()
        healthy = stats["active"] > 0 or stats["unlimited_quota_count"] > 0

        stats_json = {
            "status": "ok" if healthy else "degraded",
            "healthy": healthy,
            "version": app_version,
            "storage": {"backend": storage.get_backend_info(), "health": storage_health},
            "proxy_runtime": proxy_settings.get_runtime_status(),
            "accounts": stats,
        }
        if format == "json":
            return stats_json
        return HTMLResponse(f"""<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>号池健康监控 - chatgpt2api</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:system-ui,-apple-system,sans-serif;background:#0f1117;color:#e2e8f0;min-height:100vh}}
.header{{background:#1a1d27;border-bottom:1px solid #2a2d3a;padding:16px 24px;display:flex;justify-content:space-between;align-items:center}}
.header h1{{font-size:20px}}
.status-dot{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px}}
.status-ok{{background:#22c55e;box-shadow:0 0 8px #22c55e88}}
.status-degraded{{background:#f59e0b;box-shadow:0 0 8px #f59e0b88}}
.container{{max-width:960px;margin:0 auto;padding:24px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:24px}}
.card{{background:#1a1d27;border:1px solid #2a2d3a;border-radius:10px;padding:16px}}
.card .value{{font-size:28px;font-weight:700;margin:4px 0}}
.card .label{{font-size:13px;color:#94a3b8}}
.green{{color:#22c55e}}.yellow{{color:#f59e0b}}.red{{color:#ef4444}}.blue{{color:#6c63ff}}
table{{width:100%;border-collapse:collapse;background:#1a1d27;border:1px solid #2a2d3a;border-radius:10px;overflow:hidden}}
th{{background:#242836;font-weight:600;text-align:left;padding:10px 12px;font-size:12px;color:#94a3b8;text-transform:uppercase}}
td{{padding:8px 12px;border-top:1px solid #2a2d3a;font-size:14px}}tr:hover td{{background:rgba(108,99,255,.05)}}
.api-url{{font-family:monospace;font-size:12px;color:#6c63ff}}
.refresh{{font-size:12px;color:#64748b;text-align:center;margin-top:24px}}
</style>
<meta http-equiv="refresh" content="30">
</head>
<body>
<div class="header">
<h1><span class="status-dot {'status-ok' if healthy else 'status-degraded'}"></span>号池健康监控</h1>
<div style="font-size:13px;color:#94a3b8">v{app_version} · 30s 自动刷新</div>
</div>
<div class="container">
<div class="cards">
<div class="card"><div class="label">号池状态</div><div class="value {'green' if healthy else 'yellow'}">{'正常' if healthy else '异常'}</div></div>
<div class="card"><div class="label">当前账号</div><div class="value blue">{stats['total']}</div></div>
<div class="card"><div class="label">累计入库</div><div class="value">{stats['cumulative_total']}</div></div>
<div class="card"><div class="label">可用账号</div><div class="value green">{stats['active']}</div></div>
<div class="card"><div class="label">无限额</div><div class="value">{stats['unlimited_quota_count']}</div></div>
<div class="card"><div class="label">剩余额度</div><div class="value">{stats['total_quota']}</div></div>
<div class="card"><div class="label">限流</div><div class="value yellow">{stats['limited']}</div></div>
<div class="card"><div class="label">异常</div><div class="value red">{stats['abnormal']}</div></div>
<div class="card"><div class="label">禁用</div><div class="value">{stats['disabled']}</div></div>
<div class="card"><div class="label">成功/失败</div><div class="value">{stats['total_success']}<span style="font-size:18px;color:#94a3b8">/</span><span class="red">{stats['total_fail']}</span></div></div>
</div>
<h2 style="margin-bottom:12px;font-size:16px">账号类型分布</h2>
<table>
<tr><th>类型</th><th>数量</th></tr>
{''.join(f'<tr><td>{t}</td><td>{c}</td></tr>' for t,c in sorted(stats['by_type'].items()))}
</table>
<div class="refresh">JSON: <span class="api-url">/health?format=json</span></div>
</div></body></html>""")

    @router.get("/api/system/latest-version")
    async def get_latest_version(authorization: str | None = Header(default=None)):
        """获取云端最新版本信息

        由后端代理请求 GitHub，避免用户本地无法访问 GitHub 的问题。
        返回最新版本号和 CHANGELOG 内容。
        """
        import asyncio
        import httpx

        require_admin(authorization)

        version_url = "https://raw.githubusercontent.com/hearthealt/chatgpt2api/main/VERSION"
        changelog_url = "https://raw.githubusercontent.com/hearthealt/chatgpt2api/main/CHANGELOG.md"

        # 复用系统配置的上游代理（如果有），保证后端能访问 GitHub
        proxy = ""
        try:
            profile = proxy_settings.get_profile(upstream=True)
            proxy = getattr(profile, "proxy_url", "") or ""
        except Exception:
            proxy = ""

        client_kwargs: dict[str, Any] = {
            "timeout": 15.0,
            "verify": not proxy_settings.should_skip_ssl_verify(),
        }
        if proxy:
            client_kwargs["proxy"] = proxy

        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                version_resp, changelog_resp = await asyncio.gather(
                    client.get(version_url),
                    client.get(changelog_url),
                )

            if version_resp.status_code != 200:
                return {
                    "status": "error",
                    "message": f"获取版本信息失败：HTTP {version_resp.status_code}",
                }

            return {
                "status": "success",
                "version": version_resp.text.strip(),
                "changelog": changelog_resp.text if changelog_resp.status_code == 200 else "",
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"获取云端版本失败：{type(e).__name__}: {str(e)}",
            }

    @router.post("/api/system/update")
    async def trigger_update(authorization: str | None = Header(default=None)):
        """触发系统更新

        调用 Watchtower API 触发容器更新。
        Watchtower 会：
        1. 拉取最新镜像
        2. 停止当前容器
        3. 启动新容器
        4. 清理旧镜像

        需要管理员权限
        """
        import os
        import httpx

        require_admin(authorization)

        # 获取 Watchtower API Token
        token = os.getenv("WATCHTOWER_HTTP_API_TOKEN", "")
        if not token or token == "changeme":
            return {
                "status": "error",
                "message": "Watchtower API token not configured. Please set WATCHTOWER_HTTP_API_TOKEN environment variable."
            }

        # Watchtower HTTP API 端点
        watchtower_url = os.getenv("WATCHTOWER_URL", "http://chatgpt2api-watchtower:8080")
        update_endpoint = f"{watchtower_url}/v1/update"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    update_endpoint,
                    headers={"Authorization": f"Bearer {token}"}
                )

                if response.status_code == 200:
                    return {
                        "status": "success",
                        "message": "更新已触发，容器将在几秒内重启。请稍后刷新页面。"
                    }
                elif response.status_code == 204:
                    return {
                        "status": "no_update",
                        "message": "当前已是最新版本，无需更新。"
                    }
                else:
                    return {
                        "status": "error",
                        "message": f"Watchtower API returned status {response.status_code}: {response.text}"
                    }

        except httpx.ConnectError as e:
            return {
                "status": "error",
                "message": f"无法连接到 Watchtower 服务，请检查 Watchtower 容器是否正常运行。({type(e).__name__})"
            }
        except (httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError, httpx.WriteError) as e:
            # Watchtower 在更新时会重启本容器，导致本次请求连接被中断。
            # 这类异常恰恰说明更新已经开始，视为成功。
            return {
                "status": "success",
                "message": "更新已触发，容器将在几秒内重启。请稍后刷新页面。"
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"触发更新时发生错误：{type(e).__name__}: {str(e)}"
            }

    return router
