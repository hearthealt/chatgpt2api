from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

from services.config import DATA_DIR
from services.json_file import read_json_file, write_json_file


DELETED_IMAGES_FILE = DATA_DIR / "deleted_images.json"
_IMAGE_MARKDOWN_RE = re.compile(r"!\[[^\]]*\]\((?P<url>(?:https?://|/images/|/image-thumbnails/)[^\s)\"']+)\)")
_MAX_DELETED_IMAGES = 20000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_rel(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/").lstrip("/")
    if not text:
        return ""
    parts = PurePosixPath(text).parts
    if any(part in {"", ".", ".."} for part in parts):
        return ""
    return PurePosixPath(*parts).as_posix()


def image_rel_from_url(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        try:
            text = urlparse(text).path
        except Exception:
            return ""
    text = unquote(text).replace("\\", "/").lstrip("/")
    for prefix in ("images/", "image-thumbnails/"):
        if text.startswith(prefix):
            return _safe_rel(text[len(prefix):])
    return _safe_rel(text)


def _looks_like_local_image_ref(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text.startswith(("http://", "https://")):
        try:
            text = urlparse(text).path
        except Exception:
            return False
    text = unquote(text).replace("\\", "/").lstrip("/")
    if text.startswith(("images/", "image-thumbnails/")):
        return True
    suffix = PurePosixPath(text).suffix.lower()
    return suffix in {".png", ".jpg", ".jpeg", ".webp"} and len(PurePosixPath(text).parts) >= 2


def _load_deleted() -> dict[str, str]:
    data = read_json_file(
        DELETED_IMAGES_FILE,
        name="deleted_images.json",
        default_factory=dict,
        expected_types=(dict, list),
    )
    if isinstance(data, list):
        return {image_rel_from_url(item): "" for item in data if image_rel_from_url(item)}
    if isinstance(data, dict):
        return {image_rel_from_url(key): str(value or "") for key, value in data.items() if image_rel_from_url(key)}
    return {}


def record_deleted_images(paths: list[str]) -> None:
    rels = [image_rel_from_url(path) for path in paths]
    rels = [rel for rel in rels if rel]
    if not rels:
        return
    data = _load_deleted()
    now = _now_iso()
    for rel in rels:
        data[rel] = now
    if len(data) > _MAX_DELETED_IMAGES:
        data = dict(sorted(data.items(), key=lambda item: item[1] or "")[-_MAX_DELETED_IMAGES:])
    DELETED_IMAGES_FILE.parent.mkdir(parents=True, exist_ok=True)
    write_json_file(DELETED_IMAGES_FILE, data)


def deleted_image_paths() -> set[str]:
    return set(_load_deleted())


def is_deleted_image_ref(value: object, deleted: set[str] | None = None, *, include_missing: bool = False) -> bool:
    rel = image_rel_from_url(value)
    if not rel:
        return False
    if rel in (deleted if deleted is not None else deleted_image_paths()):
        return True
    if not include_missing or not _looks_like_local_image_ref(value):
        return False
    try:
        from services.image_storage_service import image_storage_service

        return not image_storage_service.exists(rel)
    except Exception:
        return False


def filter_deleted_image_urls(urls: list[str], deleted: set[str] | None = None, *, include_missing: bool = False) -> list[str]:
    deleted_set = deleted if deleted is not None else deleted_image_paths()
    return [url for url in urls if not is_deleted_image_ref(url, deleted_set, include_missing=include_missing)]


def scrub_deleted_image_refs(value: Any, deleted: set[str] | None = None, *, include_missing: bool = False) -> Any:
    deleted_set = deleted if deleted is not None else deleted_image_paths()
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"urls", "_image_urls"} and isinstance(item, list):
                filtered = filter_deleted_image_urls(
                    [str(url) for url in item if str(url or "").strip()],
                    deleted_set,
                    include_missing=include_missing,
                )
                if filtered:
                    output[key] = filtered
                continue
            if key == "url" and isinstance(item, str) and is_deleted_image_ref(item, deleted_set, include_missing=include_missing):
                continue
            output[key] = scrub_deleted_image_refs(item, deleted_set, include_missing=include_missing)
        return output
    if isinstance(value, list):
        return [scrub_deleted_image_refs(item, deleted_set, include_missing=include_missing) for item in value]
    if isinstance(value, str):
        text = "" if is_deleted_image_ref(value, deleted_set, include_missing=include_missing) else value
        if not text:
            return ""
        return _IMAGE_MARKDOWN_RE.sub(
            lambda match: "[此图片已删除]" if is_deleted_image_ref(match.group("url"), deleted_set, include_missing=include_missing) else match.group(0),
            text,
        )
    return value
