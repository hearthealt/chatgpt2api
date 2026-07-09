from __future__ import annotations

from threading import Lock
from typing import Any

from services.config import DATA_DIR
from services.json_file import read_json_file, write_json_file

# 每个用户的对话历史（Studio 会话）在服务端按 owner_id 持久化，
# 使历史跟随账号，跨浏览器/重新登录后仍可访问。
_MAX_CONVERSATIONS = 200


def _owner_id(identity: dict[str, object]) -> str:
    return str(identity.get("id") or "").strip() or "anonymous"


class ConversationStore:
    def __init__(self, path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._data = self._load()

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        raw = read_json_file(self.path, name=self.path.name, default_factory=dict, expected_types=dict)
        if not isinstance(raw, dict):
            return {}
        result: dict[str, list[dict[str, Any]]] = {}
        for owner, conversations in raw.items():
            if isinstance(conversations, list):
                result[str(owner)] = conversations
        return result

    def _save_locked(self) -> None:
        write_json_file(self.path, self._data)

    def get(self, identity: dict[str, object]) -> list[dict[str, Any]]:
        owner = _owner_id(identity)
        with self._lock:
            return list(self._data.get(owner, []))

    def replace(self, identity: dict[str, object], conversations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        owner = _owner_id(identity)
        cleaned = [item for item in conversations if isinstance(item, dict)][:_MAX_CONVERSATIONS]
        with self._lock:
            self._data[owner] = cleaned
            self._save_locked()
            return list(cleaned)


conversation_store = ConversationStore(DATA_DIR / "conversations.json")
