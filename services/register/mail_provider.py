from __future__ import annotations

import hashlib
import random
import re
import string
import time
import urllib.parse
from datetime import datetime, timezone
from email import message_from_string, policy
from email.utils import parsedate_to_datetime
from threading import Lock
from typing import Any, Callable, TypeVar

from curl_cffi import requests

from services.proxy_service import proxy_settings


ResultT = TypeVar("ResultT")
domain_lock = Lock()
provider_lock = Lock()
domain_index = 0
provider_index = 0


def _config(mail_config: dict) -> dict:
    return {
        "request_timeout": float(mail_config.get("request_timeout") or 30),
        "wait_timeout": float(mail_config.get("wait_timeout") or 30),
        "wait_interval": float(mail_config.get("wait_interval") or 2),
        "user_agent": str(mail_config.get("user_agent") or "Mozilla/5.0"),
        "proxy": str(mail_config.get("proxy") or "").strip(),
    }


def _random_mailbox_name() -> str:
    return (
        f"{''.join(random.choices(string.ascii_lowercase, k=5))}"
        f"{''.join(random.choices(string.digits, k=random.randint(1, 3)))}"
        f"{''.join(random.choices(string.ascii_lowercase, k=random.randint(1, 3)))}"
    )


def _random_subdomain_label() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=random.randint(4, 10)))


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _next_domain(domains: list[str]) -> str:
    global domain_index
    domains = [str(item).strip() for item in domains if str(item).strip()]
    if not domains:
        raise RuntimeError("mail.domain 不能为空")
    if len(domains) == 1:
        return domains[0]
    with domain_lock:
        value = domains[domain_index % len(domains)]
        domain_index = (domain_index + 1) % len(domains)
        return value


def _create_session(conf: dict):
    proxy = str(conf.get("proxy") or "").strip()
    kwargs = proxy_settings.build_session_kwargs(
        proxy=proxy,
        upstream=True,
        impersonate="chrome",
        verify=False,
    )
    return requests.Session(**kwargs)


def _parse_received_at(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        date = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
        return date if date.tzinfo else date.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    try:
        date = parsedate_to_datetime(text)
        return date if date.tzinfo else date.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _extract_content(data: dict[str, Any]) -> tuple[str, str]:
    text_content = str(data.get("text_content") or data.get("text") or data.get("body") or data.get("content") or "")
    html_content = str(data.get("html_content") or data.get("html") or data.get("html_body") or data.get("body_html") or "")
    if text_content or html_content:
        return text_content, html_content
    raw = data.get("raw")
    if not isinstance(raw, str) or not raw.strip():
        return "", ""
    try:
        parsed = message_from_string(raw, policy=policy.default)
    except Exception:
        return raw, ""
    plain: list[str] = []
    html: list[str] = []
    for part in parsed.walk() if parsed.is_multipart() else [parsed]:
        if part.get_content_maintype() == "multipart":
            continue
        try:
            payload = part.get_content()
        except Exception:
            payload = ""
        if not payload:
            continue
        if part.get_content_type() == "text/html":
            html.append(str(payload))
        else:
            plain.append(str(payload))
    return "\n".join(plain).strip(), "\n".join(html).strip()


def _extract_text_candidates(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for key in ("address", "email", "name", "value"):
            if value.get(key):
                out.extend(_extract_text_candidates(value.get(key)))
        return out
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_extract_text_candidates(item))
        return out
    return []


def _message_matches_email(data: dict[str, Any], email: str) -> bool:
    target = str(email or "").strip().lower()
    candidates: list[str] = []
    for key in (
        "to",
        "toEmail",
        "mailTo",
        "receiver",
        "receivers",
        "address",
        "email",
        "envelope_to",
        "delivered_to",
        "x_forwarded_to",
        "x_original_to",
    ):
        if key in data:
            candidates.extend(_extract_text_candidates(data.get(key)))
    return not target or not candidates or any(target in str(item).strip().lower() for item in candidates if str(item).strip())


def _extract_code(message: dict[str, Any]) -> str | None:
    content = f"{message.get('subject', '')}\n{message.get('text_content', '')}\n{message.get('html_content', '')}".strip()
    if not content:
        return None
    match = re.search(r"background-color:\s*#F3F3F3[^>]*>[\s\S]*?(\d{6})[\s\S]*?</p>", content, re.I)
    if match:
        return match.group(1)
    match = re.search(r"(?:Verification code|code is|代码为|验证码)[:\s]*(\d{6})", content, re.I)
    if match and match.group(1) != "177010":
        return match.group(1)
    for code in re.findall(r">\s*(\d{6})\s*<|(?<![#&])\b(\d{6})\b", content):
        value = code[0] or code[1]
        if value and value != "177010":
            return value
    return None


def _message_tracking_ref(message: dict[str, Any]) -> str:
    provider = str(message.get("provider") or "").strip()
    mailbox = str(message.get("mailbox") or "").strip()
    message_id = str(message.get("message_id") or "").strip()
    if message_id:
        return f"id:{provider}:{mailbox}:{message_id}"
    received_at = message.get("received_at")
    received_value = received_at.isoformat() if isinstance(received_at, datetime) else str(received_at or "")
    content = "\n".join(str(message.get(key) or "") for key in ("subject", "sender", "text_content", "html_content"))
    digest = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
    return f"content:{provider}:{mailbox}:{received_value}:{digest}"


def _message_before_code_boundary(mailbox: dict[str, Any], message: dict[str, Any]) -> bool:
    boundary = mailbox.get("_code_not_before")
    received_at = message.get("received_at")
    if not isinstance(boundary, datetime) or not isinstance(received_at, datetime):
        return False
    if not received_at.tzinfo:
        received_at = received_at.replace(tzinfo=timezone.utc)
    return received_at < boundary


class BaseMailProvider:
    name = "unknown"
    is_paid = False

    def __init__(self, conf: dict, provider_ref: str = ""):
        self.conf = conf
        self.provider_ref = provider_ref

    def wait_for(self, mailbox: dict[str, Any], on_message: Callable[[dict[str, Any]], ResultT | None]) -> ResultT | None:
        deadline = time.monotonic() + self.conf["wait_timeout"]
        while time.monotonic() < deadline:
            message = self.fetch_latest_message(mailbox)
            if message:
                result = on_message(message)
                if result is not None:
                    return result
            time.sleep(max(0.2, self.conf["wait_interval"]))
        return None

    def wait_for_code(self, mailbox: dict[str, Any]) -> str | None:
        seen_value = mailbox.setdefault("_seen_code_message_refs", [])
        if not isinstance(seen_value, list):
            seen_value = []
            mailbox["_seen_code_message_refs"] = seen_value
        seen_refs = {str(item) for item in seen_value}

        def extract_unseen_code(message: dict[str, Any]) -> str | None:
            if _message_before_code_boundary(mailbox, message):
                return None
            ref = _message_tracking_ref(message)
            if ref in seen_refs:
                return None
            code = _extract_code(message)
            if code:
                seen_value.append(ref)
                seen_refs.add(ref)
            return code

        return self.wait_for(mailbox, extract_unseen_code)

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class CloudflareTempMailProvider(BaseMailProvider):
    name = "cloudflare_temp_email"

    def __init__(self, entry: dict, conf: dict):
        super().__init__(conf, str(entry.get("provider_ref") or ""))
        self.api_base = str(entry.get("api_base") or "").strip().rstrip("/")
        self.admin_password = str(entry.get("admin_password") or "").strip()
        self.domain = _normalize_string_list(entry.get("domain"))
        if not self.api_base or not self.admin_password:
            raise RuntimeError("CloudflareTempMail 需要配置 api_base 和 admin_password")
        self.session = _create_session(conf)

    def _request(
        self,
        method: str,
        path: str,
        headers: dict | None = None,
        params: dict | None = None,
        payload: dict | None = None,
        expected: tuple[int, ...] = (200,),
    ):
        resp = self.session.request(
            method.upper(),
            f"{self.api_base}{path}",
            headers={"Content-Type": "application/json", "User-Agent": self.conf["user_agent"], **(headers or {})},
            params=params,
            json=payload,
            timeout=self.conf["request_timeout"],
            verify=False,
        )
        if resp.status_code not in expected:
            raise RuntimeError(f"CloudflareTempMail 请求失败: {method} {path}, HTTP {resp.status_code}, body={resp.text[:300]}")
        return {} if resp.status_code == 204 else resp.json()

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        data = self._request(
            "POST",
            "/admin/new_address",
            headers={"x-admin-auth": self.admin_password},
            payload={"enablePrefix": True, "name": username or _random_mailbox_name(), "domain": _next_domain(self.domain)},
        )
        address = str(data.get("address") or "").strip()
        token = str(data.get("jwt") or "").strip()
        if not address or not token:
            raise RuntimeError("CloudflareTempMail 缺少 address 或 jwt")
        return {"provider": self.name, "provider_ref": self.provider_ref, "address": address, "token": token}

    def get_existing_mailbox(self, email: str) -> dict[str, Any]:
        data = self._request(
            "POST",
            "/admin/get_address",
            headers={"x-admin-auth": self.admin_password},
            payload={"address": email},
        )
        address = str(data.get("address") or "").strip()
        token = str(data.get("jwt") or "").strip()
        if not address or not token:
            raise RuntimeError(f"CloudflareTempMail 无法获取已有邮箱 {email} 的 JWT")
        return {"provider": self.name, "provider_ref": self.provider_ref, "address": address, "token": token}

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        data = self._request(
            "GET",
            "/api/mails",
            headers={"Authorization": f"Bearer {mailbox['token']}"},
            params={"limit": 10, "offset": 0},
        )
        raw = list(data.get("results") or []) if isinstance(data, dict) else data if isinstance(data, list) else []
        messages = [item for item in raw if isinstance(item, dict) and _message_matches_email(item, str(mailbox.get("address") or ""))]
        if not messages:
            return None
        item = messages[0]
        text_content, html_content = _extract_content(item)
        sender = item.get("from") or item.get("sender") or ""
        if isinstance(sender, dict):
            sender = sender.get("address") or sender.get("email") or sender.get("name") or ""
        return {
            "provider": self.name,
            "mailbox": mailbox["address"],
            "message_id": str(item.get("id") or item.get("_id") or ""),
            "subject": str(item.get("subject") or ""),
            "sender": str(sender),
            "text_content": text_content,
            "html_content": html_content,
            "received_at": _parse_received_at(item.get("createdAt") or item.get("created_at") or item.get("receivedAt") or item.get("date") or item.get("timestamp")),
            "raw": item,
        }

    def close(self) -> None:
        self.session.close()


class TempMailLolProvider(BaseMailProvider):
    name = "tempmail_lol"

    def __init__(self, entry: dict, conf: dict):
        super().__init__(conf, str(entry.get("provider_ref") or ""))
        self.api_key = str(entry.get("api_key") or "").strip()
        self.domain = _normalize_string_list(entry.get("domain"))
        self.session = _create_session(conf)
        self.session.headers.update({"User-Agent": conf["user_agent"], "Accept": "application/json", "Content-Type": "application/json"})
        if self.api_key:
            self.session.headers["Authorization"] = f"Bearer {self.api_key}"

    @staticmethod
    def _resolve_domain(domain: str) -> tuple[str, bool]:
        text = str(domain or "").strip().lower()
        if text.startswith("*.") and len(text) > 2:
            return f"{_random_subdomain_label()}.{text[2:]}", True
        return text, False

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        payload: dict | None = None,
        expected: tuple[int, ...] = (200,),
    ):
        resp = self.session.request(
            method.upper(),
            f"https://api.tempmail.lol/v2{path}",
            params=params,
            json=payload,
            timeout=self.conf["request_timeout"],
            verify=False,
        )
        if resp.status_code not in expected:
            raise RuntimeError(f"TempMail.lol 请求失败: {method} {path}, HTTP {resp.status_code}, body={resp.text[:300]}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"TempMail.lol {method} {path} 返回结构不是对象")
        return data

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.domain:
            domain, force_random_prefix = self._resolve_domain(random.choice(self.domain))
            payload["domain"] = domain
            if force_random_prefix:
                payload["prefix"] = _random_mailbox_name()
        if username and "prefix" not in payload:
            payload["prefix"] = username
        data = self._request("POST", "/inbox/create", payload=payload, expected=(200, 201))
        address = str(data.get("address") or "").strip()
        token = str(data.get("token") or "").strip()
        if not address or not token:
            raise RuntimeError("TempMail.lol 缺少 address 或 token")
        return {"provider": self.name, "provider_ref": self.provider_ref, "address": address, "token": token}

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        data = self._request("GET", "/inbox", params={"token": mailbox["token"]})
        items = data.get("emails") or data.get("messages") or []
        messages = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
        if not messages:
            return None
        item = max(
            messages,
            key=lambda value: (
                (_parse_received_at(value.get("created_at") or value.get("createdAt") or value.get("date") or value.get("received_at") or value.get("timestamp")) or datetime.fromtimestamp(0, tz=timezone.utc)).timestamp(),
                str(value.get("id") or value.get("token") or ""),
            ),
        )
        text_content, html_content = _extract_content(item)
        return {
            "provider": self.name,
            "mailbox": mailbox["address"],
            "message_id": str(item.get("id") or item.get("token") or ""),
            "subject": str(item.get("subject") or ""),
            "sender": str(item.get("from") or item.get("from_address") or ""),
            "text_content": text_content,
            "html_content": html_content,
            "received_at": _parse_received_at(item.get("created_at") or item.get("createdAt") or item.get("date") or item.get("received_at") or item.get("timestamp")),
            "raw": item,
        }

    def close(self) -> None:
        self.session.close()


class LuckMailProvider(BaseMailProvider):
    """LuckMail 付费购买邮箱服务。"""

    name = "luckmail"
    is_paid = True

    def __init__(self, entry: dict, conf: dict):
        super().__init__(conf, str(entry.get("provider_ref") or ""))
        self.api_base = str(entry.get("api_base") or "https://mails.luckyous.com/api/v1/openapi").strip().rstrip("/")
        self.api_key = str(entry.get("api_key") or "").strip()
        self.email_type = str(entry.get("email_type") or "").strip()
        self.mail_domain = str(entry.get("mail_domain") or "").strip().lstrip("@")
        self.max_retry = max(1, int(entry.get("max_retry") or 20))
        if not self.api_key:
            raise RuntimeError("LuckMail 需要配置 api_key")
        self.session = _create_session(conf)
        self.session.headers.update({
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
            "User-Agent": conf["user_agent"],
        })

    def close(self) -> None:
        self.session.close()

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.api_base}/{path.lstrip('/')}"
        try:
            method_upper = method.upper()
            if method_upper == "GET":
                resp = self.session.get(url, timeout=self.conf["request_timeout"], verify=False, **kwargs)
            elif method_upper == "POST":
                resp = self.session.post(url, timeout=self.conf["request_timeout"], verify=False, **kwargs)
            else:
                raise RuntimeError(f"不支持的请求方法: {method}")
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"LuckMail API 错误 (code={data.get('code')}): {data.get('message', '未知错误')}")
            return data.get("data", {})
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"LuckMail API 请求失败: {exc}") from exc

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        last_error = ""
        for _ in range(1, self.max_retry + 1):
            try:
                data = self._request(
                    "POST",
                    "email/purchase",
                    json={
                        "email_type": self.email_type,
                        "project_code": "openai",
                        "domain": self.mail_domain,
                        "quantity": 1,
                        "variant_mode": "",
                    },
                )
                purchases = data.get("purchases", [])
                if not purchases:
                    raise RuntimeError("LuckMail 购买邮箱失败：未返回邮箱数据")
                purchase = purchases[0]
                email_address = str(purchase.get("email_address") or "").strip()
                token = str(purchase.get("token") or "").strip()
                if not email_address or not token:
                    raise RuntimeError("LuckMail 返回数据不完整")
                alive_data = self._request("GET", f"email/token/{token}/alive")
                if not alive_data.get("alive"):
                    status = alive_data.get("status", "unknown")
                    message = alive_data.get("message", "邮箱不可用")
                    last_error = f"邮箱 {email_address} 测活失败 ({status}): {message}"
                    continue
                return {
                    "provider": self.name,
                    "provider_ref": self.provider_ref,
                    "address": email_address,
                    "token": token,
                    "purchase_id": purchase.get("id"),
                    "label": f"LuckMail ({email_address})",
                }
            except RuntimeError as exc:
                last_error = str(exc)
                if "测活失败" not in last_error:
                    raise
        raise RuntimeError(f"LuckMail 购买邮箱失败（尝试了 {self.max_retry} 次）: {last_error}")

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        token = mailbox.get("token")
        if not token:
            raise RuntimeError("邮箱缺少 token")
        try:
            data = self._request("GET", f"email/token/{token}/code")
            if not data.get("has_new_mail"):
                return None
            mail = data.get("mail", {})
            if not mail:
                return None
            return {
                "provider": self.name,
                "mailbox": mailbox["address"],
                "message_id": str(mail.get("message_id") or ""),
                "subject": str(mail.get("subject") or ""),
                "sender": str(mail.get("from") or ""),
                "to": [mailbox["address"]],
                "text_content": str(mail.get("body_text") or ""),
                "html_content": str(mail.get("body_html") or ""),
                "received_at": _parse_received_at(mail.get("received_at")),
                "verification_code": data.get("verification_code"),
                "raw": mail,
            }
        except Exception:
            return None

    def wait_for_code(self, mailbox: dict[str, Any]) -> str | None:
        deadline = time.monotonic() + self.conf["wait_timeout"]
        while time.monotonic() < deadline:
            message = self.fetch_latest_message(mailbox)
            if message and message.get("verification_code"):
                return str(message.get("verification_code"))
            time.sleep(max(0.2, self.conf["wait_interval"]))
        return None


class Hotmail007Provider(BaseMailProvider):
    """Hotmail007 付费购买 Microsoft 邮箱服务。"""

    name = "hotmail007"
    is_paid = True

    def __init__(self, entry: dict, conf: dict):
        super().__init__(conf, str(entry.get("provider_ref") or ""))
        self.api_base = str(entry.get("api_base") or "https://gapi.hotmail007.com").strip().rstrip("/")
        self.api_key = str(entry.get("api_key") or "").strip()
        self.product_id = self._parse_product_id(entry.get("product_id"))
        if not self.api_key:
            raise RuntimeError("Hotmail007 需要配置 api_key")
        self.session = _create_session(conf)

    def close(self) -> None:
        self.session.close()

    def _api_get(self, path: str, **params) -> dict:
        url = f"{self.api_base}/{path.lstrip('/')}"
        params["clientKey"] = self.api_key
        qs = "&".join(
            f"{key}={urllib.parse.quote(str(value))}"
            for key, value in params.items()
            if value is not None and value != ""
        )
        if qs:
            url = f"{url}?{qs}"
        try:
            resp = self.session.get(url, timeout=self.conf["request_timeout"], verify=False)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            if not data.get("success") or data.get("code") != 0:
                raise RuntimeError(f"Hotmail007 API 错误: {data.get('message', '未知错误')}")
            return data.get("data", {})
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Hotmail007 API 请求失败: {exc}") from exc

    @staticmethod
    def _parse_stock_count(value: Any) -> int:
        try:
            return max(0, int(value))
        except Exception:
            return 0

    @staticmethod
    def _parse_product_id(value: Any) -> int:
        text = str(value or "").strip()
        if not text:
            raise RuntimeError("Hotmail007 需要配置 product_id")
        try:
            product_id = int(text)
        except Exception as exc:
            raise RuntimeError("Hotmail007 product_id 必须是正整数") from exc
        if product_id <= 0:
            raise RuntimeError("Hotmail007 product_id 必须是正整数")
        return product_id

    def _stock_count(self) -> int:
        data = self._api_get("open/stock", productId=self.product_id)
        if isinstance(data, dict):
            if "stock" in data:
                return self._parse_stock_count(data.get("stock"))
            items = data.get("items") or data.get("list") or data.get("products")
            if isinstance(items, list):
                data = items
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                item_product_id = item.get("productId") or item.get("product_id") or item.get("id")
                if str(item_product_id or "").strip() == str(self.product_id):
                    return self._parse_stock_count(item.get("stock") or item.get("count") or item.get("quantity") or item.get("available"))
        raise RuntimeError(f"Hotmail007 库存查询未返回 product_id={self.product_id} 的库存")

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        stock = self._stock_count()
        if stock <= 0:
            raise RuntimeError(f"Hotmail007 product_id={self.product_id} 库存不足")
        data = self._api_get("open/buy", productId=self.product_id, quantity=1)
        accounts = data.get("accounts", [])
        if not accounts:
            raise RuntimeError("Hotmail007 购买邮箱失败：未返回账号数据")
        account_str = str(accounts[0])
        parts = account_str.split(":")
        if len(parts) < 4:
            raise RuntimeError(f"Hotmail007 返回的账号格式不正确: {account_str}")
        email_addr = parts[0].strip()
        password = parts[1].strip()
        client_id = parts[-1].strip()
        refresh_token = ":".join(parts[2:-1]).strip()
        if not email_addr or not refresh_token or not client_id:
            raise RuntimeError("Hotmail007 返回数据不完整")
        return {
            "provider": self.name,
            "provider_ref": self.provider_ref,
            "address": email_addr,
            "password": password,
            "client_id": client_id,
            "refresh_token": refresh_token,
            "label": f"Hotmail007 ({email_addr})",
        }

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        account_str = f"{mailbox['address']}:{mailbox.get('password', '')}:{mailbox['refresh_token']}:{mailbox['client_id']}"
        candidates = []
        for folder in ("inbox", "junkemail"):
            try:
                params = {"account": account_str, "folder": folder}
                code_not_before = mailbox.get("_code_not_before")
                if isinstance(code_not_before, datetime):
                    params["start_timestamp"] = int(code_not_before.timestamp())
                data = self._api_get("open/mail/latest", **params)
                if data and (data.get("subject") or data.get("text") or data.get("html")):
                    candidates.append(data)
            except Exception:
                continue
        if not candidates:
            return None
        candidates.sort(
            key=lambda item: _parse_received_at(item.get("receivedAt")) or datetime.fromtimestamp(0, tz=timezone.utc),
            reverse=True,
        )
        data = candidates[0]
        return {
            "provider": self.name,
            "mailbox": mailbox["address"],
            "message_id": "",
            "subject": str(data.get("subject") or ""),
            "sender": str(data.get("from") or ""),
            "to": [mailbox["address"]],
            "text_content": str(data.get("text") or ""),
            "html_content": str(data.get("html") or ""),
            "received_at": _parse_received_at(data.get("receivedAt")),
            "raw": data,
        }


class MSAccountManagerProvider(BaseMailProvider):
    """自建 Microsoft 账号池服务。"""

    name = "msaccount_manager"

    def __init__(self, entry: dict, conf: dict):
        super().__init__(conf, str(entry.get("provider_ref") or ""))
        self.api_base = str(entry.get("api_base") or "").strip().rstrip("/")
        self.api_key = str(entry.get("api_key") or "").strip()
        self.mail_mode = str(entry.get("mail_mode") or "imap").strip().lower()
        self.keyword = str(entry.get("keyword") or "outlook").strip()
        if not self.api_base or not self.api_key:
            raise RuntimeError("MSAccountManager 需要配置 api_base 和 api_key")
        self.session = _create_session(conf)
        self.session.headers.update({
            "x-mail-api-token": self.api_key,
            "Content-Type": "application/json",
            "User-Agent": conf["user_agent"],
        })

    def close(self) -> None:
        self.session.close()

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.api_base}/{path.lstrip('/')}"
        try:
            method_upper = method.upper()
            if method_upper == "GET":
                resp = self.session.get(url, timeout=self.conf["request_timeout"], verify=False, **kwargs)
            elif method_upper == "POST":
                resp = self.session.post(url, timeout=self.conf["request_timeout"], verify=False, **kwargs)
            elif method_upper == "PATCH":
                resp = self.session.patch(url, timeout=self.conf["request_timeout"], verify=False, **kwargs)
            else:
                raise RuntimeError(f"不支持的请求方法: {method}")
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            return resp.json()
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"MSAccountManager API 请求失败: {exc}") from exc

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        data = self._request("GET", "api/open/accounts", params={"keyword": self.keyword})
        items = data.get("items", [])
        if not items:
            raise RuntimeError(f"MSAccountManager 账号池为空（keyword={self.keyword}）")
        account = next((item for item in items if not item.get("remark")), items[0])
        account_id = account.get("id")
        email_addr = account.get("account") or account.get("email")
        if not email_addr:
            raise RuntimeError("MSAccountManager 返回的账号数据缺少邮箱地址")
        if account_id:
            try:
                self._request("PATCH", f"api/open/accounts/{account_id}/remark", json={"remark": "使用中"})
            except Exception:
                pass
        return {
            "provider": self.name,
            "provider_ref": self.provider_ref,
            "address": email_addr,
            "account_id": account_id,
            "mode": self.mail_mode,
            "_ms_api_base": self.api_base,
            "_ms_api_key": self.api_key,
            "label": f"MSAccount ({email_addr})",
        }

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        try:
            data = self._request(
                "POST",
                "api/open/messages",
                json={"account": mailbox["address"], "mode": mailbox.get("mode", self.mail_mode)},
            )
            messages = data.get("items") or data.get("messages") or []
            if not messages:
                return None
            mail = messages[0]
            if not mail:
                return None
            text_content, html_content = _extract_content(mail)
            sender = ""
            from_field = mail.get("from")
            if isinstance(from_field, dict):
                email_addr = from_field.get("emailAddress")
                sender = str(email_addr.get("address") or "") if isinstance(email_addr, dict) else str(from_field.get("address") or "")
            elif from_field:
                sender = str(from_field)
            return {
                "provider": self.name,
                "mailbox": mailbox["address"],
                "message_id": str(mail.get("id") or mail.get("message_id") or ""),
                "subject": str(mail.get("subject") or ""),
                "sender": sender,
                "to": [mailbox["address"]],
                "text_content": text_content,
                "html_content": html_content,
                "received_at": _parse_received_at(mail.get("receivedDateTime") or mail.get("date") or mail.get("received_at")),
                "raw": mail,
            }
        except Exception:
            return None


PROVIDER_CLASSES: dict[str, type[BaseMailProvider]] = {
    CloudflareTempMailProvider.name: CloudflareTempMailProvider,
    TempMailLolProvider.name: TempMailLolProvider,
    LuckMailProvider.name: LuckMailProvider,
    Hotmail007Provider.name: Hotmail007Provider,
    MSAccountManagerProvider.name: MSAccountManagerProvider,
}


def _entries(mail_config: dict) -> list[dict]:
    result: list[dict] = []
    for item in mail_config.get("providers") or []:
        if not isinstance(item, dict):
            continue
        idx = len(result) + 1
        provider_type = str(item.get("type") or "").strip()
        stable_id = str(item.get("id") or item.get("provider_id") or "").strip()
        provider_ref = f"{provider_type}:{stable_id}" if stable_id else f"{provider_type}#{idx}"
        result.append({**item, "type": provider_type, "provider_ref": provider_ref, "label": str(item.get("label") or f"{provider_type}#{idx}")})
    return result


def _enabled_entries(mail_config: dict) -> list[dict]:
    items = [item for item in _entries(mail_config) if item.get("enable")]
    if not items:
        raise RuntimeError("mail.providers 没有启用的 provider")
    return items


def _next_entry(mail_config: dict) -> dict:
    global provider_index
    items = _enabled_entries(mail_config)
    if len(items) == 1:
        return dict(items[0])
    with provider_lock:
        value = dict(items[provider_index % len(items)])
        provider_index = (provider_index + 1) % len(items)
        return value


def _create_provider(mail_config: dict, provider: str = "", provider_ref: str = "") -> BaseMailProvider:
    entry = next((dict(item) for item in _entries(mail_config) if provider_ref and item["provider_ref"] == provider_ref), None)
    entry = entry or next((dict(item) for item in _enabled_entries(mail_config) if provider and item["type"] == provider), None) or _next_entry(mail_config)
    provider_type = str(entry.get("type") or "").strip()
    provider_class = PROVIDER_CLASSES.get(provider_type)
    if not provider_class:
        raise RuntimeError(f"不支持的 mail.provider: {provider_type}")
    return provider_class(entry, _config(mail_config))


def create_mailbox(mail_config: dict, username: str | None = None) -> dict:
    provider = _create_provider(mail_config)
    try:
        mailbox = provider.create_mailbox(username)
        mailbox["_code_not_before"] = datetime.now(timezone.utc)
        return mailbox
    finally:
        provider.close()


def wait_for_code(mail_config: dict, mailbox: dict) -> str | None:
    provider = _create_provider(mail_config, str(mailbox.get("provider") or ""), str(mailbox.get("provider_ref") or ""))
    try:
        return provider.wait_for_code(mailbox)
    finally:
        provider.close()


def mark_mailbox_result(mailbox: dict, *, success: bool, error: Exception | str | None = None) -> None:
    """注册流程结束后更新邮箱池状态。"""
    if str(mailbox.get("provider") or "") != MSAccountManagerProvider.name:
        return
    account_id = mailbox.get("account_id")
    api_base = str(mailbox.get("_ms_api_base") or "").strip().rstrip("/")
    api_key = str(mailbox.get("_ms_api_key") or "").strip()
    if not account_id or not api_base or not api_key:
        return
    try:
        import requests as py_requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        session = py_requests.Session()
        retry = Retry(total=2, backoff_factor=0.1)
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.mount("http://", HTTPAdapter(max_retries=retry))
        remark = "已使用" if success else f"注册失败: {str(error)[:50]}" if error else "注册失败"
        session.patch(
            f"{api_base}/api/open/accounts/{account_id}/remark",
            json={"remark": remark},
            headers={"x-mail-api-token": api_key, "Content-Type": "application/json"},
            timeout=10,
            verify=False,
        )
        session.close()
    except Exception:
        pass


def release_mailbox(mailbox: dict) -> None:
    return None


def get_existing_mailbox(mail_config: dict, email: str) -> dict:
    enabled = _enabled_entries(mail_config)
    tried: set[str] = set()
    last_error = ""
    for _ in range(len(enabled)):
        provider = _create_provider(mail_config)
        provider_key = f"{provider.name}#{provider.provider_ref}"
        try:
            if provider_key in tried:
                continue
            tried.add(provider_key)
            if hasattr(provider, "get_existing_mailbox"):
                return provider.get_existing_mailbox(email)  # type: ignore[attr-defined]
            raise RuntimeError(f"邮箱提供商 {provider.name} 不支持查询已有邮箱")
        except RuntimeError as error:
            last_error = str(error)
            raise
        finally:
            provider.close()
    raise RuntimeError(last_error or "所有启用的邮箱提供商均无法查询已有邮箱")
