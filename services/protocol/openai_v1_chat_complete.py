from __future__ import annotations

import time
import uuid
from typing import Any, Iterable, Iterator

from fastapi import HTTPException

from services.protocol.chat_completion_cache import cache_key, chat_completion_cache, normalize_text_messages
from services.protocol.conversation import (
    ConversationRequest,
    ImageOutput,
    collect_image_outputs,
    collect_text,
    count_message_image_tokens,
    count_message_text_tokens,
    count_text_tokens,
    encode_images,
    normalize_messages,
    save_image_bytes,
    stream_image_outputs_with_pool,
    stream_text_deltas,
    text_backend,
)
from services.protocol.reasoning import thinking_effort_from_body
from services.protocol.web_search_tool import (
    WEB_SEARCH_TOOL_TYPES,
    has_unsupported_tools,
    is_web_search_chat_request,
    run_web_search,
    search_query_from_messages,
    text_with_url_citations,
)
from services.provider_service import provider_service
from services.third_party_backend import ThirdPartyBackend, resolve_image_bytes
from utils.helper import build_chat_image_markdown_content, extract_chat_image, extract_chat_prompt, is_image_chat_request, parse_image_count
from utils.image_tokens import (
    chat_usage_from_image_usage,
    count_image_inputs_tokens,
    count_image_output_items_tokens,
    image_usage,
)

TOOL_UNAVAILABLE_SYSTEM_MESSAGE = (
    "This compatibility backend cannot execute local tools, shell commands, non-search tools, "
    "or file operations. Do not claim to have run tools or inspected external resources. "
    "If a user asks you to use a tool, say that tool execution is unavailable through this backend."
)


def completion_chunk(model: str, delta: dict[str, Any], finish_reason: str | None = None, completion_id: str = "", created: int | None = None) -> dict[str, Any]:
    return {
        "id": completion_id or f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion.chunk",
        "created": created or int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def completion_response(
    model: str,
    content: str,
    created: int | None = None,
    messages: list[dict[str, Any]] | None = None,
    annotations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    prompt_text_tokens = count_message_text_tokens(messages, model) if messages else 0
    prompt_image_tokens = count_message_image_tokens(messages, model) if messages else 0
    prompt_tokens = prompt_text_tokens + prompt_image_tokens
    completion_tokens = count_text_tokens(content, model) if messages else 0
    message = {"role": "assistant", "content": content}
    if annotations:
        message["annotations"] = annotations
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": created or int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens_details": {
                "text_tokens": prompt_text_tokens,
                "image_tokens": prompt_image_tokens,
                "cached_tokens": 0,
            },
            "completion_tokens_details": {
                "text_tokens": completion_tokens,
                "image_tokens": 0,
                "reasoning_tokens": 0,
            },
        },
    }


def _with_log_metadata(payload: dict[str, Any], account_email: str = "", conversation_id: str = "") -> dict[str, Any]:
    if account_email:
        payload["_account_email"] = account_email
    if conversation_id:
        payload["_conversation_id"] = conversation_id
    return payload


def _backend_account_email(backend: object) -> str:
    return str(getattr(backend, "account_email", "") or "").strip()


def stream_text_chat_completion(
    backend,
    messages: list[dict[str, Any]],
    model: str,
    thinking_effort: str = "",
) -> Iterator[dict[str, Any]]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    sent_role = False
    request = ConversationRequest(model=model, messages=messages, thinking_effort=thinking_effort)
    for delta_text in stream_text_deltas(backend, request):
        if not sent_role:
            sent_role = True
            yield _with_log_metadata(
                completion_chunk(model, {"role": "assistant", "content": delta_text}, None, completion_id, created),
                _backend_account_email(backend),
            )
        else:
            yield _with_log_metadata(
                completion_chunk(model, {"content": delta_text}, None, completion_id, created),
                _backend_account_email(backend),
            )
    if not sent_role:
        yield _with_log_metadata(
            completion_chunk(model, {"role": "assistant", "content": ""}, None, completion_id, created),
            _backend_account_email(backend),
        )
    yield _with_log_metadata(completion_chunk(model, {}, "stop", completion_id, created), _backend_account_email(backend))


def collect_chat_content(chunks: Iterable[dict[str, Any]]) -> str:
    parts: list[str] = []
    for chunk in chunks:
        choices = chunk.get("choices")
        first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
        content = str(delta.get("content") or "")
        if content:
            parts.append(content)
    return "".join(parts)


def chat_messages_from_body(body: dict[str, Any]) -> list[dict[str, Any]]:
    messages = body.get("messages")
    if isinstance(messages, list) and messages:
        return [message for message in messages if isinstance(message, dict)]
    prompt = str(body.get("prompt") or "").strip()
    if prompt:
        return [{"role": "user", "content": prompt}]
    raise HTTPException(status_code=400, detail={"error": "messages or prompt is required"})


def chat_image_args(body: dict[str, Any]) -> tuple[str, str, int, list[tuple[bytes, str, str]], str | None]:
    model = str(body.get("model") or "gpt-image-2").strip() or "gpt-image-2"
    prompt = extract_chat_prompt(body)
    if not prompt:
        raise HTTPException(status_code=400, detail={"error": "prompt is required"})
    images = [
        (data, f"image_{idx}.png", mime)
        for idx, (data, mime) in enumerate(extract_chat_image(body), start=1)
    ]
    base_url = str(body.get("base_url") or "").strip() or None
    return model, prompt, parse_image_count(body.get("n")), images, base_url


def text_chat_parts(body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    model = str(body.get("model") or "auto").strip() or "auto"
    messages = normalize_text_messages(normalize_messages(chat_messages_from_body(body)))
    if has_unsupported_tools(body, WEB_SEARCH_TOOL_TYPES):
        messages.insert(0, {"role": "system", "content": TOOL_UNAVAILABLE_SYSTEM_MESSAGE})
    return model, messages


def chat_completion_annotations(annotations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for item in annotations:
        if item.get("type") != "url_citation":
            continue
        output.append({
            "type": "url_citation",
            "url_citation": {
                "start_index": item.get("start_index", 0),
                "end_index": item.get("end_index", 0),
                "url": item.get("url", ""),
                "title": item.get("title", ""),
            },
        })
    return output


def web_search_chat_response(messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
    query = search_query_from_messages(messages)
    if not query:
        raise HTTPException(status_code=400, detail={"error": "messages or prompt is required for web search"})
    text, annotations = text_with_url_citations(run_web_search(query))
    return completion_response(
        model,
        text,
        messages=messages,
        annotations=chat_completion_annotations(annotations),
    )


def stream_web_search_chat_completion(messages: list[dict[str, Any]], model: str) -> Iterator[dict[str, Any]]:
    query = search_query_from_messages(messages)
    if not query:
        raise HTTPException(status_code=400, detail={"error": "messages or prompt is required for web search"})
    text, _annotations = text_with_url_citations(run_web_search(query))
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    yield completion_chunk(model, {"role": "assistant", "content": text}, None, completion_id, created)
    yield completion_chunk(model, {}, "stop", completion_id, created)


def image_result_content(result: dict[str, Any]) -> str:
    data = result.get("data")
    if isinstance(data, list) and data:
        return build_chat_image_markdown_content(result)
    return str(result.get("message") or "Image generation completed.")


def image_chat_response(body: dict[str, Any]) -> dict[str, Any]:
    model, prompt, n, images, base_url = chat_image_args(body)
    result = collect_image_outputs(stream_image_outputs_with_pool(ConversationRequest(
        prompt=prompt,
        model=model,
        n=n,
        response_format="b64_json",
        images=encode_images(images) or None,
        base_url=base_url,
        call_id=str(body.get("_call_id") or ""),
        trace_image_perf=bool(body.get("_trace_image_perf")),
    )))
    response = completion_response(model, image_result_content(result), int(result.get("created") or 0) or None)
    usage = image_usage(
        input_text_tokens=count_text_tokens(prompt, model),
        input_image_tokens=count_image_inputs_tokens(images, model),
        output_tokens=count_image_output_items_tokens(result.get("data")),
    )
    response["usage"] = chat_usage_from_image_usage(usage)
    _with_log_metadata(
        response,
        str(result.get("_account_email") or ""),
        str(result.get("_conversation_id") or ""),
    )
    return response


def image_chat_events(body: dict[str, Any]) -> Iterator[dict[str, Any]]:
    model, prompt, n, images, base_url = chat_image_args(body)
    image_outputs = stream_image_outputs_with_pool(ConversationRequest(
        prompt=prompt,
        model=model,
        n=n,
        response_format="b64_json",
        images=encode_images(images) or None,
        base_url=base_url,
        call_id=str(body.get("_call_id") or ""),
        trace_image_perf=bool(body.get("_trace_image_perf")),
    ))
    yield from stream_image_chat_completion(image_outputs, model)


def stream_image_chat_completion(image_outputs: Iterable[ImageOutput], model: str) -> Iterator[dict[str, Any]]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    sent_role = False
    sent_text = ""
    for output in image_outputs:
        content = ""
        if output.kind == "progress":
            content = output.text
            sent_text += content
        elif output.kind == "result":
            content = build_chat_image_markdown_content({"data": output.data})
        elif output.kind == "message":
            content = output.text[len(sent_text):] if output.text.startswith(sent_text) else output.text
        if not content:
            continue
        if not sent_role:
            sent_role = True
            yield _with_log_metadata(
                completion_chunk(model, {"role": "assistant", "content": content}, None, completion_id, created),
                output.account_email,
                output.conversation_id,
            )
        else:
            yield _with_log_metadata(
                completion_chunk(model, {"content": content}, None, completion_id, created),
                output.account_email,
                output.conversation_id,
            )
    if not sent_role:
        yield completion_chunk(model, {"role": "assistant", "content": ""}, None, completion_id, created)
    yield completion_chunk(model, {}, "stop", completion_id, created)


_PASSTHROUGH_PARAMS = (
    "temperature",
    "top_p",
    "max_tokens",
    "presence_penalty",
    "frequency_penalty",
    "stop",
    "reasoning_effort",
)


def _third_party_params(body: dict[str, Any]) -> dict[str, Any]:
    return {key: body[key] for key in _PASSTHROUGH_PARAMS if body.get(key) is not None}


def third_party_chat_response(provider, body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
    with ThirdPartyBackend(provider) as backend:
        result = backend.chat_completion(messages, model, **_third_party_params(body))
    if isinstance(result, dict):
        result.setdefault("model", model)
    return result


def third_party_chat_stream(provider, body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> Iterator[dict[str, Any]]:
    backend = ThirdPartyBackend(provider)
    try:
        for chunk in backend.chat_completion_stream(messages, model, **_third_party_params(body)):
            if isinstance(chunk, dict):
                chunk.setdefault("model", model)
            yield chunk
    finally:
        backend.close()


def _persist_provider_image_urls(urls: list[str], provider, base_url: str | None) -> list[dict[str, Any]]:
    """把第三方返回的图片来源（URL 或 data:image base64）落盘到本地图片库，返回本地 URL。

    下载/解码失败时：http URL 回退原始 URL；data URL 无法回退，则丢弃该项。
    """
    data: list[dict[str, Any]] = []
    for source in urls:
        image_bytes = resolve_image_bytes(source, getattr(provider, "proxy", "") or "")
        if image_bytes:
            try:
                data.append({"url": save_image_bytes(image_bytes, base_url)})
                continue
            except Exception:
                pass
        if source.startswith(("http://", "https://")):
            data.append({"url": source})
    return data


def _third_party_image_content(provider, body: dict[str, Any], model: str) -> str:
    prompt = extract_chat_prompt(body)
    if not prompt:
        raise HTTPException(status_code=400, detail={"error": "prompt is required"})
    n = parse_image_count(body.get("n"))
    with ThirdPartyBackend(provider) as backend:
        urls = backend.image_via_chat(prompt, model, n=n)
    if not urls:
        raise HTTPException(status_code=502, detail={"error": "上游未返回图片 URL"})
    base_url = str(body.get("base_url") or "").strip() or None
    data = _persist_provider_image_urls(urls, provider, base_url)
    return build_chat_image_markdown_content({"data": data})


def third_party_image_chat_response(provider, body: dict[str, Any], model: str) -> dict[str, Any]:
    return completion_response(model, _third_party_image_content(provider, body, model))


def third_party_image_chat_stream(provider, body: dict[str, Any], model: str) -> Iterator[dict[str, Any]]:
    content = _third_party_image_content(provider, body, model)
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    yield completion_chunk(model, {"role": "assistant", "content": content}, None, completion_id, created)
    yield completion_chunk(model, {}, "stop", completion_id, created)


def third_party_handle(provider, body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    model = str(body.get("model") or "").strip()
    stream = bool(body.get("stream"))
    if provider_service.is_provider_image_model(model):
        if stream:
            return third_party_image_chat_stream(provider, body, model)
        return third_party_image_chat_response(provider, body, model)
    messages = chat_messages_from_body(body)
    if stream:
        return third_party_chat_stream(provider, body, messages, model)
    return third_party_chat_response(provider, body, messages, model)


def text_completion_response(model: str, messages: list[dict[str, Any]], thinking_effort: str) -> dict[str, Any]:
    backend = text_backend()
    response = completion_response(
        model,
        collect_text(backend, ConversationRequest(model=model, messages=messages, thinking_effort=thinking_effort)),
        messages=messages,
    )
    return _with_log_metadata(response, _backend_account_email(backend))


def handle(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    provider = provider_service.resolve(str(body.get("model") or ""))
    if provider is not None:
        return third_party_handle(provider, body)
    if body.get("stream"):
        if is_image_chat_request(body):
            return image_chat_events(body)
        model, messages = text_chat_parts(body)
        if is_web_search_chat_request(body) and not has_unsupported_tools(body, WEB_SEARCH_TOOL_TYPES):
            return stream_web_search_chat_completion(messages, model)
        thinking_effort = thinking_effort_from_body(body)
        key = cache_key(body, messages, stream=True)
        return chat_completion_cache.get_or_compute_stream(
            key,
            lambda: stream_text_chat_completion(text_backend(), messages, model, thinking_effort),
        )
    if is_image_chat_request(body):
        return image_chat_response(body)
    model, messages = text_chat_parts(body)
    if is_web_search_chat_request(body) and not has_unsupported_tools(body, WEB_SEARCH_TOOL_TYPES):
        return web_search_chat_response(messages, model)
    thinking_effort = thinking_effort_from_body(body)
    key = cache_key(body, messages, stream=False)
    return chat_completion_cache.get_or_compute_response(
        key,
        lambda: text_completion_response(model, messages, thinking_effort),
    )
