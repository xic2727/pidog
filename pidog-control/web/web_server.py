"""PiDog Local Web Console — FastAPI entry point.

Two equivalent ways to run:

    # As a script (cd into the web/ directory first)
    cd pidog-control/web && python3 web_server.py

    # As a module (from anywhere on the path)
    python3 -m web.web_server       # if cwd is pidog-control/
    python3 -m pidog_control.web.web_server   # if pidog-control is renamed to pidog_control

The script-mode sys.path shim below makes the first form work even when
the file is invoked with an absolute path.
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Allow `python3 web_server.py` from anywhere: add the parent of this package
# (i.e. the directory that contains `web/`) to sys.path, so absolute imports
# of the form `from web.xxx import ...` resolve cleanly even when launched
# as a script.
_HERE = Path(__file__).resolve().parent        # .../pidog-control/web
_PARENT = _HERE.parent                          # .../pidog-control
for _p in (str(_PARENT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Always use the absolute `web.xxx` form so both script and module launch
# modes resolve the same modules.
from web.config import AppConfig
from web.daemon_client import DaemonClient
from web.mdns_register import format_banner, probe
from web.routes import actions, camera, heads, lights, sounds, status, voice
from web.status_poller import StatusBroadcaster

logger = logging.getLogger("pidog.web")


def _configure_logging(level: str) -> None:
    numeric = {
        "critical": logging.CRITICAL, "error": logging.ERROR,
        "warning": logging.WARNING,  "info": logging.INFO,
        "debug": logging.DEBUG,
    }.get(level.lower(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def create_app(config: AppConfig | None = None) -> FastAPI:
    cfg = config or AppConfig.load()
    _configure_logging(cfg.server.log_level)

    client = DaemonClient.from_config(cfg.daemon)
    broadcaster = StatusBroadcaster(client, cfg.status.ws_push_interval_ms)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup banner.
        if cfg.mdns.enabled:
            report = probe(cfg.mdns.hostname, cfg.server.port)
            for line in format_banner(report).splitlines():
                logger.info(line)
        else:
            logger.info("[pidog-web] mDNS disabled; listening on %s:%d",
                        cfg.server.host, cfg.server.port)
        await broadcaster.start()
        try:
            yield
        finally:
            await broadcaster.stop()

    app = FastAPI(
        title="PiDog Local Web Console",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.config = cfg
    app.state.daemon = client
    app.state.broadcaster = broadcaster

    # CORS: keep tight; LAN-only is the assumption.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],          # same-origin only
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # API routes
    app.include_router(status.router)
    app.include_router(actions.router)
    app.include_router(lights.router)
    app.include_router(camera.router)
    app.include_router(heads.router)
    app.include_router(voice.router)
    app.include_router(sounds.router)

    # WebSocket
    @app.websocket("/ws/status")
    async def ws_status(ws: WebSocket) -> None:
        await broadcaster.subscribe(ws)

    # Static SPA
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.is_dir():
        app.mount(
            "/static",
            StaticFiles(directory=str(static_dir)),
            name="static",
        )

        @app.get("/", include_in_schema=False)
        async def root_index() -> FileResponse:
            return FileResponse(static_dir / "index.html")

        @app.get("/manifest.webmanifest", include_in_schema=False)
        async def manifest() -> FileResponse:
            return FileResponse(
                static_dir / "manifest.webmanifest",
                media_type="application/manifest+json",
            )

    return app


# `uvicorn web.web_server:app` works because of this:
app = create_app()


def main() -> None:
    import uvicorn
    cfg = app.state.config
    # Always use the absolute import path; the sys.path shim at the top of
    # this file makes `web.web_server` resolvable regardless of cwd.
    uvicorn.run(
        "web.web_server:app",
        host=cfg.server.host,
        port=cfg.server.port,
        log_level=cfg.server.log_level,
        reload=False,
    )


if __name__ == "__main__":
    main()
