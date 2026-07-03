from __future__ import annotations

from dataclasses import dataclass, field

from services.config import config


@dataclass
class Provider:
    """第三方 OpenAI 兼容 API 提供商配置。"""

    name: str
    base_url: str
    api_key: str
    models: set[str] = field(default_factory=set)          # 对话模型
    image_models: set[str] = field(default_factory=set)    # 生图模型（走 chat completions）
    enabled: bool = True
    timeout_secs: int = 120
    proxy: str = ""

    @property
    def all_models(self) -> set[str]:
        return self.models | self.image_models


def _provider_from_dict(data: dict[str, object]) -> Provider | None:
    name = str(data.get("name") or "").strip()
    base_url = str(data.get("base_url") or "").strip().rstrip("/")
    if not name or not base_url:
        return None
    models = {str(item or "").strip() for item in data.get("models", []) if str(item or "").strip()}
    image_models = {str(item or "").strip() for item in data.get("image_models", []) if str(item or "").strip()}
    try:
        timeout_secs = max(1, int(data.get("timeout_secs") or 120))
    except (TypeError, ValueError):
        timeout_secs = 120
    return Provider(
        name=name,
        base_url=base_url,
        api_key=str(data.get("api_key") or "").strip(),
        models=models,
        image_models=image_models,
        enabled=bool(data.get("enabled", True)),
        timeout_secs=timeout_secs,
        proxy=str(data.get("proxy") or "").strip(),
    )


class ProviderService:
    """管理第三方 Provider 配置并按模型名路由。

    配置源自 config.json 的 `providers` 字段，跟随配置热加载。
    先匹配到的 Provider 优先（模型名全局唯一）。
    """

    def _providers(self) -> list[Provider]:
        raw = config.get_providers_settings()
        providers: list[Provider] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            provider = _provider_from_dict(item)
            if provider is not None and provider.enabled:
                providers.append(provider)
        return providers

    def resolve(self, model: str) -> Provider | None:
        """按模型名查找 Provider，未找到返回 None（走原有逻辑）。"""
        target = str(model or "").strip()
        if not target:
            return None
        for provider in self._providers():
            if target in provider.all_models:
                return provider
        return None

    def is_provider_model(self, model: str) -> bool:
        """判断是否为第三方 Provider 的模型（对话或生图）。"""
        return self.resolve(model) is not None

    def is_provider_image_model(self, model: str) -> bool:
        """判断是否为第三方 Provider 的生图模型。"""
        target = str(model or "").strip()
        if not target:
            return False
        for provider in self._providers():
            if target in provider.image_models:
                return True
            if target in provider.models:
                # 命中对话模型，明确不是生图模型
                return False
        return False

    def list_chat_models(self) -> list[str]:
        """汇总所有 Provider 的对话模型列表（保持顺序、去重）。"""
        result: list[str] = []
        seen: set[str] = set()
        for provider in self._providers():
            for item in provider.models:
                if item not in seen:
                    seen.add(item)
                    result.append(item)
        return result

    def list_image_models(self) -> list[str]:
        """汇总所有 Provider 的生图模型列表（保持顺序、去重）。"""
        result: list[str] = []
        seen: set[str] = set()
        for provider in self._providers():
            for item in provider.image_models:
                if item not in seen:
                    seen.add(item)
                    result.append(item)
        return result

    def list_all_models(self) -> list[str]:
        """汇总所有 Provider 的模型列表（对话 + 生图）。"""
        result: list[str] = []
        seen: set[str] = set()
        for model in (*self.list_chat_models(), *self.list_image_models()):
            if model not in seen:
                seen.add(model)
                result.append(model)
        return result

    def reload(self) -> None:
        """从 config 重新加载（config 本身热加载，这里触发一次读取即可）。"""
        config.reload_if_changed()


provider_service = ProviderService()
