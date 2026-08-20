"""Voice mode routes: toggle the ASR + LLM + TTS companion subprocess.

While voice mode is ON, the daemon refuses to dispatch hardware actions
(other than the toggle itself) because the spawned companion orchestrator
owns the Pidog runtime. This is the simplest correct integration given that
the orchestrator creates its own `Pidog()` instance in `examples/21_*`.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from web.daemon_client import DaemonError
from web.routes.actions import _http_status_for

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/voice", tags=["voice"])


@router.get("", summary="Current voice mode state")
async def get_voice(request: Request) -> dict:
    client = request.app.state.daemon
    try:
        data = await client.ping()
        return {
            "ok": True,
            "data": {
                "voice_mode": data.get("voice_mode", False),
                "voice_pid":  data.get("voice_pid"),
            },
        }
    except DaemonError as exc:
        raise HTTPException(status_code=_http_status_for(exc.code), detail=exc.to_dict())


@router.post("/on", summary="Enable voice mode (spawns companion subprocess)")
async def post_voice_on(request: Request) -> dict:
    client = request.app.state.daemon
    try:
        data = await client.voice_set(True)
        return {"ok": True, "data": data}
    except DaemonError as exc:
        raise HTTPException(status_code=_http_status_for(exc.code), detail=exc.to_dict())


@router.post("/off", summary="Disable voice mode (kills companion subprocess)")
async def post_voice_off(request: Request) -> dict:
    client = request.app.state.daemon
    try:
        data = await client.voice_set(False)
        return {"ok": True, "data": data}
    except DaemonError as exc:
        raise HTTPException(status_code=_http_status_for(exc.code), detail=exc.to_dict())
