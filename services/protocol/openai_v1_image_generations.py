from __future__ import annotations

import base64
import time
from typing import Any, Iterator

from fastapi import HTTPException

from services.protocol.conversation import (
    ConversationRequest,
    collect_image_outputs,
    count_text_tokens,
    save_image_bytes,
    stream_image_chunks,
    stream_image_outputs_with_pool,
)
from services.provider_service import provider_service
from services.third_party_backend import ThirdPartyBackend, resolve_image_bytes
from utils.image_tokens import count_image_output_items_tokens, image_usage


def _parse_size_to_aspect_and_resolution(size: str | None) -> tuple[str | None, str | None]:
    """将 size（如 1024x1024）转换为 aspect_ratio 和 resolution"""
    if not size or size == "auto":
        return None, None

    # 解析尺寸
    parts = size.lower().split('x')
    if len(parts) != 2:
        return None, None

    try:
        width = int(parts[0])
        height = int(parts[1])
    except (ValueError, TypeError):
        return None, None

    # 计算宽高比
    from math import gcd
    divisor = gcd(width, height)
    aspect_ratio = f"{width // divisor}:{height // divisor}"

    # 确定分辨率
    max_edge = max(width, height)
    if max_edge >= 1920:
        resolution = "2k"
    else:
        resolution = "1k"

    return aspect_ratio, resolution


def third_party_image_generation(provider, body: dict[str, Any]) -> dict[str, Any]:
    model = str(body.get("model") or "").strip()
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail={"error": "prompt is required"})
    try:
        n = max(1, min(4, int(body.get("n") or 1)))
    except (TypeError, ValueError):
        n = 1
    response_format = str(body.get("response_format") or "url").strip().lower()

    # 优先使用直接传递的 aspect_ratio 和 resolution，否则从 size 解析
    aspect_ratio = body.get("aspect_ratio")
    resolution = body.get("resolution")
    if not aspect_ratio or not resolution:
        size = body.get("size")
        parsed_aspect, parsed_resolution = _parse_size_to_aspect_and_resolution(size)
        if not aspect_ratio:
            aspect_ratio = parsed_aspect
        if not resolution:
            resolution = parsed_resolution

    base_url = str(body.get("base_url") or "").strip() or None

    with ThirdPartyBackend(provider) as backend:
        urls = backend.image_generation(
            prompt,
            model,
            n=n,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            response_format=response_format,
        )
    if not urls:
        raise HTTPException(status_code=502, detail={"error": "上游未返回图片 URL"})

    data: list[dict[str, Any]] = []
    for source in urls:
        image_bytes = resolve_image_bytes(source, getattr(provider, "proxy", "") or "")
        if image_bytes:
            # 下载/解码成功：落盘到本地图片库（图片管理可见），返回本地 URL / b64。
            entry: dict[str, Any] = {}
            try:
                entry["url"] = save_image_bytes(image_bytes, base_url)
            except Exception:
                if source.startswith(("http://", "https://")):
                    entry["url"] = source
            if response_format == "b64_json":
                entry["b64_json"] = base64.b64encode(image_bytes).decode("ascii")
            if entry:
                data.append(entry)
        elif source.startswith(("http://", "https://")):
            # 下载失败：回退上游 URL，保证仍有可用产物。
            data.append({"url": source})

    return {
        "created": int(time.time()),
        "data": data,
        "usage": image_usage(
            input_text_tokens=count_text_tokens(prompt, model),
            output_tokens=count_image_output_items_tokens(data),
        ),
    }


def handle(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    provider = provider_service.resolve(str(body.get("model") or ""))
    if provider is not None:
        return third_party_image_generation(provider, body)
    prompt = str(body.get("prompt") or "")
    model = str(body.get("model") or "gpt-image-2")
    n = int(body.get("n") or 1)
    size = body.get("size")
    quality = str(body.get("quality") or "auto")
    response_format = str(body.get("response_format") or "b64_json")
    base_url = str(body.get("base_url") or "") or None
    progress_callback = body.get("progress_callback")
    outputs = stream_image_outputs_with_pool(ConversationRequest(
        prompt=prompt,
        model=model,
        n=n,
        size=size,
        quality=quality,
        response_format=response_format,
        base_url=base_url,
        message_as_error=True,
        progress_callback=progress_callback,
        call_id=str(body.get("_call_id") or ""),
        trace_image_perf=bool(body.get("_trace_image_perf")),
    ))
    if body.get("stream"):
        input_text_tokens = count_text_tokens(prompt, model)
        return stream_image_chunks(
            outputs,
            event_prefix="image_generation",
            partial_images=body.get("partial_images"),
            usage_builder=lambda data: image_usage(
                input_text_tokens=input_text_tokens,
                output_tokens=count_image_output_items_tokens(data, size, quality),
            ),
        )
    result = collect_image_outputs(outputs)
    result["usage"] = image_usage(
        input_text_tokens=count_text_tokens(prompt, model),
        output_tokens=count_image_output_items_tokens(result.get("data"), size, quality),
    )
    return result
