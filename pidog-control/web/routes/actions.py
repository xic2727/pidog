"""Action routes: list, trigger, and release."""
from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field

from web.daemon_client import DaemonError, list_actions

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["actions"])


class ActionRequest(BaseModel):
    name: str = Field(..., description="Action name from /api/actions")
    speed: int = Field(70, ge=1, le=100, description="1..100")
    hold: bool = Field(False, description="If true, keep the posture until released")


class MoveRequest(BaseModel):
    name: str = Field(..., description="Move action name (forward, backward, turn-left, turn-right)")
    speed: int = Field(98, ge=1, le=100, description="Movement speed 1..100, default 98")


class ReleaseRequest(BaseModel):
    name: str = Field(..., description="Action name to release (must be a hold posture)")


@router.get("/actions", summary="List curated v1 action whitelist")
async def get_actions() -> dict:
    return {"ok": True, "data": {"actions": list_actions()}}


@router.post("/action", summary="Trigger one action (one-shot or hold)")
async def post_action(req: ActionRequest, request: Request) -> dict:
    client = request.app.state.daemon
    is_hold = req.hold
    try:
        data = await client.action(req.name, speed=req.speed, hold=is_hold)
        # If this is a new hold, also stop any previous hold (matches pidog-control UX).
        if is_hold and req.name in {"stand", "sit", "lie"}:
            asyncio.create_task(_stop_previous_hold(req.name))
        return {"ok": True, "data": data}
    except DaemonError as exc:
        raise HTTPException(status_code=_http_status_for(exc.code), detail=exc.to_dict())


@router.post("/move", summary="Start continuous movement worker (forward, backward, turn-left, turn-right)")
async def post_move(req: MoveRequest, request: Request) -> dict:
    client = request.app.state.daemon
    try:
        data = await client.move(req.name, speed=req.speed)
        return {"ok": True, "data": data}
    except DaemonError as exc:
        raise HTTPException(status_code=_http_status_for(exc.code), detail=exc.to_dict())


@router.post("/action/release", summary="Release a previously held posture")
async def post_action_release(req: ReleaseRequest, request: Request) -> dict:
    # We release by sending the same hold action with hold=False to a *different*
    # process.  The daemon already manages this internally via its hold.pid file,
    # so the simplest web-side command is: SIGTERM the running hold.
    # We delegate to the upstream `pidog_ctl.py` for the actual kill, since the
    # daemon socket is request/response only and does not expose release.
    rc, out, err = await _run_pidog_ctl(["action", "release", "--name", req.name])
    if rc != 0:
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "code": "RELEASE_FAILED",
                    "error": err.strip() or out.strip() or "release failed"},
        )
    return {"ok": True, "data": {"message": f"released {req.name}", "stdout": out.strip()}}


@router.post("/stop", summary="Immediately halt ongoing movement (legs/body)")
async def post_stop(request: Request, scope: str = "legs") -> dict:
    """Used by press-and-hold movement buttons.  Halts the legs (default)
    or the whole body.  Safe to call when nothing is moving.
    """
    client = request.app.state.daemon
    try:
        data = await client.stop(scope=scope)
        return {"ok": True, "data": data}
    except DaemonError as exc:
        raise HTTPException(status_code=_http_status_for(exc.code), detail=exc.to_dict())


# --- helpers ---

async def _stop_previous_hold(new_name: str) -> None:
    """Best-effort: kill any previous hold PID before starting a new one.

    Done in a background task so the user's request returns quickly. We use the
    upstream `pidog_ctl.py action release` flow via subprocess (the daemon's
    Unix socket protocol is request/response and has no release verb).
    """
    try:
        await _run_pidog_ctl(["action", "release"])
    except Exception as exc:
        logger.debug("previous hold release failed (likely none): %s", exc)


async def _run_pidog_ctl(args: list[str]) -> tuple[int, str, str]:
    """Run `pidog_ctl.py <args>` in a thread. Returns (rc, stdout, stderr)."""
    import os
    script = os.environ.get(
        "PIDOG_CTL_SCRIPT",
        str(Path.home() / "pidog" / "pidog-control" / "scripts" / "pidog_ctl.py"),
    )
    def _go() -> tuple[int, str, str]:
        proc = subprocess.run(
            ["python3", script, *args],
            capture_output=True, text=True, timeout=10.0,
        )
        return proc.returncode, proc.stdout, proc.stderr
    return await asyncio.to_thread(_go)


def _http_status_for(code: str) -> int:
    return {
        "DAEMON_DOWN":          503,
        "DAEMON_ERROR":         502,
        "TIMEOUT":              504,
        "UNKNOWN_ACTION":       400,
        "UNKNOWN_LIGHT_MODE":   400,
        "UNKNOWN_COLOR":        400,
        "INVALID_HEAD_ANGLE":   400,
        "INVALID_HEAD_AXIS":    400,
        "NO_AXIS":              400,
        "VOICE_MODE_ACTIVE":    409,
        "CALIBRATION_NEEDED":   409,
        "RUNTIME_UNAVAILABLE":  503,
        "DAEMON_SCRIPT_MISSING":500,
        "DAEMON_SCRIPT_LOAD_FAILED":500,
    }.get(code, 500)
