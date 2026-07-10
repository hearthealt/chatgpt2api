from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import uuid
from datetime import datetime, timezone
from threading import Lock
from typing import Literal

from services.config import config
from services.storage.base import StorageBackend

UserRole = Literal["admin", "user"]

_PBKDF2_ROUNDS = 200_000
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
_MIN_PASSWORD_LEN = 6


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_password(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), _PBKDF2_ROUNDS
    ).hex()


def _verify_password(password: str, salt_hex: str, expected_hash: str) -> bool:
    if not salt_hex or not expected_hash:
        return False
    try:
        candidate = _hash_password(password, salt_hex)
    except ValueError:
        return False
    return hmac.compare_digest(candidate, expected_hash)


def _normalize_quota(raw: object) -> dict[str, object]:
    source = raw if isinstance(raw, dict) else {}

    def _coerce(value: object, default: int) -> int:
        try:
            result = int(value)
        except (TypeError, ValueError):
            return default
        return result if result >= -1 else default

    return {
        "call_limit": _coerce(source.get("call_limit"), 0),
        "image_limit": _coerce(source.get("image_limit"), 0),
        "period": str(source.get("period") or "").strip().lower(),
    }


def _normalize_allowed_models(raw: object) -> list[str]:
    """规范化允许使用的模型列表，coerce 为 list[str]，去重"""
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


class UserService:
    def __init__(self, storage: StorageBackend):
        self.storage = storage
        self._lock = Lock()
        self._items = self._load()
        self._last_login_flush_at: dict[str, datetime] = {}

    @staticmethod
    def _clean(value: object) -> str:
        return str(value or "").strip()

    def _normalize_item(self, raw: object) -> dict[str, object] | None:
        if not isinstance(raw, dict):
            return None
        username = self._clean(raw.get("username"))
        password_hash = self._clean(raw.get("password_hash"))
        salt = self._clean(raw.get("salt"))
        if not username or not password_hash or not salt:
            return None
        role = self._clean(raw.get("role")).lower()
        if role not in {"admin", "user"}:
            role = "user"
        item_id = self._clean(raw.get("id")) or f"u_{uuid.uuid4().hex[:12]}"
        try:
            password_version = int(raw.get("password_version") or 1)
        except (TypeError, ValueError):
            password_version = 1
        return {
            "id": item_id,
            "username": username,
            "password_hash": password_hash,
            "salt": salt,
            "password_version": max(1, password_version),
            "role": role,
            "enabled": bool(raw.get("enabled", True)),
            "created_at": self._clean(raw.get("created_at")) or _now_iso(),
            "last_login_at": self._clean(raw.get("last_login_at")) or None,
            "quota": _normalize_quota(raw.get("quota")),
            "allowed_models": _normalize_allowed_models(raw.get("allowed_models")),
        }

    def _load(self) -> list[dict[str, object]]:
        try:
            items = self.storage.load_users()
        except Exception:
            return []
        if not isinstance(items, list):
            return []
        return [normalized for item in items if (normalized := self._normalize_item(item)) is not None]

    def _save(self) -> None:
        self.storage.save_users(self._items)

    def _reload_locked(self) -> None:
        self._items = self._load()

    @staticmethod
    def public_user(item: dict[str, object]) -> dict[str, object]:
        return {
            "id": item.get("id"),
            "username": item.get("username"),
            "role": item.get("role"),
            "enabled": bool(item.get("enabled", True)),
            "created_at": item.get("created_at"),
            "last_login_at": item.get("last_login_at"),
            "quota": item.get("quota") or {"call_limit": 0, "image_limit": 0, "period": ""},
            "allowed_models": item.get("allowed_models") or [],
        }

    def _validate_username_locked(self, username: str, *, exclude_id: str = "") -> str:
        candidate = self._clean(username)
        if not _USERNAME_RE.match(candidate):
            raise ValueError("用户名需为 3-32 位字母、数字、点、下划线或连字符")
        lowered = candidate.lower()
        for item in self._items:
            if exclude_id and self._clean(item.get("id")) == exclude_id:
                continue
            if self._clean(item.get("username")).lower() == lowered:
                raise ValueError("这个用户名已经被占用了，换一个吧")
        return candidate

    @staticmethod
    def _validate_password(password: str) -> str:
        candidate = str(password or "")
        if len(candidate) < _MIN_PASSWORD_LEN:
            raise ValueError(f"密码至少需要 {_MIN_PASSWORD_LEN} 位")
        return candidate

    def create_user(
        self,
        username: str,
        password: str,
        *,
        role: UserRole = "user",
        quota: dict[str, object] | None = None,
        allowed_models: list[str] | None = None,
    ) -> dict[str, object]:
        with self._lock:
            self._reload_locked()
            normalized_username = self._validate_username_locked(username)
            normalized_password = self._validate_password(password)
            salt = secrets.token_hex(16)
            item = {
                "id": f"u_{uuid.uuid4().hex[:12]}",
                "username": normalized_username,
                "password_hash": _hash_password(normalized_password, salt),
                "salt": salt,
                "password_version": 1,
                "role": "admin" if str(role).lower() == "admin" else "user",
                "enabled": True,
                "created_at": _now_iso(),
                "last_login_at": None,
                "quota": _normalize_quota(quota),
                "allowed_models": _normalize_allowed_models(allowed_models),
            }
            self._items.append(item)
            self._save()
            return self.public_user(item)

    def get_user(self, user_id: str) -> dict[str, object] | None:
        normalized = self._clean(user_id)
        if not normalized:
            return None
        with self._lock:
            for item in self._items:
                if self._clean(item.get("id")) == normalized:
                    return dict(item)
        return None

    def get_by_username(self, username: str) -> dict[str, object] | None:
        lowered = self._clean(username).lower()
        if not lowered:
            return None
        with self._lock:
            for item in self._items:
                if self._clean(item.get("username")).lower() == lowered:
                    return dict(item)
        return None

    def list_users(self) -> list[dict[str, object]]:
        with self._lock:
            self._reload_locked()
            return [self.public_user(item) for item in self._items]

    def authenticate_password(self, username: str, password: str) -> dict[str, object] | None:
        lowered = self._clean(username).lower()
        if not lowered or not password:
            return None
        with self._lock:
            for index, item in enumerate(self._items):
                if self._clean(item.get("username")).lower() != lowered:
                    continue
                if not bool(item.get("enabled", True)):
                    return None
                if not _verify_password(password, self._clean(item.get("salt")), self._clean(item.get("password_hash"))):
                    return None
                now = datetime.now(timezone.utc)
                next_item = dict(item)
                next_item["last_login_at"] = now.isoformat()
                self._items[index] = next_item
                user_id = self._clean(next_item.get("id"))
                last_flush = self._last_login_flush_at.get(user_id)
                if last_flush is None or (now - last_flush).total_seconds() >= 60:
                    try:
                        self._save()
                        self._last_login_flush_at[user_id] = now
                    except Exception:
                        pass
                return dict(next_item)
        return None

    def update_user(self, user_id: str, updates: dict[str, object]) -> dict[str, object] | None:
        normalized_id = self._clean(user_id)
        if not normalized_id:
            return None
        with self._lock:
            self._reload_locked()
            for index, item in enumerate(self._items):
                if self._clean(item.get("id")) != normalized_id:
                    continue
                next_item = dict(item)
                if "username" in updates and updates.get("username") is not None:
                    next_item["username"] = self._validate_username_locked(
                        str(updates.get("username") or ""), exclude_id=normalized_id
                    )
                if "enabled" in updates and updates.get("enabled") is not None:
                    next_item["enabled"] = bool(updates.get("enabled"))
                if "role" in updates and updates.get("role") is not None:
                    role = str(updates.get("role")).lower()
                    next_item["role"] = "admin" if role == "admin" else "user"
                if "quota" in updates and updates.get("quota") is not None:
                    next_item["quota"] = _normalize_quota(updates.get("quota"))
                if "allowed_models" in updates and updates.get("allowed_models") is not None:
                    next_item["allowed_models"] = _normalize_allowed_models(updates.get("allowed_models"))
                self._items[index] = next_item
                self._save()
                return self.public_user(next_item)
        return None

    def _set_password_locked(self, index: int, item: dict[str, object], new_password: str) -> dict[str, object]:
        normalized_password = self._validate_password(new_password)
        salt = secrets.token_hex(16)
        next_item = dict(item)
        next_item["salt"] = salt
        next_item["password_hash"] = _hash_password(normalized_password, salt)
        next_item["password_version"] = int(item.get("password_version") or 1) + 1
        self._items[index] = next_item
        self._save()
        return next_item

    def set_password(self, user_id: str, new_password: str) -> dict[str, object] | None:
        """管理员重置密码（无需旧密码）。"""
        normalized_id = self._clean(user_id)
        if not normalized_id:
            return None
        with self._lock:
            self._reload_locked()
            for index, item in enumerate(self._items):
                if self._clean(item.get("id")) == normalized_id:
                    return dict(self._set_password_locked(index, item, new_password))
        return None

    def verify_and_change_password(
        self, user_id: str, old_password: str, new_password: str
    ) -> dict[str, object] | None:
        """用户自助改密：先校验旧密码。旧密码错误抛 ValueError。"""
        normalized_id = self._clean(user_id)
        if not normalized_id:
            return None
        with self._lock:
            self._reload_locked()
            for index, item in enumerate(self._items):
                if self._clean(item.get("id")) != normalized_id:
                    continue
                if not _verify_password(old_password, self._clean(item.get("salt")), self._clean(item.get("password_hash"))):
                    raise ValueError("当前密码不正确")
                return dict(self._set_password_locked(index, item, new_password))
        return None

    def delete_user(self, user_id: str) -> bool:
        normalized_id = self._clean(user_id)
        if not normalized_id:
            return False
        with self._lock:
            self._reload_locked()
            before = len(self._items)
            self._items = [item for item in self._items if self._clean(item.get("id")) != normalized_id]
            if len(self._items) == before:
                return False
            self._save()
            return True

    def has_admin(self) -> bool:
        with self._lock:
            self._reload_locked()
            return any(str(item.get("role") or "").lower() == "admin" for item in self._items)

    def count_admins(self, *, enabled_only: bool = False) -> int:
        with self._lock:
            self._reload_locked()
            return sum(
                1
                for item in self._items
                if str(item.get("role") or "").lower() == "admin"
                and (not enabled_only or bool(item.get("enabled", True)))
            )

    def is_last_enabled_admin(self, user_id: str) -> bool:
        """该用户是否为当前唯一的启用管理员（用于防止误删/禁用导致锁死）。"""
        normalized_id = self._clean(user_id)
        if not normalized_id:
            return False
        with self._lock:
            self._reload_locked()
            enabled_admin_ids = [
                self._clean(item.get("id"))
                for item in self._items
                if str(item.get("role") or "").lower() == "admin" and bool(item.get("enabled", True))
            ]
        return enabled_admin_ids == [normalized_id]

    def ensure_default_admin(self) -> dict[str, object] | None:
        """启动时若不存在任何管理员账号，则用环境变量引导创建默认管理员。

        环境变量：
          CHATGPT2API_ADMIN_USERNAME（默认 admin）
          CHATGPT2API_ADMIN_PASSWORD（默认 admin12345，强烈建议自定义）
        """
        if self.has_admin():
            return None
        username = str(os.getenv("CHATGPT2API_ADMIN_USERNAME") or "admin").strip() or "admin"
        password = str(os.getenv("CHATGPT2API_ADMIN_PASSWORD") or "admin12345")
        # 若用户名已被占用（历史普通用户），退让一个带后缀的名字，避免启动失败
        existing = self.get_by_username(username)
        if existing is not None:
            username = f"{username}_admin"
        try:
            user = self.create_user(username, password, role="admin", quota={"call_limit": -1, "image_limit": -1})
        except ValueError:
            return None
        print(f"[user-service] 已创建默认管理员账号：{username}（请尽快登录后修改密码）", flush=True)
        return user


user_service = UserService(config.get_storage_backend())
