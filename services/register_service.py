from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

from services.account_service import account_service
from services.config import DATA_DIR
from services.json_file import read_json_object, write_json_file
from services.register import openai_register


REGISTER_FILE = DATA_DIR / "register.json"


def _safe_bool(value: object, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return fallback


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_config() -> dict:
    return {
        **openai_register.config,
        "mode": "total",
        "target_quota": 100,
        "target_available": 10,
        "check_interval": 5,
        "enabled": False,
        "auto_register": {
            "enabled": False,
            "trigger_conditions": {
                "no_account": True,
                "all_quota_exhausted": True,
                "all_accounts_invalid": True,
                "all_accounts_rate_limited": True,
                "all_accounts_busy": True,
            },
            "register_count": 1,
            "cooldown_seconds": 300,
            "max_total_accounts": 100,
            "min_available_accounts": 0,
            "max_failures": 3,
            "reset_failures_after": 3600,
        },
        "stats": {
            "success": 0,
            "fail": 0,
            "done": 0,
            "running": 0,
            "threads": openai_register.config["threads"],
            "elapsed_seconds": 0,
            "avg_seconds": 0,
            "success_rate": 0,
            "current_quota": 0,
            "current_available": 0,
        },
    }


def _normalize(raw: dict) -> dict:
    cfg = _default_config()
    cfg.update({k: v for k, v in raw.items() if k not in {"stats", "logs"}})
    cfg["total"] = max(1, int(cfg.get("total") or 1))
    cfg["threads"] = max(1, int(cfg.get("threads") or 1))
    cfg["mode"] = str(cfg.get("mode") or "total").strip() if str(cfg.get("mode") or "total").strip() in {"total", "quota", "available"} else "total"
    cfg["target_quota"] = max(1, int(cfg.get("target_quota") or 1))
    cfg["target_available"] = max(1, int(cfg.get("target_available") or 1))
    cfg["check_interval"] = max(1, int(cfg.get("check_interval") or 5))
    cfg["proxy"] = str(cfg.get("proxy") or "").strip()
    default_mail = _default_config()["mail"] if isinstance(_default_config().get("mail"), dict) else {}
    mail = cfg.get("mail") if isinstance(cfg.get("mail"), dict) else {}
    cfg["mail"] = {**default_mail, **mail}
    cfg["mail"]["api_use_register_proxy"] = _safe_bool(cfg["mail"].get("api_use_register_proxy"), True)
    cfg["mail"].pop("proxy", None)
    cfg["enabled"] = bool(cfg.get("enabled"))

    # 规范化 auto_register 配置
    default_auto_register = _default_config()["auto_register"]
    auto_register = cfg.get("auto_register") if isinstance(cfg.get("auto_register"), dict) else {}
    cfg["auto_register"] = {**default_auto_register, **auto_register}
    cfg["auto_register"]["enabled"] = _safe_bool(cfg["auto_register"].get("enabled"), False)
    cfg["auto_register"]["register_count"] = max(1, min(5, int(cfg["auto_register"].get("register_count") or 1)))
    cfg["auto_register"]["cooldown_seconds"] = max(60, int(cfg["auto_register"].get("cooldown_seconds") or 300))
    cfg["auto_register"]["max_total_accounts"] = max(0, int(cfg["auto_register"].get("max_total_accounts") or 100))
    cfg["auto_register"]["min_available_accounts"] = max(0, int(cfg["auto_register"].get("min_available_accounts") or 0))
    cfg["auto_register"]["max_failures"] = max(1, int(cfg["auto_register"].get("max_failures") or 3))
    cfg["auto_register"]["reset_failures_after"] = max(60, int(cfg["auto_register"].get("reset_failures_after") or 3600))

    # 规范化触发条件
    default_trigger_conditions = default_auto_register["trigger_conditions"]
    trigger_conditions = cfg["auto_register"].get("trigger_conditions") if isinstance(cfg["auto_register"].get("trigger_conditions"), dict) else {}
    cfg["auto_register"]["trigger_conditions"] = {
        k: _safe_bool(trigger_conditions.get(k), default_trigger_conditions[k])
        for k in default_trigger_conditions
    }

    stats = {**_default_config()["stats"], **(raw.get("stats") if isinstance(raw.get("stats"), dict) else {}),
             "threads": cfg["threads"]}
    cfg["stats"] = stats
    return cfg


class RegisterService:
    def __init__(self, store_file: Path):
        self._store_file = store_file
        self._lock = threading.RLock()
        self._runner: threading.Thread | None = None
        self._logs: list[dict] = []
        openai_register.register_log_sink = self._append_log
        self._config = self._load()
        # 自动注册状态
        self._auto_register_state = {
            "enabled": False,
            "last_triggered_at": None,
            "last_completed_at": None,
            "last_trigger_reason": "",
            "running": False,
            "consecutive_failures": 0,
            "last_failure_reset_at": None,
            "total_auto_registered": 0,
            "trigger_count_by_reason": {
                "no_account": 0,
                "all_quota_exhausted": 0,
                "all_accounts_invalid": 0,
                "all_accounts_rate_limited": 0,
                "all_accounts_busy": 0,
                "min_available_threshold": 0,
            },
        }
        if self._config["enabled"]:
            self.start()

    def _load(self) -> dict:
        return _normalize(read_json_object(self._store_file, name="register.json"))

    def _save(self) -> None:
        write_json_file(self._store_file, self._config)

    def get(self) -> dict:
        with self._lock:
            snapshot = json.loads(json.dumps({**self._config, "logs": self._logs[-300:]}, ensure_ascii=False))
        return snapshot

    def _drop_mail_proxy(self) -> None:
        if isinstance(self._config.get("mail"), dict):
            self._config["mail"].pop("proxy", None)

    def update(self, updates: dict) -> dict:
        with self._lock:
            self._config = _normalize({**self._config, **updates})
            self._drop_mail_proxy()
            openai_register.config.update({k: self._config[k] for k in ("mail", "proxy", "total", "threads")})
            self._save()
            return self.get()

    def start(self) -> dict:
        with self._lock:
            if self._runner and self._runner.is_alive():
                self._config["enabled"] = True
                self._save()
                return self.get()
            self._config["enabled"] = True
            self._drop_mail_proxy()
            self._logs = []
            metrics = self._pool_metrics()
            self._config["stats"] = {"job_id": uuid.uuid4().hex, "success": 0, "fail": 0, "done": 0, "running": 0, "threads": self._config["threads"], **metrics, "started_at": _now(), "updated_at": _now()}
            openai_register.config.update({k: self._config[k] for k in ("mail", "proxy", "total", "threads")})
            with openai_register.stats_lock:
                openai_register.stats.update({"done": 0, "success": 0, "fail": 0, "start_time": time.time()})
            self._save()
            self._runner = threading.Thread(target=self._run, daemon=True, name="openai-register")
            self._runner.start()
            self._append_log(f"注册任务启动，模式={self._config['mode']}，线程数={self._config['threads']}", "yellow")
            return self.get()

    def stop(self) -> dict:
        with self._lock:
            self._config["enabled"] = False
            self._config["stats"]["updated_at"] = _now()
            self._save()
            self._append_log("已请求停止注册任务，正在等待当前运行任务结束", "yellow")
            return self.get()

    def reset(self) -> dict:
        with self._lock:
            self._logs = []
            self._config["stats"] = {"success": 0, "fail": 0, "done": 0, "running": 0, "threads": self._config["threads"], "elapsed_seconds": 0, "avg_seconds": 0, "success_rate": 0, **self._pool_metrics(), "updated_at": _now()}
            with openai_register.stats_lock:
                openai_register.stats.update({"done": 0, "success": 0, "fail": 0, "start_time": 0.0})
            self._save()
            return self.get()

    def _append_log(self, text: str, color: str = "") -> None:
        with self._lock:
            self._logs.append({"time": _now(), "text": str(text), "level": str(color or "info")})
            self._logs = self._logs[-300:]

    def _pool_metrics(self) -> dict:
        items = account_service.list_accounts()
        normal = [item for item in items if item.get("status") == "正常"]
        return {
            "current_quota": sum(int(item.get("quota") or 0) for item in normal if not item.get("image_quota_unknown")),
            "current_available": len(normal),
        }

    def _check_account_pool_status(self) -> dict:
        """检查账号池状态，返回各种条件的判断结果"""
        items = account_service.list_accounts()

        if not items:
            return {
                "trigger": True,
                "reason": "no_account",
                "message": "账号池为空"
            }

        # 筛选启用的账号
        enabled_accounts = [item for item in items if item.get("status") != "禁用"]

        if not enabled_accounts:
            return {
                "trigger": True,
                "reason": "all_accounts_disabled",
                "message": "所有账号都已禁用"
            }

        # 筛选可用账号（正常状态）
        available_accounts = [item for item in enabled_accounts if item.get("status") == "正常"]

        if not available_accounts:
            # 进一步分析原因
            # 检查是否所有账号额度耗尽
            quota_zero_count = sum(
                1 for item in enabled_accounts
                if item.get("status") == "限流" or (
                    not item.get("image_quota_unknown") and int(item.get("quota") or 0) == 0
                )
            )
            if quota_zero_count == len(enabled_accounts):
                return {
                    "trigger": True,
                    "reason": "all_quota_exhausted",
                    "message": f"所有 {len(enabled_accounts)} 个账号额度已耗尽"
                }

            # 检查是否所有账号状态异常
            invalid_count = sum(1 for item in enabled_accounts if item.get("status") == "异常")
            if invalid_count == len(enabled_accounts):
                return {
                    "trigger": True,
                    "reason": "all_accounts_invalid",
                    "message": f"所有 {len(enabled_accounts)} 个账号状态异常"
                }

            # 综合原因
            return {
                "trigger": True,
                "reason": "no_available_account",
                "message": f"无可用账号（总数 {len(items)}，已启用 {len(enabled_accounts)}，可用 0）"
            }

        # 有可用账号，不触发
        return {
            "trigger": False,
            "available_count": len(available_accounts),
            "total_count": len(items)
        }

    def _should_trigger_auto_register(self) -> tuple[bool, str, str]:
        """
        判断是否应该触发自动注册
        返回: (是否触发, 触发原因, 描述信息)
        """
        config = self._config.get("auto_register", {})

        # 开关未开启
        if not config.get("enabled", False):
            return False, "", "自动注册未启用"

        # 正在注册中
        if self._auto_register_state["running"]:
            return False, "", "已有注册任务正在运行"

        # 冷却期内
        cooldown = config.get("cooldown_seconds", 300)
        if self._auto_register_state["last_triggered_at"]:
            try:
                last_triggered = datetime.fromisoformat(self._auto_register_state["last_triggered_at"])
                elapsed = (datetime.now(timezone.utc) - last_triggered).total_seconds()
                if elapsed < cooldown:
                    return False, "", f"冷却期内（剩余 {int(cooldown - elapsed)} 秒）"
            except Exception:
                pass

        # 失败次数过多（熔断）
        max_failures = config.get("max_failures", 3)
        if self._auto_register_state["consecutive_failures"] >= max_failures:
            # 检查是否需要重置失败计数
            reset_after = config.get("reset_failures_after", 3600)
            if self._auto_register_state["last_failure_reset_at"]:
                try:
                    last_reset = datetime.fromisoformat(self._auto_register_state["last_failure_reset_at"])
                    elapsed = (datetime.now(timezone.utc) - last_reset).total_seconds()
                    if elapsed >= reset_after:
                        self._auto_register_state["consecutive_failures"] = 0
                        self._auto_register_state["last_failure_reset_at"] = _now()
                    else:
                        return False, "", f"熔断保护中（连续失败 {self._auto_register_state['consecutive_failures']} 次）"
                except Exception:
                    return False, "", f"熔断保护中（连续失败 {self._auto_register_state['consecutive_failures']} 次）"
            else:
                return False, "", f"熔断保护中（连续失败 {self._auto_register_state['consecutive_failures']} 次）"

        # 账号数量达到上限
        max_total = config.get("max_total_accounts", 0)
        if max_total > 0:
            current_count = len(account_service.list_accounts())
            if current_count >= max_total:
                return False, "", f"账号数已达上限（{current_count}/{max_total}）"

        # 检查账号池状态
        pool_status = self._check_account_pool_status()

        if pool_status.get("trigger"):
            reason = pool_status["reason"]
            # 检查该触发条件是否启用
            trigger_conditions = config.get("trigger_conditions", {})

            if reason == "no_account" and not trigger_conditions.get("no_account", True):
                return False, "", "账号池为空但该触发条件未启用"

            if reason == "all_quota_exhausted" and not trigger_conditions.get("all_quota_exhausted", True):
                return False, "", "所有账号额度耗尽但该触发条件未启用"

            if reason == "all_accounts_invalid" and not trigger_conditions.get("all_accounts_invalid", True):
                return False, "", "所有账号异常但该触发条件未启用"

            return True, reason, pool_status["message"]

        # 检查可用账号数阈值
        min_available = config.get("min_available_accounts", 0)
        if min_available > 0:
            available_count = pool_status.get("available_count", 0)
            if available_count < min_available:
                return True, "min_available_threshold", f"可用账号数 {available_count} 低于阈值 {min_available}"

        return False, "", "条件不满足"

    def trigger_auto_register_if_needed(self) -> tuple[bool, str]:
        """在图片调度失败时调用此函数，检查并触发自动注册"""
        should_trigger, reason, message = self._should_trigger_auto_register()

        if not should_trigger:
            return False, message

        # 标记为运行中
        with self._lock:
            self._auto_register_state["running"] = True
            self._auto_register_state["last_triggered_at"] = _now()
            self._auto_register_state["last_trigger_reason"] = reason
            self._auto_register_state["trigger_count_by_reason"][reason] = \
                self._auto_register_state["trigger_count_by_reason"].get(reason, 0) + 1

        # 记录日志
        self._append_log(f"[自动注册] 已触发: {message}（原因: {reason}）", "yellow")

        # 在后台线程启动注册任务
        count = self._config["auto_register"].get("register_count", 1)
        threading.Thread(
            target=self._run_auto_register_task,
            args=(count, reason),
            daemon=True
        ).start()

        return True, f"自动注册已启动，将注册 {count} 个账号"

    def _run_auto_register_task(self, count: int, reason: str) -> None:
        """后台运行自动注册任务

        注意：自动注册会临时禁用付费邮箱提供商（LuckMail、Hotmail007），
        避免自动扣费。付费服务只能用于手动注册。
        """
        # 付费邮箱提供商类型（自动注册时排除）
        PAID_PROVIDER_TYPES = {"luckmail", "hotmail007"}

        try:
            self._append_log(f"[自动注册] 开始注册 {count} 个账号", "yellow")

            # 检查是否有可用的免费邮箱提供商
            mail = self._config.get("mail", {})
            providers = mail.get("providers", []) if isinstance(mail, dict) else []
            free_providers = [
                p for p in providers
                if isinstance(p, dict) and p.get("enable") and p.get("type") not in PAID_PROVIDER_TYPES
            ]
            if not free_providers:
                with self._lock:
                    self._auto_register_state["consecutive_failures"] += 1
                    if self._auto_register_state["consecutive_failures"] == 1:
                        self._auto_register_state["last_failure_reset_at"] = _now()
                    self._append_log("[自动注册] 失败：没有可用的免费邮箱来源（付费服务不参与自动注册）", "red")
                return

            count = max(1, min(5, int(count or 1)))
            original_config = {
                "enabled": self._config["enabled"],
                "total": self._config["total"],
                "mode": self._config["mode"],
            }
            original_mail = json.loads(json.dumps(mail, ensure_ascii=False))
            configured = False
            current_thread = threading.current_thread()

            try:
                with self._lock:
                    if self._runner and self._runner.is_alive() and self._runner is not current_thread:
                        self._auto_register_state["consecutive_failures"] += 1
                        if self._auto_register_state["consecutive_failures"] == 1:
                            self._auto_register_state["last_failure_reset_at"] = _now()
                        self._append_log("[自动注册] 失败：已有注册任务正在运行", "red")
                        return

                    self._runner = current_thread
                    self._config["enabled"] = True
                    self._config["total"] = count
                    self._config["mode"] = "total"
                    configured = True

                    # 临时禁用付费邮箱提供商
                    for provider in self._config["mail"].get("providers", []):
                        if isinstance(provider, dict) and provider.get("type") in PAID_PROVIDER_TYPES:
                            provider["_auto_register_disabled"] = provider.get("enable")
                            provider["enable"] = False

                    metrics = self._pool_metrics()
                    self._config["stats"] = {
                        "job_id": uuid.uuid4().hex,
                        "success": 0,
                        "fail": 0,
                        "done": 0,
                        "running": 0,
                        "threads": self._config["threads"],
                        **metrics,
                        "started_at": _now(),
                        "updated_at": _now(),
                    }
                    openai_register.config.update({k: self._config[k] for k in ("mail", "proxy", "total", "threads")})
                    with openai_register.stats_lock:
                        openai_register.stats.update({"done": 0, "success": 0, "fail": 0, "start_time": time.time()})
                    self._save()

                # 当前方法已经在后台线程中运行，直接复用注册 runner 逻辑。
                self._run()

                stats = self.get().get("stats", {})
                success = int(stats.get("success") or 0)
                with self._lock:
                    if success > 0:
                        self._auto_register_state["consecutive_failures"] = 0
                        self._auto_register_state["total_auto_registered"] += success
                        self._append_log(f"[自动注册] 完成，成功注册 {success} 个账号", "green")
                    else:
                        self._auto_register_state["consecutive_failures"] += 1
                        if self._auto_register_state["consecutive_failures"] == 1:
                            self._auto_register_state["last_failure_reset_at"] = _now()
                        self._append_log("[自动注册] 失败，未成功注册任何账号", "red")
            finally:
                if configured:
                    with self._lock:
                        self._config["enabled"] = original_config["enabled"]
                        self._config["total"] = original_config["total"]
                        self._config["mode"] = original_config["mode"]
                        self._config["mail"] = original_mail
                        if self._runner is current_thread:
                            self._runner = None
                        openai_register.config.update({k: self._config[k] for k in ("mail", "proxy", "total", "threads")})
                        self._save()

        except Exception as e:
            with self._lock:
                self._auto_register_state["consecutive_failures"] += 1
                self._append_log(f"[自动注册] 异常: {str(e)}", "red")

        finally:
            with self._lock:
                self._auto_register_state["running"] = False
                self._auto_register_state["last_completed_at"] = _now()

    def get_auto_register_status(self) -> dict:
        """获取自动注册状态"""
        with self._lock:
            return {
                **self._auto_register_state,
                "config": self._config.get("auto_register", {}),
            }

    def update_auto_register_config(self, updates: dict) -> dict:
        """更新自动注册配置"""
        with self._lock:
            auto_register = self._config.get("auto_register", {})
            auto_register.update(updates)
            self._config["auto_register"] = auto_register
            self._config = _normalize(self._config)
            self._auto_register_state["enabled"] = self._config["auto_register"].get("enabled", False)
            self._save()
            return self.get_auto_register_status()

    def reset_auto_register_failures(self) -> dict:
        """重置自动注册失败计数"""
        with self._lock:
            self._auto_register_state["consecutive_failures"] = 0
            self._auto_register_state["last_failure_reset_at"] = _now()
            self._append_log("[自动注册] 失败计数已重置", "yellow")
            return self.get_auto_register_status()

    def trigger_auto_register_manual(self, count: int | None = None) -> dict:
        """手动触发自动注册（测试用）"""
        with self._lock:
            if self._auto_register_state["running"]:
                return {"triggered": False, "message": "已有注册任务正在运行"}

            register_count = count if count is not None else self._config["auto_register"].get("register_count", 1)
            register_count = max(1, min(5, int(register_count)))

            self._auto_register_state["running"] = True
            self._auto_register_state["last_triggered_at"] = _now()
            self._auto_register_state["last_trigger_reason"] = "manual"

            self._append_log(f"[自动注册] 手动触发，将注册 {register_count} 个账号", "yellow")

            threading.Thread(
                target=self._run_auto_register_task,
                args=(register_count, "manual"),
                daemon=True
            ).start()

            return {"triggered": True, "message": f"自动注册任务已启动，将注册 {register_count} 个账号"}

    def _target_reached(self, cfg: dict, submitted: int) -> bool:
        mode = str(cfg.get("mode") or "total")
        metrics = self._pool_metrics()
        self._bump(**metrics)
        if mode == "quota":
            reached = metrics["current_quota"] >= int(cfg.get("target_quota") or 1)
            self._append_log(f"检查号池：当前正常账号={metrics['current_available']}，当前剩余额度={metrics['current_quota']}，目标额度={cfg.get('target_quota')}，{'跳过注册' if reached else '继续注册'}", "yellow")
            return reached
        if mode == "available":
            reached = metrics["current_available"] >= int(cfg.get("target_available") or 1)
            self._append_log(f"检查号池：当前正常账号={metrics['current_available']}，目标账号={cfg.get('target_available')}，当前剩余额度={metrics['current_quota']}，{'跳过注册' if reached else '继续注册'}", "yellow")
            return reached
        return submitted >= int(cfg.get("total") or 1)

    def _bump(self, **updates) -> None:
        with self._lock:
            self._config["stats"].update(updates)
            stats = self._config["stats"]
            started_at = str(stats.get("started_at") or "")
            if started_at:
                try:
                    elapsed = max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(started_at)).total_seconds())
                except Exception:
                    elapsed = 0.0
                done = int(stats.get("done") or 0)
                success = int(stats.get("success") or 0)
                fail = int(stats.get("fail") or 0)
                stats["elapsed_seconds"] = round(elapsed, 1)
                stats["avg_seconds"] = round(elapsed / success, 1) if success else 0
                stats["success_rate"] = round(success * 100 / max(1, success + fail), 1)
            self._config["stats"]["updated_at"] = _now()
            self._save()

    def _run(self) -> None:
        threads = int(self.get()["threads"])
        submitted, done, success, fail = 0, 0, 0, 0
        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = set()
            while True:
                cfg = self.get()
                while self.get()["enabled"] and not self._target_reached(cfg, submitted) and len(futures) < threads:
                    submitted += 1
                    futures.add(executor.submit(openai_register.worker, submitted))
                self._bump(running=len(futures), done=done, success=success, fail=fail)
                if not futures and (not self.get()["enabled"] or str(cfg.get("mode") or "total") == "total"):
                    break
                if not futures:
                    time.sleep(max(1, int(cfg.get("check_interval") or 5)))
                    continue
                finished, futures = wait(futures, return_when=FIRST_COMPLETED)
                for future in finished:
                    done += 1
                    try:
                        result = future.result()
                        success += 1 if result.get("ok") else 0
                        fail += 0 if result.get("ok") else 1
                    except Exception:
                        fail += 1
        self._bump(running=0, done=done, success=success, fail=fail, finished_at=_now())
        with self._lock:
            self._config["enabled"] = False
            self._save()
        self._append_log(f"注册任务结束，成功{success}，失败{fail}", "yellow")


register_service = RegisterService(REGISTER_FILE)
