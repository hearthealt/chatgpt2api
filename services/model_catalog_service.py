from __future__ import annotations

from typing import Any

from services.account_service import account_service
from services.config import config
from services.provider_service import provider_service
from utils.helper import CODEX_IMAGE_MODEL


FALLBACK_CHAT_MODELS = [
    "auto",
    "gpt-5",
    "gpt-5-1",
    "gpt-5-2",
    "gpt-5-3",
    "gpt-5-3-mini",
    "gpt-5-5",
    "gpt-5-mini",
]

FALLBACK_IMAGE_MODELS = [
    "gpt-image-2",
]


def _normalize_list(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    values: list[str] = []
    seen: set[str] = set()
    for item in raw:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
    return values


def _settings_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _configured_chat_models(settings: dict[str, Any]) -> list[str]:
    catalog = _settings_dict(settings.get("model_catalog"))
    explicit = _normalize_list(catalog.get("chat_models"))
    if explicit:
        return explicit

    combined: list[str] = []
    for key in ("base_chat_models", "specialized_chat_models", "image_capable_chat_models"):
        for model in _normalize_list(catalog.get(key)):
            if model not in combined:
                combined.append(model)
    return combined


def _configured_image_models(settings: dict[str, Any]) -> list[str]:
    image_generation = _settings_dict(settings.get("image_generation"))
    catalog = _settings_dict(settings.get("model_catalog"))
    for source in (
        image_generation.get("model_options"),
        catalog.get("image_api_models"),
        image_generation.get("supported_models"),
    ):
        models = _normalize_list(source)
        if models:
            return models
    return []


def _image_models_from_accounts(accounts: list[dict[str, Any]]) -> list[str]:
    available_accounts = [
        account
        for account in accounts
        if isinstance(account, dict) and account_service._is_image_account_available(account)
    ]
    if not available_accounts:
        return []

    models: list[str] = ["gpt-image-2"]
    codex_types = {
        normalized
        for account in available_accounts
        if account_service._normalize_source_type(account.get("source_type")) == "codex"
        and (normalized := account_service._normalize_account_type(account.get("type")))
    }

    if codex_types & {"Plus", "Team", "Pro"}:
        models.append(CODEX_IMAGE_MODEL)
    for plan_type in ("Plus", "Team", "Pro"):
        if plan_type in codex_types:
            models.append(f"{plan_type.lower()}-{CODEX_IMAGE_MODEL}")
    return models


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def get_model_catalog() -> dict[str, Any]:
    settings = config.get()
    configured_chat_models = _configured_chat_models(settings)
    configured_image_models = _configured_image_models(settings)

    chat_source = "config" if configured_chat_models else "fallback"
    chat_models = configured_chat_models or list(FALLBACK_CHAT_MODELS)

    if configured_image_models:
        image_source = "config"
        image_models = configured_image_models
    else:
        account_models = _image_models_from_accounts(account_service.list_accounts())
        image_source = "accounts" if account_models else "fallback"
        image_models = account_models or list(FALLBACK_IMAGE_MODELS)

    chat_models = _unique(chat_models)
    image_models = _unique(image_models)

    # 追加第三方 Provider 的模型（对话 + 生图）。
    provider_chat_models = provider_service.list_chat_models()
    provider_image_models = provider_service.list_image_models()
    if provider_chat_models:
        chat_models = _unique([*chat_models, *provider_chat_models])
    if provider_image_models:
        image_models = _unique([*image_models, *provider_image_models])

    # 追加自定义模型（管理员手动添加的模型）
    catalog_settings = config.get_model_catalog_settings()
    custom_chat_models = catalog_settings.get("custom_chat_models") or []
    custom_image_models = catalog_settings.get("custom_image_models") or []
    if custom_chat_models:
        chat_models = _unique([*chat_models, *custom_chat_models])
    if custom_image_models:
        image_models = _unique([*image_models, *custom_image_models])

    enabled_models = catalog_settings.get("enabled_models") or []
    disabled_models = catalog_settings.get("disabled_models") or []

    if enabled_models:
        # 白名单模式：只保留启用的
        enabled_set = set(enabled_models)
        chat_models = [m for m in chat_models if m in enabled_set]
        image_models = [m for m in image_models if m in enabled_set]
    elif disabled_models:
        # 黑名单模式：排除禁用的
        disabled_set = set(disabled_models)
        chat_models = [m for m in chat_models if m not in disabled_set]
        image_models = [m for m in image_models if m not in disabled_set]

    all_models = _unique([*chat_models, *image_models])

    return {
        "object": "model_catalog",
        "chat_models": chat_models,
        "image_models": image_models,
        "all_models": all_models,
        "source": {
            "chat": chat_source,
            "image": image_source,
        },
        "openai_models_endpoint": "s",
    }
