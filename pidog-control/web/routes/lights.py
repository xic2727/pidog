"""Light routes: presets, set mode/color/brightness, quick off."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field

from web.daemon_client import DaemonError, list_light_presets
from web.routes.actions import _http_status_for  # reuse the same status mapping

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["lights"])


class LightRequest(BaseModel):
    mode: str = Field("boom", description="off | breath | listen | boom")
    color: str = Field("white", description="Named color from /api/lights/presets")
    brightness: Optional[float] = Field(None, ge=0.0, le=1.0)
    bps: Optional[float] = Field(None, ge=0.1, le=2.0)


@router.get("/lights/presets", summary="Light mode + color presets")
async def get_presets() -> dict:
    return {"ok": True, "data": list_light_presets()}


@router.post("/light", summary="Set light mode/color/brightness")
async def post_light(req: LightRequest, request: Request) -> dict:
    client = request.app.state.daemon
    try:
        data = await client.light(
            mode=req.mode, color=req.color,
            brightness=req.brightness, bps=req.bps,
        )
        return {"ok": True, "data": data}
    except DaemonError as exc:
        raise HTTPException(status_code=_http_status_for(exc.code), detail=exc.to_dict())


@router.post("/light/off", summary="Quick off")
async def post_light_off(request: Request) -> dict:
    client = request.app.state.daemon
    try:
        data = await client.light(mode="off", color="white")
        return {"ok": True, "data": data}
    except DaemonError as exc:
        raise HTTPException(status_code=_http_status_for(exc.code), detail=exc.to_dict())
