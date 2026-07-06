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
        self.max_retry = int(entry.get("max_retry") or 3)

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
        """
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

        return {
            "provider": self.name,
            "provider_ref": self.provider_ref,
            "address": email_address,
            "token": token,
            "purchase_id": purchase.get("id"),
            "label": f"LuckMail ({email_address})"
        }

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        """通过 token 获取最新邮件"""
        token = mailbox.get("token")
        if not token:
            raise RuntimeError("邮箱缺少 token")

        try:
            data = self._request("GET", f"email/token/{token}/mails")
            mails = data.get("mails", [])

            if not mails:
                return None

            # 按接收时间排序，取最新的
            mails.sort(key=lambda m: m.get("received_at", ""), reverse=True)
            mail = mails[0]

            return {
                "provider": self.name,
                "mailbox": mailbox["address"],
                "message_id": str(mail.get("message_id") or ""),
                "subject": str(mail.get("subject") or ""),
                "sender": str(mail.get("from") or ""),
                "to": [mailbox["address"]],
                "text_content": str(mail.get("body") or ""),
                "html_content": str(mail.get("html_body") or ""),
                "received_at": _parse_received_at(mail.get("received_at")),
                "raw": mail
            }
        except Exception:
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
        self.mail_type = str(entry.get("mail_type") or "outlook Trusted Graph").strip()
        self.mail_mode = str(entry.get("mail_mode") or "graph").strip().lower()

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

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        """购买一个 Microsoft 邮箱（会消耗余额！）

        注意：username 参数被忽略，因为 Hotmail007 返回的是已购买的邮箱账号
        """
        data = self._api_get(
            "open/buy",
            productId=self._get_product_id(),
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

    def _get_product_id(self) -> int:
        """获取商品 ID（根据 mail_type 查询库存）"""
        try:
            data = self._api_get("open/stock", mailType=self.mail_type)

            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        item_type = str(item.get("mailType") or "").strip()
                        if item_type.lower() == self.mail_type.lower():
                            product_id = item.get("productId")
                            if product_id:
                                return int(product_id)

                # 如果没找到匹配的，取第一个有库存的
                for item in data:
                    if isinstance(item, dict) and item.get("stock", 0) > 0:
                        return int(item.get("productId", 1))

            # 默认返回 1
            return 1
        except Exception:
            # 如果获取失败，使用默认值
            return 1

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        """通过 Microsoft Graph API 获取最新邮件"""
        try:
            account_str = f"{mailbox['address']}:{mailbox.get('password', '')}:{mailbox['refresh_token']}:{mailbox['client_id']}"

            data = self._api_get(
                "open/mail/latest",
                account=account_str,
                folder="inbox"
            )

            if not data:
                return None

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
        except Exception:
            return None


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
        self.mail_mode = str(entry.get("mail_mode") or "graph").strip().lower()
        self.proxy = str(entry.get("proxy") or "").strip()

        if not self.api_base or not self.api_key:
            raise RuntimeError("MSAccountManager 需要配置 api_base 和 api_key")

        self.session = _create_session(conf)
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": conf["user_agent"]
        })

    def close(self) -> None:
        self.session.close()

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """调用自建服务 API"""
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

            return resp.json()
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"MSAccountManager API 请求失败: {e}")

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        """从账号池获取一个可用账号

        注意：username 参数被忽略，因为从已有的账号池中分配
        """
        payload = {"mode": self.mail_mode}
        if self.proxy:
            payload["proxy"] = self.proxy

        data = self._request("POST", "api/account/acquire", json=payload)

        email_addr = str(data.get("email") or data.get("address") or "").strip()
        client_id = str(data.get("client_id") or "").strip()
        refresh_token = str(data.get("refresh_token") or "").strip()

        if not email_addr or not client_id or not refresh_token:
            raise RuntimeError("MSAccountManager 返回数据不完整")

        return {
            "provider": self.name,
            "provider_ref": self.provider_ref,
            "address": email_addr,
            "client_id": client_id,
            "refresh_token": refresh_token,
            "password": data.get("password", ""),
            "label": f"MSAccount ({email_addr})"
        }

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        """通过自建服务获取最新邮件"""
        try:
            data = self._request(
                "GET",
                "api/messages",
                params={
                    "email": mailbox["address"],
                    "top": 10,
                    "mode": self.mail_mode
                }
            )

            messages = data.get("value") or data.get("messages") or []
            if not messages:
                return None

            # 取最新的一封
            messages.sort(
                key=lambda m: _parse_received_at(m.get("receivedDateTime") or m.get("date")) or datetime.fromtimestamp(0, tz=timezone.utc),
                reverse=True
            )
            mail = messages[0]

            text_content, html_content = _extract_content(mail)

            # 解析发件人（兼容 Graph API 的嵌套结构和普通字符串）
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
                "message_id": str(mail.get("id") or ""),
                "subject": str(mail.get("subject") or ""),
                "sender": sender,
                "to": [mailbox["address"]],
                "text_content": text_content,
                "html_content": html_content,
                "received_at": _parse_received_at(mail.get("receivedDateTime") or mail.get("date")),
                "raw": mail
            }
        except Exception:
            return None
