# 付费邮箱服务提供商
# 这些服务需要充值后才能使用，会产生费用
# 不建议用于自动注册功能

from typing import Any
from datetime import datetime, timezone

from .mail_provider import BaseMailProvider, _create_session, _parse_received_at, _extract_content


class LuckMailProvider(BaseMailProvider):
    """LuckMail 付费购买邮箱服务

    官网：https://mails.luckyous.com
    API 文档：https://mails.luckyous.com/user/api-doc

    注意：这是付费服务，调用 create_mailbox 会购买邮箱并消耗余额
    """
    name = "luckmail"
    is_paid = True  # 标记为付费服务

    def __init__(self, entry: dict, conf: dict):
        super().__init__(conf, str(entry.get("provider_ref") or ""))
        self.api_base = str(entry.get("api_base") or "https://mails.luckyous.com/api/v1/openapi").strip().rstrip("/")
        self.api_key = str(entry.get("api_key") or "").strip()
        self.email_type = str(entry.get("email_type") or "").strip()  # ms_imap, ms_graph 等
        self.mail_domain = str(entry.get("mail_domain") or "").strip().lstrip("@")
        self.max_retry = int(entry.get("max_retry") or 20)  # 默认最多尝试 20 次购买

        if not self.api_key:
            raise RuntimeError("LuckMail 需要配置 api_key")

        self.session = _create_session(conf)
        self.session.headers.update({
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
            "User-Agent": conf["user_agent"]
        })

    def close(self) -> None:
        self.session.close()

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """调用 LuckMail API"""
        url = f"{self.api_base}/{path.lstrip('/')}"
        try:
            if method.upper() == "GET":
                resp = self.session.get(url, timeout=self.conf["request_timeout"], verify=False, **kwargs)
            elif method.upper() == "POST":
                resp = self.session.post(url, timeout=self.conf["request_timeout"], verify=False, **kwargs)
            else:
                raise RuntimeError(f"不支持的请求方法: {method}")

            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

            data = resp.json()
            if data.get("code") != 0:
                error_msg = data.get("message", '未知错误')
                raise RuntimeError(f"LuckMail API 错误 (code={data.get('code')}): {error_msg}")

            return data.get("data", {})
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"LuckMail API 请求失败: {e}")

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        """购买一个邮箱（会消耗余额！）

        注意：username 参数被忽略，因为 LuckMail 返回的是已购买的邮箱地址
        会自动重试购买，直到买到可用的邮箱（最多尝试 max_retry 次，默认 20 次）
        """
        last_error = ""

        for attempt in range(1, self.max_retry + 1):
            try:
                payload = {
                    "email_type": self.email_type,
                    "project_code": "openai",
                    "domain": self.mail_domain,
                    "quantity": 1,
                    "variant_mode": ""
                }

                data = self._request("POST", "email/purchase", json=payload)
                purchases = data.get("purchases", [])

                if not purchases:
                    raise RuntimeError("LuckMail 购买邮箱失败：未返回邮箱数据")

                purchase = purchases[0]
                email_address = purchase.get("email_address")
                token = purchase.get("token")

                if not email_address or not token:
                    raise RuntimeError("LuckMail 返回数据不完整")

                # 购买后立即测活，确保邮箱可用
                alive_data = self._request("GET", f"email/token/{token}/alive")
                if not alive_data.get("alive"):
                    status = alive_data.get("status", "unknown")
                    message = alive_data.get("message", "邮箱不可用")
                    last_error = f"邮箱 {email_address} 测活失败 ({status}): {message}"
                    # 测活失败，继续下一次购买
                    continue

                # 测活成功，返回邮箱
                return {
                    "provider": self.name,
                    "provider_ref": self.provider_ref,
                    "address": email_address,
                    "token": token,
                    "purchase_id": purchase.get("id"),
                    "label": f"LuckMail ({email_address})"
                }

            except RuntimeError as e:
                last_error = str(e)
                if "测活失败" not in last_error:
                    # 非测活失败的错误（如网络错误、余额不足等），直接抛出
                    raise

        # 所有尝试都失败
        raise RuntimeError(f"LuckMail 购买邮箱失败（尝试了 {self.max_retry} 次）: {last_error}")

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        """通过 token 获取最新邮件（使用 /code 接口，直接返回验证码）"""
        token = mailbox.get("token")
        if not token:
            raise RuntimeError("邮箱缺少 token")

        try:
            # 获取验证码（LuckMail 的 /code 接口会直接返回 verification_code）
            data = self._request("GET", f"email/token/{token}/code")

            # 如果没有新邮件
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
                "verification_code": data.get("verification_code"),  # 直接包含验证码
                "raw": mail
            }
        except Exception:
            return None

    def wait_for_code(self, mailbox: dict[str, Any]) -> str | None:
        """重写以利用 LuckMail 直接返回验证码的特性

        LuckMail 的 /code 接口会直接解析并返回 verification_code，
        无需从邮件正文中用正则提取。
        """
        import time

        deadline = time.monotonic() + self.conf["wait_timeout"]
        while time.monotonic() < deadline:
            message = self.fetch_latest_message(mailbox)
            if message:
                code = message.get("verification_code")
                if code:
                    return str(code)
            time.sleep(max(0.2, self.conf["wait_interval"]))
        return None


class Hotmail007Provider(BaseMailProvider):
    """Hotmail007 付费购买 Microsoft 邮箱服务

    官网：https://hotmail007.com
    API 文档：https://hotmail007.com/zh/api-docs

    注意：这是付费服务，调用 create_mailbox 会购买邮箱并消耗余额
    """
    name = "hotmail007"
    is_paid = True  # 标记为付费服务

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
        """调用 Hotmail007 GET API"""
        import urllib.parse

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
            resp = self.session.get(
                url,
                timeout=self.conf["request_timeout"],
                verify=False
            )

            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

            data = resp.json()

            if not data.get("success") or data.get("code") != 0:
                error_msg = data.get("message", "未知错误")
                raise RuntimeError(f"Hotmail007 API 错误: {error_msg}")

            return data.get("data", {})
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Hotmail007 API 请求失败: {e}")

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
                    return self._parse_stock_count(
                        item.get("stock") or item.get("count") or item.get("quantity") or item.get("available")
                    )

        raise RuntimeError(f"Hotmail007 库存查询未返回 product_id={self.product_id} 的库存")

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        """购买一个 Microsoft 邮箱（会消耗余额！）

        注意：username 参数被忽略，因为 Hotmail007 返回的是已购买的邮箱账号
        """
        stock = self._stock_count()
        if stock <= 0:
            raise RuntimeError(f"Hotmail007 product_id={self.product_id} 库存不足")

        data = self._api_get(
            "open/buy",
            productId=self.product_id,
            quantity=1
        )

        accounts = data.get("accounts", [])
        if not accounts:
            raise RuntimeError("Hotmail007 购买邮箱失败：未返回账号数据")

        # 格式: email:password:refreshToken:clientId
        account_str = accounts[0]
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
            "label": f"Hotmail007 ({email_addr})"
        }

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        """获取最新邮件（同时查询收件箱和垃圾邮件）"""
        account_str = f"{mailbox['address']}:{mailbox.get('password', '')}:{mailbox['refresh_token']}:{mailbox['client_id']}"

        # OpenAI 验证码可能进收件箱或垃圾箱，两个都要查
        candidates = []
        for folder in ("inbox", "junkemail"):
            try:
                params = {"account": account_str, "folder": folder}
                # 用 start_timestamp 过滤注册前的旧邮件
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

        # 取最新的一封（按 receivedAt 排序）
        candidates.sort(
            key=lambda d: _parse_received_at(d.get("receivedAt")) or datetime.fromtimestamp(0, tz=timezone.utc),
            reverse=True
        )
        data = candidates[0]

        return {
            "provider": self.name,
            "mailbox": mailbox["address"],
            "message_id": "",  # Hotmail007 不返回 message_id
            "subject": str(data.get("subject") or ""),
            "sender": str(data.get("from") or ""),
            "to": [mailbox["address"]],
            "text_content": str(data.get("text") or ""),
            "html_content": str(data.get("html") or ""),
            "received_at": _parse_received_at(data.get("receivedAt")),
            "raw": data
        }


class MSAccountManagerProvider(BaseMailProvider):
    """Microsoft Account Manager 自建服务提供商

    从你自己维护的 Microsoft 邮箱账号池中分配账号。
    这不是第三方付费服务，而是你自己搭建的账号管理服务。
    """
    name = "msaccount_manager"
    is_paid = False  # 不是付费服务，是自建服务

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
            "User-Agent": conf["user_agent"]
        })

    def close(self) -> None:
        self.session.close()

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """调用自建服务 API"""
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
        except Exception as e:
            raise RuntimeError(f"MSAccountManager API 请求失败: {e}")

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        """从账号池获取一个可用账号

        注意：username 参数被忽略，从已有的账号池中分配
        """
        # 获取账号列表
        data = self._request("GET", "api/open/accounts", params={"keyword": self.keyword})
        items = data.get("items", [])

        if not items:
            raise RuntimeError(f"MSAccountManager 账号池为空（keyword={self.keyword}）")

        # 优先选择备注为空的账号
        account = next((acc for acc in items if not acc.get("remark")), items[0])

        account_id = account.get("id")
        email_addr = account.get("account") or account.get("email")

        if not email_addr:
            raise RuntimeError("MSAccountManager 返回的账号数据缺少邮箱地址")

        # 标记账号为"使用中"
        if account_id:
            try:
                self._request("PATCH", f"api/open/accounts/{account_id}/remark", json={"remark": "使用中"})
            except Exception:
                pass  # 更新备注失败不影响使用

        return {
            "provider": self.name,
            "provider_ref": self.provider_ref,
            "address": email_addr,
            "account_id": account_id,
            "mode": self.mail_mode,
            "label": f"MSAccount ({email_addr})"
        }

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        """通过自建服务获取最新邮件"""
        try:
            # 使用 POST /api/open/messages 接口
            data = self._request(
                "POST",
                "api/open/messages",
                json={
                    "account": mailbox["address"],
                    "mode": mailbox.get("mode", self.mail_mode)
                }
            )

            messages = data.get("items") or data.get("messages") or []
            if not messages:
                return None

            # 取最新的一封（假设返回的已经按时间排序）
            mail = messages[0] if messages else None
            if not mail:
                return None

            text_content, html_content = _extract_content(mail)

            # 解析发件人
            sender = ""
            from_field = mail.get("from")
            if isinstance(from_field, dict):
                email_addr = from_field.get("emailAddress")
                if isinstance(email_addr, dict):
                    sender = str(email_addr.get("address") or "")
                else:
                    sender = str(from_field.get("address") or "")
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
                "raw": mail
            }
        except Exception:
            return None
