from __future__ import annotations

import secrets
from datetime import datetime, timezone
from threading import Lock

from services.config import config


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_code() -> str:
    # 去除易混字符，生成 10 位邀请码
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(10))


class InviteService:
    def __init__(self):
        self._lock = Lock()

    @staticmethod
    def _public(item: dict[str, object]) -> dict[str, object]:
        max_uses = int(item.get("max_uses") or 0)
        used = int(item.get("used_count") or 0)
        return {
            "code": item.get("code"),
            "max_uses": max_uses,
            "used_count": used,
            "unlimited": max_uses == 0,
            "remaining": None if max_uses == 0 else max(0, max_uses - used),
            "enabled": bool(item.get("enabled", True)),
            "created_at": item.get("created_at"),
            "note": item.get("note") or "",
            "exhausted": max_uses != 0 and used >= max_uses,
        }

    def list_codes(self) -> list[dict[str, object]]:
        return [self._public(item) for item in config.get_invite_codes()]

    def generate(self, *, count: int = 1, max_uses: int = 1, note: str = "") -> list[dict[str, object]]:
        count = max(1, min(int(count or 1), 200))
        max_uses = max(0, int(max_uses if max_uses is not None else 1))
        with self._lock:
            existing = config.get_invite_codes()
            existing_codes = {str(item.get("code") or "").lower() for item in existing}
            created: list[dict[str, object]] = []
            for _ in range(count):
                code = _gen_code()
                while code.lower() in existing_codes:
                    code = _gen_code()
                existing_codes.add(code.lower())
                item = {
                    "code": code,
                    "max_uses": max_uses,
                    "used_count": 0,
                    "enabled": True,
                    "created_at": _now_iso(),
                    "note": str(note or "").strip(),
                }
                existing.append(item)
                created.append(item)
            config.update({"invite_codes": existing})
        return [self._public(item) for item in created]

    def set_enabled(self, code: str, enabled: bool) -> bool:
        normalized = str(code or "").strip().lower()
        if not normalized:
            return False
        with self._lock:
            items = config.get_invite_codes()
            found = False
            for item in items:
                if str(item.get("code") or "").lower() == normalized:
                    item["enabled"] = bool(enabled)
                    found = True
            if found:
                config.update({"invite_codes": items})
            return found

    def delete(self, code: str) -> bool:
        normalized = str(code or "").strip().lower()
        if not normalized:
            return False
        with self._lock:
            items = config.get_invite_codes()
            next_items = [item for item in items if str(item.get("code") or "").lower() != normalized]
            if len(next_items) == len(items):
                return False
            config.update({"invite_codes": next_items})
            return True

    def validate(self, code: str) -> bool:
        """仅校验邀请码是否可用（启用且未用尽），不消费。"""
        normalized = str(code or "").strip().lower()
        if not normalized:
            return False
        for item in config.get_invite_codes():
            if str(item.get("code") or "").lower() != normalized:
                continue
            if not bool(item.get("enabled", True)):
                return False
            max_uses = int(item.get("max_uses") or 0)
            used = int(item.get("used_count") or 0)
            return max_uses == 0 or used < max_uses
        return False

    def consume(self, code: str) -> bool:
        """校验并消费一次邀请码；成功返回 True。启用、未用尽方可消费。"""
        normalized = str(code or "").strip().lower()
        if not normalized:
            return False
        with self._lock:
            items = config.get_invite_codes()
            for item in items:
                if str(item.get("code") or "").lower() != normalized:
                    continue
                if not bool(item.get("enabled", True)):
                    return False
                max_uses = int(item.get("max_uses") or 0)
                used = int(item.get("used_count") or 0)
                if max_uses != 0 and used >= max_uses:
                    return False
                item["used_count"] = used + 1
                config.update({"invite_codes": items})
                return True
        return False


invite_service = InviteService()
