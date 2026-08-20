"""Head control routes: absolute RPY, home, incremental nudge."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field

from web.daemon_client import DaemonError, head_limits
from web.routes.actions import _http_status_for

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/head", tags=["head"])


class HeadRequest(BaseModel):
    yaw:   Optional[float] = Field(None, description="Absolute yaw in degrees")
    roll:  Optional[float] = Field(None, description="Absolute roll in degrees")
    pitch: Optional[float] = Field(None, description="Absolute pitch in degrees")
    speed: int = Field(50, ge=1, le=100)


class HeadNudgeRequest(BaseModel):
    axis:  str  = Field(..., description="yaw | roll | pitch")
    delta: float = Field(10.0, description="Relative increment in degrees (signed)")
    speed: int = Field(50, ge=1, le=100)


@router.get("/limits", summary="Safe head RPY limits")
async def get_limits() -> dict:
    return {"ok": True, "data": head_limits()}


@router.post("", summary="Move head to absolute RPY (None = keep current)")
async def post_head(req: HeadRequest, request: Request) -> dict:
    if req.yaw is None and req.roll is None and req.pitch is None:
        raise HTTPException(
            status_code=400,
            detail={"ok": False, "code": "NO_AXIS",
                    "error": "specify at least one of yaw/roll/pitch"},
        )
    client = request.app.state.daemon
    try:
        data = await client.head(yaw=req.yaw, roll=req.roll, pitch=req.pitch, speed=req.speed)
        return {"ok": True, "data": data}
    except DaemonError as exc:
        raise HTTPException(status_code=_http_status_for(exc.code), detail=exc.to_dict())


@router.post("/home", summary="Move head back to (0, 0, 0)")
async def post_head_home(request: Request, speed: int = 50) -> dict:
    client = request.app.state.daemon
    try:
        data = await client.head_home(speed=speed)
        return {"ok": True, "data": data}
    except DaemonError as exc:
        raise HTTPException(status_code=_http_status_for(exc.code), detail=exc.to_dict())


@router.post("/nudge", summary="Relative head move (used by the D-pad)")
async def post_head_nudge(req: HeadNudgeRequest, request: Request) -> dict:
    client = request.app.state.daemon
    try:
        data = await client.head_nudge(req.axis, req.delta, speed=req.speed)
        return {"ok": True, "data": data}
    except DaemonError as exc:
        raise HTTPException(status_code=_http_status_for(exc.code), detail=exc.to_dict())
