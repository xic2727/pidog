"""Status poller: pings the daemon on a fixed interval and fans out to WS clients.

Why a poller (instead of pushing from the daemon)?
  - The daemon is single-threaded and request/response. It doesn't currently
    emit unsolicited events. Adding a publish channel is a v1.1 change.
  - One `cmd=ping` per second is trivial load (the daemon just returns a dict).
  - WS clients get a single, simple, predictable message shape.

If the daemon is down, the poller still emits a heartbeat so the SPA can flip
to the "offline" banner.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Set

from fastapi import WebSocket

from web.daemon_client import DaemonClient, DaemonError

logger = logging.getLogger(__name__)


class StatusBroadcaster:
    """Holds the active WebSocket subscribers and the background poller task."""

    def __init__(self, client: DaemonClient, interval_ms: int):
        self.client = client
        self.interval_s = max(0.2, interval_ms / 1000.0)
        self._subs: Set[WebSocket] = set()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="status-poller")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None

    async def subscribe(self, ws: WebSocket) -> None:
        await ws.accept()
        self._subs.add(ws)
        logger.info("ws subscribe, total=%d", len(self._subs))
        try:
            # Send a snapshot immediately so the client doesn't wait `interval_s`.
            snapshot = await self._snapshot()
            await ws.send_json(snapshot)
            # Keep the connection alive; we just discard inbound messages.
            while not self._stop.is_set():
                try:
                    await asyncio.wait_for(ws.receive_text(), timeout=30.0)
                except asyncio.TimeoutError:
                    # Send a ping frame to keep middleboxes happy.
                    await ws.send_json({"op": "ping", "ts": time.time()})
        except Exception as exc:
            logger.debug("ws subscribe ended: %s", exc)
        finally:
            self._subs.discard(ws)
            logger.info("ws unsubscribe, total=%d", len(self._subs))

    async def _run(self) -> None:
        while not self._stop.is_set():
            snapshot = await self._snapshot()
            await self._broadcast(snapshot)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_s)
            except asyncio.TimeoutError:
                pass

    async def _snapshot(self) -> dict[str, Any]:
        ts = time.time()
        if not self.client.socket_exists():
            return {
                "ts": ts, "running": False, "daemon": "down",
                "reason": "socket missing", "uptime_s": 0.0,
            }
        try:
            data = await self.client.ping()
            return {
                "ts": ts, "running": True, "daemon": "up",
                "uptime_s": data.get("uptime_s", 0.0),
                "request_count": data.get("request_count", 0),
                "current_posture": data.get("current_posture"),
                "last_action": data.get("last_action"),
                "last_light":  data.get("last_light"),
                "last_head":   data.get("last_head"),
                "head_state":  data.get("head_state", [0, 0, 0]),
                "voice_mode":  data.get("voice_mode", False),
                "voice_pid":   data.get("voice_pid"),
                "runtime_available": data.get("runtime_available", False),
            }
        except DaemonError as exc:
            return {
                "ts": ts, "running": False, "daemon": "down",
                "reason": exc.message, "code": exc.code,
            }
        except Exception as exc:
            return {
                "ts": ts, "running": False, "daemon": "error",
                "reason": f"unexpected: {exc}",
            }

    async def _broadcast(self, msg: dict) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._subs):
            try:
                await ws.send_json(msg)
            except Exception as exc:
                logger.debug("broadcast to ws failed (%s); dropping", exc)
                dead.append(ws)
        for ws in dead:
            self._subs.discard(ws)
