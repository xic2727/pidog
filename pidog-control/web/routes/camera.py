"""Camera routes.

Exposes two endpoints:
  * GET /api/camera/info    — small JSON telling the SPA where to load the stream
  * GET /api/camera/stream  — same-origin MJPEG proxy (when `proxy_enabled`)

The proxy is the default in v1: it forwards the upstream picamera2/vilib MJPEG
byte-for-byte from inside the web server, so the browser always loads the
stream from the same origin as the page. This sidesteps:

  * cross-port `<img>` quirks (some browsers / OS combos refuse MJPEG from
    a different port than the page);
  * mDNS resolution failures (`{host}` substitution can resolve to
    `pidog.local` which doesn't work on phones that can't reach avahi);
  * CORS / mixed-content edge cases when the page is on HTTPS but the
    camera is plain HTTP.

For backward compatibility, `proxy_enabled = false` falls back to the old
direct-URL mode (`http://{host}:9000/mjpg`).
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/camera", tags=["camera"])


@router.get("/info", summary="Where the SPA should look for the video stream")
async def info(request: Request) -> dict:
    cfg = request.app.state.config
    if cfg.camera.proxy_enabled:
        # Relative URL — browser resolves it against the page's origin, so we
        # never depend on mDNS or hostname matching.
        url = "/api/camera/stream"
    else:
        host_hint = "{host}"  # the SPA replaces this with window.location.hostname
        url = cfg.camera.direct_mjpeg_url.format(host=host_hint)
    return {
        "ok": True,
        "data": {
            "mjpeg_url_template": url,
            "proxy_enabled": cfg.camera.proxy_enabled,
            "note": "v1 default uses same-origin proxy; direct mode is opt-in",
        },
    }


@router.get("/stream", summary="Same-origin MJPEG proxy to upstream camera server")
async def stream(request: Request) -> StreamingResponse:
    cfg = request.app.state.config
    if not cfg.camera.proxy_enabled:
        raise HTTPException(
            status_code=503,
            detail={
                "ok": False,
                "error": "proxy disabled",
                "hint": "set [camera] proxy_enabled = true in web_server.toml",
            },
        )

    # Resolve `{host}` placeholder to loopback — we never want the proxy to
    # bounce back through itself or through mDNS.
    upstream = cfg.camera.direct_mjpeg_url.replace("{host}", "127.0.0.1")
    parsed = urlparse(upstream)
    upstream_host = parsed.hostname or "127.0.0.1"
    upstream_port = parsed.port or 80
    upstream_path = (parsed.path or "/") + ("?%s" % parsed.query if parsed.query else "")

    # Default content type; we'll override it with whatever the upstream says
    # (the boundary parameter must match the actual `boundary=...` used in the
    # body or browsers can't demultiplex the multipart stream).
    content_type = "multipart/x-mixed-replace; boundary=frame"

    async def relay() -> "asyncio.AsyncIterator[bytes]":
        nonlocal content_type
        try:
            reader, writer = await asyncio.open_connection(upstream_host, upstream_port)
        except (ConnectionRefusedError, OSError) as exc:
            logger.warning(
                "camera proxy: cannot connect to %s:%d (%s)",
                upstream_host, upstream_port, exc,
            )
            return

        try:
            req = (
                f"GET {upstream_path} HTTP/1.1\r\n"
                f"Host: {upstream_host}:{upstream_port}\r\n"
                f"User-Agent: pidog-web-proxy/1.0\r\n"
                f"Accept: multipart/x-mixed-replace\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            )
            writer.write(req.encode("ascii"))
            await writer.drain()

            # Read just enough to peel off the upstream's response headers.
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = await asyncio.wait_for(reader.read(1024), timeout=10.0)
                if not chunk:
                    logger.warning("camera proxy: upstream closed before headers")
                    return
                head += chunk

            # Forward the upstream's status line + headers — but rewrite the
            # Content-Type so the browser sees a real value even if the upstream
            # sent something else (e.g. image/jpeg for a single-frame response).
            header_text, _, body_prefix = head.partition(b"\r\n\r\n")
            for line in header_text.split(b"\r\n"):
                lower = line.lower()
                if lower.startswith(b"content-type:"):
                    ct = line.split(b":", 1)[1].strip().decode("latin-1", "replace")
                    # Only adopt upstream CT if it looks like multipart
                    # (some upstreams send `image/jpeg` for the first frame).
                    if "multipart" in ct.lower():
                        content_type = ct
                    break

            if body_prefix:
                yield body_prefix

            # Then keep streaming body chunks until upstream EOF or client hangs up.
            while True:
                if await request.is_disconnected():
                    logger.debug("camera proxy: client disconnected, closing upstream")
                    break
                try:
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=15.0)
                except asyncio.TimeoutError:
                    logger.warning("camera proxy: upstream read timeout, closing")
                    break
                if not chunk:
                    break
                yield chunk
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            # Client went away mid-stream — nothing to do.
            pass
        except Exception as exc:  # noqa: BLE001 — log anything unexpected
            logger.warning("camera proxy: stream error: %s", exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    return StreamingResponse(
        relay(),
        media_type=content_type,
    )