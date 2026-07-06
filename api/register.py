from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.support import require_admin
from services.register_service import register_service


class RegisterConfigRequest(BaseModel):
    mail: dict | None = None
    proxy: str | None = None
    total: int | None = None
    threads: int | None = None
    mode: str | None = None
    target_quota: int | None = None
    target_available: int | None = None
    check_interval: int | None = None


class OutlookPoolResetRequest(BaseModel):
    scope: str | None = None


class GptMailStatusRequest(BaseModel):
    provider: dict | None = None
    force: bool | None = None


class AutoRegisterConfigRequest(BaseModel):
    enabled: bool | None = None
    trigger_conditions: dict | None = None
    register_count: int | None = None
    cooldown_seconds: int | None = None
    max_total_accounts: int | None = None
    min_available_accounts: int | None = None
    max_failures: int | None = None
    reset_failures_after: int | None = None


class AutoRegisterTriggerRequest(BaseModel):
    count: int | None = None


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/register")
    async def get_register_config(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.get()}

    @router.post("/api/register")
    async def update_register_config(body: RegisterConfigRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.update(body.model_dump(exclude_none=True))}

    @router.post("/api/register/start")
    async def start_register(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.start()}

    @router.post("/api/register/stop")
    async def stop_register(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.stop()}

    @router.post("/api/register/reset")
    async def reset_register(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.reset()}

    @router.post("/api/register/outlook-pool/reset")
    async def reset_outlook_pool(body: OutlookPoolResetRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.reset_outlook_pool(body.scope or "all")}

    @router.post("/api/register/gptmail/status")
    async def get_gptmail_status(body: GptMailStatusRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"status": register_service.gptmail_status(body.provider, force=bool(body.force))}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/register/gptmail/refresh-key")
    async def refresh_gptmail_public_key(body: GptMailStatusRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"status": register_service.refresh_gptmail_public_key(body.provider, force=body.force is not False)}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/register/events")
    async def register_events(token: str = ""):
        require_admin(f"Bearer {token}")

        async def stream():
            last = ""
            while True:
                payload = json.dumps(register_service.get(), ensure_ascii=False)
                if payload != last:
                    last = payload
                    yield f"data: {payload}\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @router.get("/api/register/auto-register/status")
    async def get_auto_register_status(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"status": register_service.get_auto_register_status()}

    @router.post("/api/register/auto-register/config")
    async def update_auto_register_config(body: AutoRegisterConfigRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"status": register_service.update_auto_register_config(body.model_dump(exclude_none=True))}

    @router.post("/api/register/auto-register/trigger")
    async def trigger_auto_register(body: AutoRegisterTriggerRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return register_service.trigger_auto_register_manual(body.count)

    @router.post("/api/register/auto-register/reset-failures")
    async def reset_auto_register_failures(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"status": register_service.reset_auto_register_failures()}

    return router
