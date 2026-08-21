"""Client wrapper for the `pidog-control` Unix-socket daemon.

Imports the existing `controller_request` helper from
`pidog-control/scripts/pidog_ctl.py` so we don't duplicate the wire protocol.
Adds:
  - Lazy socket path resolution (we may want to override the default at runtime)
  - Timeouts tailored to the web use case (short for actions, long for holds)
  - A small, stable error surface (DaemonError with .code) for the route layer
  - Action whitelisting (see `WHITELIST_ACTIONS` / `WHITELIST_LIGHT_MODES`)

The daemon is a single-threaded request/response server, so it serializes
hardware access. We never touch `pidog.Pidog` directly from this process.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# --- Whitelists (kept in sync with the daemon's ACTION_MAP / LIGHT_MODE_MAP) ---

# Map: web name -> daemon `cmd=action` `name` (or compound key).
# Mirrors `pidog-control/scripts/pidog_ctl.py:ACTION_MAP` + `COMPOUND_ACTIONS`.
ACTION_MAP = {
    # do_action-based
    "stand":      "stand",
    "sit":        "sit",
    "lie":        "lie",
    "wag-tail":   "wag_tail",
    "bark":       "bark",       # special: speak(single_bark_1)
    "stretch":    "stretch",
    "push-up":    "push_up",
    "forward":    "forward",
    "backward":   "backward",
    "turn-left":  "turn_left",
    "turn-right": "turn_right",
}

# Compound actions — daemon calls the matching function in pidog.preset_actions.
# (These are *not* in ACTION_MAP because the daemon handles them by function name.)
COMPOUND_ACTIONS = {
    "pant":           "pant",
    "hand-shake":     "hand_shake",
    "high-five":      "high_five",
    "scratch":        "scratch",
    "howling":        "howling",
    "body-twisting":  "body_twisting",
}

# Display name -> web action key. Anything not in this map is rejected.
ALL_ACTIONS = {**ACTION_MAP, **COMPOUND_ACTIONS}

# These three are 'hold' postures.  Clicking another hold auto-releases the previous.
HOLD_ACTIONS = {"stand", "sit", "lie"}

# Continuous movement actions (press-and-hold D-pad). These use the daemon's
# `cmd=move` worker instead of one-shot `cmd=action`.
MOVE_ACTIONS = ("forward", "backward", "turn-left", "turn-right")

# Display groups for the front-end grid layout.
ACTION_GROUPS = {
    "posture":  ("stand", "sit", "lie", "body-twisting"),
    "express":  ("wag-tail", "bark", "pant", "stretch", "hand-shake",
                 "high-five", "push-up", "scratch", "howling"),
    "move":     ("forward", "backward", "turn-left", "turn-right"),
}

# Curated color names (mirrors `pidog_ctl.py:COLOR_MAP`).
COLOR_MAP = {
    "red":    (255,   0,   0),
    "green":  (  0, 255,   0),
    "blue":   (  0,   0, 255),
    "yellow": (255, 255,   0),
    "purple": (128,   0, 255),
    "pink":   (255,   0, 128),
    "cyan":   (  0, 255, 255),
    "white":  (255, 255, 255),
    "orange": (255, 128,   0),
}

# Light modes we expose to the web UI.
# Matches `pidog-control/scripts/pidog_ctl.py:LIGHT_MODE_MAP` keys (minus `solid`).
LIGHT_MODES = ("off", "breath", "listen", "boom")


# --- Errors ---

class DaemonError(Exception):
    """A wrapper for all daemon-side failures so the route layer can map to HTTP."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict:
        return {"ok": False, "error": self.message, "code": self.code}


# --- Lazy import of the upstream controller_request() helper ---

_CONTROLLER_REQUEST = None  # resolved on first use


def _load_controller_request():
    """Import `controller_request` from `pidog-control/scripts/pidog_ctl.py`.

    We do it lazily so that the web app can still start (and report a useful
    `/api/health` response) even if the helper is not yet importable.
    """
    global _CONTROLLER_REQUEST
    if _CONTROLLER_REQUEST is not None:
        return _CONTROLLER_REQUEST

    # Allow override via env var (used by tests).
    script_path = os.environ.get(
        "PIDOG_CTL_SCRIPT",
        str(Path.home() / "pidog" / "pidog-control" / "scripts" / "pidog_ctl.py"),
    )
    p = Path(script_path)
    if not p.is_file():
        raise DaemonError(
            "DAEMON_SCRIPT_MISSING",
            f"pidog_ctl.py not found at {p}. Set PIDOG_CTL_SCRIPT or install pidog.",
        )

    spec = importlib.util.spec_from_file_location("pidog_ctl", p)
    if spec is None or spec.loader is None:
        raise DaemonError("DAEMON_SCRIPT_LOAD_FAILED", f"cannot import spec from {p}")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except SystemExit as exc:  # the script can call sys.exit on bad CLI
        raise DaemonError("DAEMON_SCRIPT_LOAD_FAILED", f"pidog_ctl.py raised SystemExit: {exc}")
    except Exception as exc:
        raise DaemonError("DAEMON_SCRIPT_LOAD_FAILED", f"importing pidog_ctl failed: {exc}")

    if not hasattr(mod, "controller_request"):
        raise DaemonError("DAEMON_SCRIPT_LOAD_FAILED", "pidog_ctl.py has no controller_request()")
    _CONTROLLER_REQUEST = mod.controller_request
    return _CONTROLLER_REQUEST


# --- Low-level socket client (also reusable directly) ---

@dataclass
class DaemonClient:
    """Thin async wrapper around the upstream synchronous `controller_request`."""

    socket_path: Path
    request_timeout: float = 5.0
    long_timeout: float = 15.0

    @classmethod
    def from_config(cls, cfg: "object") -> "DaemonClient":  # type: ignore[name-defined]
        return cls(
            socket_path=Path(os.path.expanduser(cfg.socket)),
            request_timeout=cfg.request_timeout,
            long_timeout=cfg.long_timeout,
        )

    async def request(self, payload: dict, *, long: bool = False) -> Any:
        """Send a JSON request to the daemon, return `data` (raises on error)."""
        cr = _load_controller_request()
        timeout = self.long_timeout if long else self.request_timeout
        try:
            return await asyncio.to_thread(cr, payload, timeout)
        except FileNotFoundError:
            raise DaemonError("DAEMON_DOWN", f"socket not found: {self.socket_path}")
        except (ConnectionRefusedError, OSError) as exc:
            raise DaemonError("DAEMON_DOWN", f"cannot connect to daemon: {exc}")
        except RuntimeError as exc:
            msg = str(exc)
            if "controller returned no data" in msg or "no data" in msg:
                raise DaemonError("DAEMON_DOWN", msg)
            if "timed out" in msg or "Timeout" in msg:
                raise DaemonError("TIMEOUT", msg)
            raise DaemonError("DAEMON_ERROR", msg)
        except Exception as exc:
            raise DaemonError("DAEMON_ERROR", f"unexpected: {exc}")

    # --- Convenience methods (each maps to one daemon command) ---

    async def ping(self) -> dict:
        return await self.request({"cmd": "ping"})

    async def action(self, name: str, *, speed: int = 70, hold: bool = False) -> dict:
        if name not in ALL_ACTIONS:
            raise DaemonError("UNKNOWN_ACTION", f"action '{name}' is not in the whitelist")
        return await self.request(
            {"cmd": "action", "name": name, "speed": speed, "hold": hold},
            long=hold,
        )

    async def move(self, name: str, *, speed: int = 98) -> dict:
        if name not in MOVE_ACTIONS:
            raise DaemonError("UNKNOWN_MOVE_ACTION", f"move action '{name}' is not in the whitelist")
        return await self.request({"cmd": "move", "name": name, "speed": int(speed)})

    async def light(
        self,
        mode: str,
        color: str = "white",
        *,
        brightness: Optional[float] = None,
        bps: Optional[float] = None,
    ) -> dict:
        if mode not in LIGHT_MODES:
            raise DaemonError("UNKNOWN_LIGHT_MODE", f"mode '{mode}' is not in the v1 whitelist")
        if color not in COLOR_MAP:
            raise DaemonError("UNKNOWN_COLOR", f"color '{color}' is not in the v1 whitelist")
        payload = {"cmd": "light", "mode": mode, "color": COLOR_MAP[color]}
        if brightness is not None:
            payload["brightness"] = float(brightness)
        if bps is not None:
            payload["bps"] = float(bps)
        return await self.request(payload)

    # --- Head control (yaw / roll / pitch in degrees) ---

    HEAD_LIMITS = {
        "yaw":   (-90.0, 90.0),
        "roll":  (-30.0, 30.0),
        "pitch": (-30.0, 30.0),
    }
    HEAD_DEFAULT_DELTA = 10.0
    HEAD_DEFAULT_SPEED = 50

    async def head(self, *, yaw=None, roll=None, pitch=None, speed: int = 50) -> dict:
        payload = {"cmd": "head", "speed": int(speed)}
        for axis, val in (("yaw", yaw), ("roll", roll), ("pitch", pitch)):
            if val is None: continue
            lo, hi = self.HEAD_LIMITS[axis]
            if not (lo <= float(val) <= hi):
                raise DaemonError("INVALID_HEAD_ANGLE", f"{axis}={val} out of range [{lo}, {hi}]")
            payload[axis] = float(val)
        return await self.request(payload)

    async def head_home(self, *, speed: int = 50) -> dict:
        return await self.request({"cmd": "head_home", "speed": int(speed)})

    async def head_nudge(self, axis: str, delta: float = 10.0, *, speed: int = 50) -> dict:
        if axis not in self.HEAD_LIMITS:
            raise DaemonError("INVALID_HEAD_AXIS", f"axis must be one of {list(self.HEAD_LIMITS)}")
        return await self.request(
            {"cmd": "head_nudge", "axis": axis, "delta": float(delta), "speed": int(speed)}
        )

    # --- Voice mode (ASR + LLM + TTS via companion subprocess) ---

    async def voice_set(self, on: bool) -> dict:
        return await self.request({"cmd": "voice", "on": bool(on)}, long=True)

    # --- Immediate stop (used by press-and-hold D-pad) ---

    async def stop(self, scope: str = "legs") -> dict:
        return await self.request({"cmd": "stop", "scope": scope})

    async def shutdown(self) -> dict:
        return await self.request({"cmd": "shutdown"})

    # --- Socket presence (fast check, no daemon roundtrip) ---

    def socket_exists(self) -> bool:
        return self.socket_path.exists()


# --- Whitelist serialization helpers (used by /api/actions, /api/lights/presets) ---

def list_actions() -> list[dict]:
    """All actions the front-end is allowed to dispatch, with grouping metadata."""
    out: list[dict] = []
    for name in ALL_ACTIONS:
        kind = "compound" if name in COMPOUND_ACTIONS else "do_action"
        out.append({
            "name":   name,
            "kind":   kind,
            "hold":   name in HOLD_ACTIONS,
            "group": (
                "posture" if name in ACTION_GROUPS["posture"]
                else "move"   if name in ACTION_GROUPS["move"]
                else "express"
            ),
        })
    return out


def list_light_presets() -> dict:
    return {
        "modes": [
            {"id": "off",    "label": "关"},
            {"id": "breath", "label": "呼吸"},
            {"id": "listen", "label": "聆听"},
            {"id": "boom",   "label": "Boom"},
        ],
        "colors": [
            {"id": k, "rgb": list(v)} for k, v in COLOR_MAP.items()
        ],
        "brightness": {"min": 0.0, "max": 1.0, "default": 0.8},
        "bps":        {"min": 0.1, "max": 2.0, "default": 1.0},
    }


def head_limits() -> dict:
    """Safe head RPY limits for the SPA (mirrors the daemon's checks)."""
    return {
        axis: {"min": lo, "max": hi, "default": 0.0}
        for axis, (lo, hi) in DaemonClient.HEAD_LIMITS.items()
    }


def get_local_ip() -> str:
    """Best-effort LAN IP discovery, used for the startup banner fallback."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't actually send a packet; just makes the kernel pick an interface.
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()
