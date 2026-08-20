"""Status / health / capabilities routes."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from web.daemon_client import DaemonError, head_limits, list_actions, list_light_presets
from web.routes.actions import _http_status_for

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["status"])


@router.get("/health", summary="Liveness check (does NOT touch the daemon)")
async def health() -> dict:
    return {"ok": True, "data": {"service": "pidog-web", "version": "1.0.0"}}


@router.get("/daemon/status", summary="Forward daemon ping")
async def daemon_status(request: Request) -> dict:
    client = request.app.state.daemon
    if not client.socket_exists():
        return {
            "ok": False,
            "error": "daemon socket missing",
            "code": "DAEMON_DOWN",
            "data": {"daemon": "down"},
        }
    try:
        data = await client.ping()
        return {"ok": True, "data": {"daemon": "up", **data}}
    except DaemonError as exc:
        return {"ok": False, "error": exc.message, "code": exc.code}


@router.get("/capabilities", summary="What the UI can render right now")
async def capabilities() -> dict:
    return {
        "ok": True,
        "data": {
            "actions": list_actions(),
            "lights": list_light_presets(),
            "head": head_limits(),
            "camera": {
                "direct_mjpeg": "/api/camera/info",
                "hint": "vilib is expected at http://<pi-host>:9000/mjpg",
            },
            "voice": {
                "script_hint": "examples/21_embodied_pet_companion.py (overridable via PIDOG_VOICE_SCRIPT)",
                "exclusivity": "While voice mode is ON, hardware actions are paused",
            },
        },
    }
