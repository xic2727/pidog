"""Camera routes.

v1 is intentionally minimal: the SPA talks to vilib directly (same-origin),
so this module only exposes a tiny /api/camera/info endpoint that returns
the URL the SPA should embed. Snapshot / proxy are v1.1.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/camera", tags=["camera"])


@router.get("/info", summary="Where the SPA should look for the video stream")
async def info(request: Request) -> dict:
    cfg = request.app.state.config
    host_hint = "{host}"  # the SPA replaces this with window.location.hostname
    url = cfg.camera.direct_mjpeg_url.format(host=host_hint)
    return {
        "ok": True,
        "data": {
            "mjpeg_url_template": url,
            "proxy_enabled": cfg.camera.proxy_enabled,
            "note": "v1 uses direct MJPEG; snapshot/proxy are v1.1",
        },
    }
