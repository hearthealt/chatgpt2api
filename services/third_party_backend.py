from __future__ import annotations

import base64
import json
import re
import time
from typing import Any, Iterator

from curl_cffi import requests

from services.provider_service import Provider
from services.proxy_service import proxy_settings
from utils.helper import UpstreamHTTPError, ensure_ok, iter_sse_payloads
from utils.log import logger

# 上游偶发 5xx（server_error）时的重试配置：对幂等的对话/生图请求做退避重试。
_MAX_RETRIES = 3
_RETRY_BACKOFF_SECS = (1.0, 2.5, 5.0)
_RETRYABLE_STATUS = {500, 502, 503, 504, 520, 521, 522, 524}

# 从 chat completion 的 content 中提取图片来源。覆盖 Grok 生图的多种返回格式：
#   1) URL(Grok 原生)   https://assets.grok.com/.../image.jpg
#   2) URL(本地代理)     https://grok2api.kunstone.com/v1/files/image?id=xxx （无图片后缀）
#   3) Markdown(原生)    ![image](https://assets.grok.com/...)
#   4) Markdown(本地代理) ![image](https://.../v1/files/image?id=xxx)
#   5) Base64(内嵌)      data:image/jpeg;base64,....（可裸出现或包在 markdown 里）
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*(<?)([^)\s>]+)\1[^)]*\)")
DATA_URL_RE = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+")
BARE_URL_RE = re.compile(r"https?://[^\s\"'()<>\[\]{}]+")


def extract_image_sources(content: str) -> list[str]:
    """从 content 中提取所有图片来源（http/https URL 或 data:image base64），去重保序。"""
    text = str(content or "")
    if not text:
        return []
    sources: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        item = str(value or "").strip().rstrip(").,;")
        if item and item not in seen:
            seen.add(item)
            sources.append(item)

    # 1) markdown 图片语法里的 URL / data URL（优先，最明确）
    for _bracket, url in MARKDOWN_IMAGE_RE.findall(text):
        _add(url)
    # 2) 裸 data:image base64
    for match in DATA_URL_RE.findall(text):
        _add(re.sub(r"\s+", "", match))
    # 3) 裸 http(s) URL（生图 content 基本只含图片链接，放宽匹配以兼容无后缀的代理直链）
    for match in BARE_URL_RE.findall(text):
        _add(match)
    return sources


# 向后兼容旧名字。
def extract_image_urls_from_content(content: str) -> list[str]:
    return extract_image_sources(content)


_IMAGE_DOWNLOAD_TIMEOUT_SECS = 30


def _decode_data_url(source: str) -> bytes | None:
    """解析 data:image/...;base64,xxx，返回图片字节。"""
    try:
        header, _, encoded = source.partition(",")
        if "base64" not in header.lower() or not encoded:
            return None
        data = base64.b64decode(re.sub(r"\s+", "", encoded), validate=False)
        return data or None
    except Exception as exc:
        logger.warning({"event": "third_party_data_url_decode_failed", "error": str(exc)})
        return None


def download_image(url: str, proxy: str = "") -> bytes | None:
    """下载图片 URL 的二进制内容，失败返回 None（调用方回退到原 URL）。"""
    target = str(url or "").strip()
    if not target.startswith(("http://", "https://")):
        return None
    try:
        response = requests.get(
            target,
            headers={"Accept": "image/*,*/*;q=0.8", "User-Agent": "chatgpt2api third-party image fetcher"},
            timeout=_IMAGE_DOWNLOAD_TIMEOUT_SECS,
            allow_redirects=True,
            **proxy_settings.build_session_kwargs(proxy=proxy, upstream=True),
        )
    except Exception as exc:
        logger.warning({"event": "third_party_image_download_failed", "url": target[:200], "error": str(exc)})
        return None
    if not 200 <= response.status_code < 300:
        logger.warning({"event": "third_party_image_download_failed", "url": target[:200], "status": response.status_code})
        return None
    content = response.content
    return content if content else None


def resolve_image_bytes(source: str, proxy: str = "") -> bytes | None:
    """统一解析图片来源为字节：data:image base64 直接解码，http(s) URL 下载。"""
    item = str(source or "").strip()
    if item.startswith("data:image/"):
        return _decode_data_url(item)
    return download_image(item, proxy)


class ThirdPartyBackend:
    """第三方 OpenAI 兼容 API 客户端（对话 + 生图）。

    生图走 chat completions（第三方约定），返回内容里包含图片 URL。
    """

    def __init__(self, provider: Provider) -> None:
        self.provider = provider
        self.base_url = provider.base_url.rstrip("/")
        self.timeout = max(1, int(provider.timeout_secs or 120))
        session_kwargs = proxy_settings.build_session_kwargs(
            proxy=provider.proxy,
            upstream=True,
        )
        self.session = requests.Session(**session_kwargs)
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        if provider.api_key:
            self.session.headers["Authorization"] = f"Bearer {provider.api_key}"

    def __enter__(self) -> "ThirdPartyBackend":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass

    def _endpoint(self, path: str) -> str:
        return f"{self.base_url}/v1/{path.lstrip('/')}"

    def _payload(self, messages: list[dict[str, Any]], model: str, stream: bool, **kwargs: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": stream}
        # 透传 OpenAI 兼容的可选参数。
        for key in ("temperature", "top_p", "max_tokens", "presence_penalty", "frequency_penalty", "stop", "n", "reasoning_effort"):
            value = kwargs.get(key)
            if value is not None:
                payload[key] = value
        return payload

    def _post_with_retry(self, context: str, payload: dict[str, Any], *, stream: bool):
        """POST 到 chat/completions，对 5xx 与网络异常退避重试。

        对话/生图都是幂等重试安全的（无副作用）。流式仅重试建连阶段，
        一旦开始产出 SSE 就不再重试。
        """
        url = self._endpoint("chat/completions")
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                response = self.session.post(url, json=payload, timeout=self.timeout, stream=stream)
            except Exception as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    logger.warning({
                        "event": "third_party_retry",
                        "provider": self.provider.name,
                        "reason": "network",
                        "error": str(exc),
                        "attempt": attempt + 1,
                    })
                    time.sleep(_RETRY_BACKOFF_SECS[min(attempt, len(_RETRY_BACKOFF_SECS) - 1)])
                    continue
                raise
            if response.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES - 1:
                logger.warning({
                    "event": "third_party_retry",
                    "provider": self.provider.name,
                    "reason": "upstream_5xx",
                    "status": response.status_code,
                    "attempt": attempt + 1,
                })
                try:
                    response.close()
                except Exception:
                    pass
                time.sleep(_RETRY_BACKOFF_SECS[min(attempt, len(_RETRY_BACKOFF_SECS) - 1)])
                continue
            ensure_ok(response, context)
            return response
        # 理论到不了这里；兜底重新抛最后一次网络异常。
        if last_exc is not None:
            raise last_exc
        raise UpstreamHTTPError(context, 500, "retry exhausted")

    def chat_completion(self, messages: list[dict[str, Any]], model: str, **kwargs: Any) -> dict[str, Any]:
        """调用 /v1/chat/completions（非流式），返回完整响应 dict。"""
        response = self._post_with_retry(
            f"third_party[{self.provider.name}] chat_completion",
            self._payload(messages, model, stream=False, **kwargs),
            stream=False,
        )
        return response.json()

    def chat_completion_stream(self, messages: list[dict[str, Any]], model: str, **kwargs: Any) -> Iterator[dict[str, Any]]:
        """调用 /v1/chat/completions（stream=true），yield SSE chunk dict。"""
        response = self._post_with_retry(
            f"third_party[{self.provider.name}] chat_completion_stream",
            self._payload(messages, model, stream=True, **kwargs),
            stream=True,
        )
        for payload in iter_sse_payloads(response, max_duration_secs=float(self.timeout)):
            if payload == "[DONE]":
                break
            try:
                yield json.loads(payload)
            except (TypeError, ValueError):
                logger.warning({
                    "event": "third_party_stream_parse_error",
                    "provider": self.provider.name,
                    "payload": payload[:200],
                })
                continue

    def image_via_chat(self, prompt: str, model: str, n: int = 1, **kwargs: Any) -> list[str]:
        """用生图模型调用 /v1/chat/completions，解析返回的图片来源（URL 或 data:image base64）。"""
        messages = [{"role": "user", "content": prompt}]
        result = self.chat_completion(messages, model, **kwargs)
        content = _first_message_content(result)
        urls = extract_image_sources(content)
        if n and n > 1 and len(urls) < n:
            # 有的上游一次只返回一张，尝试重复调用补足。
            for _ in range(n - len(urls)):
                extra = self.chat_completion(messages, model, **kwargs)
                for url in extract_image_sources(_first_message_content(extra)):
                    if url not in urls:
                        urls.append(url)
                if len(urls) >= n:
                    break
        return urls[: n or 1] if n else urls


def _first_message_content(result: dict[str, Any]) -> str:
    choices = result.get("choices") if isinstance(result, dict) else None
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    # content 可能是 OpenAI 的分段结构。
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = str(item.get("text") or "")
                if text:
                    parts.append(text)
                image_url = item.get("image_url")
                if isinstance(image_url, dict):
                    url = str(image_url.get("url") or "")
                    if url:
                        parts.append(url)
        return "\n".join(parts)
    return ""
