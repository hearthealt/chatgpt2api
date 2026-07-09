from __future__ import annotations

import time
from datetime import timedelta
from threading import Lock
from typing import Any, Literal

from fastapi import HTTPException

from services.auth_service import auth_service
from services.config import config
from services.log_service import LOG_TYPE_CALL, log_service
from services.user_service import user_service
from utils.timezone import beijing_now, parse_to_beijing_naive

QuotaKind = Literal["call", "image"]

_CACHE_TTL_SECONDS = 30
_usage_cache: dict[str, tuple[float, dict[str, int]]] = {}
_cache_lock = Lock()

_user_stats_cache: dict[str, object] = {"at": 0.0, "data": None}
_USER_STATS_TTL = 60.0


def _clean(value: object) -> str:
    return str(value or "").strip()


def resolve_user_for_identity(identity: dict[str, object]) -> dict[str, Any] | None:
    """将请求 identity 映射到用户账户。

    - 会话令牌：identity['id'] 直接是 user.id
    - API key：identity['id'] 是 key.id，需通过 key 的 owner_user_id 反查
    - legacy admin / 无归属 key：返回 None（不限额）
    """
    identity_id = _clean(identity.get("id"))
    if not identity_id or identity_id == "admin":
        return None
    # 会话令牌路径
    user = user_service.get_user(identity_id)
    if user is not None:
        return user
    # API key 路径：反查 owner_user_id
    for item in auth_service.list_keys():
        if _clean(item.get("id")) == identity_id:
            owner_user_id = _clean(item.get("owner_user_id"))
            return user_service.get_user(owner_user_id) if owner_user_id else None
    return None


def owner_key_ids(user: dict[str, Any]) -> set[str]:
    """用户名下用于用量聚合的所有 id：user.id 本身 + 名下所有 API key id。"""
    user_id = _clean(user.get("id"))
    ids: set[str] = {user_id} if user_id else set()
    for item in auth_service.list_owner_keys(user_id):
        key_id = _clean(item.get("id"))
        if key_id:
            ids.add(key_id)
    return ids


def _resolve_period(user: dict[str, Any]) -> str:
    quota = user.get("quota") if isinstance(user.get("quota"), dict) else {}
    period = _clean(quota.get("period")).lower()
    if period in {"daily", "monthly", "total"}:
        return period
    return _clean(config.get_user_access_settings().get("period")) or "monthly"


def _period_start_date(period: str) -> str:
    """返回 YYYY-MM-DD 起点（北京时区），total 返回空串。"""
    if period == "total":
        return ""
    now = beijing_now()
    if period == "daily":
        return now.strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-01")


def _is_image_endpoint(endpoint: str, model: str) -> bool:
    endpoint = endpoint.lower()
    if "/images/" in endpoint or "/image-tasks/" in endpoint:
        return True
    return "image" in model.lower()


def _compute_usage(user: dict[str, Any]) -> dict[str, int]:
    key_ids = owner_key_ids(user)
    if not key_ids:
        return {"calls": 0, "images": 0}
    period = _resolve_period(user)
    start_date = _period_start_date(period)
    # 拉取较大窗口的调用日志后按 owner 过滤
    items = log_service.list(type=LOG_TYPE_CALL, start_date=start_date, limit=20000)
    calls = 0
    images = 0
    for item in items:
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        key_id = _clean(detail.get("key_id") or item.get("key_id"))
        if key_id not in key_ids:
            continue
        # 只统计成功调用：失败/报错的调用不扣额度
        status = _clean(detail.get("status") or item.get("status")).lower()
        if status in {"failed", "error", "fail"} or detail.get("error") or detail.get("error_code"):
            continue
        endpoint = _clean(detail.get("endpoint"))
        model = _clean(detail.get("model"))
        if _is_image_endpoint(endpoint, model):
            images += 1
        else:
            calls += 1
    return {"calls": calls, "images": images}


def current_usage(user: dict[str, Any], *, force_refresh: bool = False) -> dict[str, int]:
    user_id = _clean(user.get("id"))
    if not user_id:
        return {"calls": 0, "images": 0}
    now = time.time()
    if not force_refresh:
        with _cache_lock:
            cached = _usage_cache.get(user_id)
            if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
                return dict(cached[1])
    usage = _compute_usage(user)
    with _cache_lock:
        _usage_cache[user_id] = (now, dict(usage))
    return usage


def invalidate_usage_cache(user_id: str) -> None:
    with _cache_lock:
        _usage_cache.pop(_clean(user_id), None)


def _effective_limit(user: dict[str, Any], kind: QuotaKind) -> int:
    """返回该用户对应类型的上限：-1=无限。"""
    quota = user.get("quota") if isinstance(user.get("quota"), dict) else {}
    field = "image_limit" if kind == "image" else "call_limit"
    try:
        user_limit = int(quota.get(field, 0))
    except (TypeError, ValueError):
        user_limit = 0
    if user_limit == -1:
        return -1
    if user_limit > 0:
        return user_limit
    # 0 = 使用全局默认
    defaults = config.get_user_access_settings()
    default_field = "default_image_limit" if kind == "image" else "default_call_limit"
    try:
        return int(defaults.get(default_field, 0))
    except (TypeError, ValueError):
        return 0


def effective_limits(user: dict[str, Any]) -> dict[str, int]:
    """该用户实际生效的额度（把 0=默认 解析成具体数字，-1=无限）。"""
    return {
        "call_limit": _effective_limit(user, "call"),
        "image_limit": _effective_limit(user, "image"),
    }


def default_quota_for_new_user() -> dict[str, object]:
    """注册/建号时把当前全局默认额度固化到用户，避免后续改默认影响老用户。"""
    defaults = config.get_user_access_settings()

    def _coerce(value: object) -> int:
        try:
            result = int(value)
        except (TypeError, ValueError):
            return 0
        return result if result >= -1 else 0

    return {
        "call_limit": _coerce(defaults.get("default_call_limit")),
        "image_limit": _coerce(defaults.get("default_image_limit")),
        "period": str(defaults.get("period") or "monthly"),
    }


def count_active_owner_images(identity: dict[str, object]) -> int:
    """当前用户名下在途（排队/运行中）的图片任务数，用于堵住并发越界。"""
    try:
        from services.image_task_service import image_task_service
        return image_task_service.count_active_images(identity)
    except Exception:
        return 0


def quota_snapshot(user: dict[str, Any]) -> dict[str, Any]:
    """给 /api/me 与 /api/me/usage 用的完整配额+用量快照。"""
    usage = current_usage(user)
    call_limit = _effective_limit(user, "call")
    image_limit = _effective_limit(user, "image")
    period = _resolve_period(user)

    def _remaining(limit: int, used: int) -> int:
        if limit == -1:
            return -1
        return max(0, limit - used)

    return {
        "period": period,
        "calls": {
            "limit": call_limit,
            "used": usage["calls"],
            "remaining": _remaining(call_limit, usage["calls"]),
        },
        "images": {
            "limit": image_limit,
            "used": usage["images"],
            "remaining": _remaining(image_limit, usage["images"]),
        },
    }


def check_quota(identity: dict[str, object], *, kind: QuotaKind) -> None:
    """在调用入口做配额校验，超额抛 429。无归属 identity（admin/无主 key）不限额。"""
    if str(identity.get("role") or "").lower() == "admin":
        return
    user = resolve_user_for_identity(identity)
    if user is None:
        return
    if str(user.get("role") or "").lower() == "admin":
        return
    limit = _effective_limit(user, kind)
    if limit == -1 or limit <= 0:
        return
    # 实时计算，避免缓存造成的越界
    usage = current_usage(user, force_refresh=True)
    used = usage["images"] if kind == "image" else usage["calls"]
    # 图片为异步任务：把在途（排队/运行中、尚未落日志）的任务计入，堵住并发越界
    if kind == "image":
        used += count_active_owner_images(identity)
    if used >= limit:
        period = _resolve_period(user)
        period_label = "今日" if period == "daily" else "本月" if period == "monthly" else "累计"
        kind_label = "生图" if kind == "image" else "对话"
        message = f"{kind_label}额度已用完（{period_label}上限 {limit} 次）。如需继续使用，请联系管理员提升额度。"
        raise HTTPException(
            status_code=429,
            detail={
                "error": message,
                "code": "quota_exceeded",
                "kind": kind,
                "limit": limit,
                "used": used,
                "period": period,
            },
        )


def _key_to_user_index() -> dict[str, dict[str, str]]:
    """构建 key_id -> {user_id, username} 映射（用户自身 id + 其名下 API key id）。"""
    users = user_service.list_users()
    by_id = {str(u.get("id")): str(u.get("username") or "") for u in users}
    index: dict[str, dict[str, str]] = {}
    for uid, uname in by_id.items():
        index[uid] = {"user_id": uid, "username": uname}
    for key in auth_service.list_keys():
        owner = str(key.get("owner_user_id") or "").strip()
        key_id = str(key.get("id") or "").strip()
        if key_id and owner and owner in by_id:
            index[key_id] = {"user_id": owner, "username": by_id[owner]}
    return index


def build_user_stats(*, top_n: int = 10, trend_days: int = 14, rank_days: int = 30) -> dict[str, Any]:
    """概览中心用：用户数量、用量排行、活跃用户趋势。带 60s 缓存以避免频繁扫日志。"""
    now_ts = time.time()
    with _cache_lock:
        cached = _user_stats_cache.get("data")
        if cached is not None and (now_ts - float(_user_stats_cache.get("at") or 0)) < _USER_STATS_TTL:
            return cached  # type: ignore[return-value]

    users = user_service.list_users()
    now = beijing_now()
    today_str = now.strftime("%Y-%m-%d")
    total = len(users)
    enabled = sum(1 for u in users if bool(u.get("enabled", True)))
    admins = sum(1 for u in users if str(u.get("role") or "").lower() == "admin")
    new_today = sum(1 for u in users if _created_today(u, today_str))
    counts = {
        "total": total,
        "enabled": enabled,
        "disabled": total - enabled,
        "admins": admins,
        "new_today": new_today,
    }

    key_index = _key_to_user_index()
    # 排行窗口与趋势窗口取较大者，单遍扫描日志
    window_days = max(int(trend_days), int(rank_days))
    start_date = (now - timedelta(days=window_days - 1)).strftime("%Y-%m-%d")
    try:
        items = log_service.list(type=LOG_TYPE_CALL, start_date=start_date, limit=20000)
    except Exception:
        items = []

    rank_start = now - timedelta(days=int(rank_days) - 1)
    # 趋势：最近 trend_days 天的每日去重活跃用户
    trend_labels = [(now - timedelta(days=i)).strftime("%m-%d") for i in range(int(trend_days) - 1, -1, -1)]
    trend_dates = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(int(trend_days) - 1, -1, -1)]
    active_by_day: dict[str, set[str]] = {d: set() for d in trend_dates}
    usage_by_user: dict[str, dict[str, Any]] = {}

    for item in items:
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        status = str(detail.get("status") or item.get("status") or "").lower()
        if status in {"failed", "error", "fail"} or detail.get("error") or detail.get("error_code"):
            continue
        key_id = str(detail.get("key_id") or item.get("key_id") or "").strip()
        mapped = key_index.get(key_id)
        if not mapped:
            continue
        uid = mapped["user_id"]
        uname = mapped["username"]
        dt = parse_to_beijing_naive(detail.get("started_at") or item.get("time"))
        day = dt.strftime("%Y-%m-%d") if dt else ""
        # 活跃趋势
        if day in active_by_day:
            active_by_day[day].add(uid)
        # 排行（仅统计 rank 窗口内）
        if dt is not None and dt >= rank_start.replace(tzinfo=None):
            endpoint = str(detail.get("endpoint") or "")
            model = str(detail.get("model") or "")
            bucket = usage_by_user.setdefault(uid, {"username": uname, "calls": 0, "images": 0})
            if _is_image_endpoint(endpoint, model):
                bucket["images"] += 1
            else:
                bucket["calls"] += 1

    top_users = sorted(
        (
            {"username": v["username"], "calls": v["calls"], "images": v["images"], "total": v["calls"] + v["images"]}
            for v in usage_by_user.values()
        ),
        key=lambda x: x["total"],
        reverse=True,
    )[: int(top_n)]

    result = {
        "counts": counts,
        "top_users": top_users,
        "active_trend": {
            "labels": trend_labels,
            "values": [len(active_by_day[d]) for d in trend_dates],
        },
        "rank_days": int(rank_days),
        "trend_days": int(trend_days),
    }
    with _cache_lock:
        _user_stats_cache["at"] = now_ts
        _user_stats_cache["data"] = result
    return result


def _created_today(user: dict[str, Any], today_str: str) -> bool:
    dt = parse_to_beijing_naive(user.get("created_at"))
    return bool(dt and dt.strftime("%Y-%m-%d") == today_str)