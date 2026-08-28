"""Sound playback routes: play arbitrary sound files from the sounds/ directory."""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["sounds"])


# Map web sound IDs -> bare sound names (without extension).
SOUND_MAP = {
    "birthday_song":    "birthday_song",
    "birthday_greeting": "birthday_greeting",
}

ALLOWED_SOUNDS = set(SOUND_MAP.keys())

# Where the sounds directory lives relative to the repo root.
_SOUNDS_DIR = Path(__file__).resolve().parents[3] / "sounds"


def _resolve_path(name: str) -> Path | None:
    """Find a sound file by bare name in the sounds/ directory."""
    for ext in (".mp3", ".wav"):
        p = _SOUNDS_DIR / f"{name}{ext}"
        if p.is_file():
            return p
    return None


# ---------------------------------------------------------------------------
# Lazy import of play_sound_threading from pidog_ctl.py
# ---------------------------------------------------------------------------

_SOUND_FUNC = None


def _load_play_sound():
    global _SOUND_FUNC
    if _SOUND_FUNC is not None:
        return _SOUND_FUNC

    script = os.environ.get(
        "PIDOG_CTL_SCRIPT",
        str(Path.home() / "pidog" / "pidog-control" / "scripts" / "pidog_ctl.py"),
    )
    p = Path(script)
    if not p.is_file():
        raise RuntimeError(f"pidog_ctl.py not found at {p}")

    spec = importlib.util.spec_from_file_location("pidog_ctl", p)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load spec from {p}")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except SystemExit as exc:
        raise RuntimeError(f"pidog_ctl.py raised SystemExit: {exc}") from exc

    if not hasattr(mod, "play_sound_threading"):
        raise RuntimeError("pidog_ctl.py has no play_sound_threading")
    _SOUND_FUNC = mod.play_sound_threading
    return _SOUND_FUNC


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class SoundRequest(BaseModel):
    sound: str = Field(..., description=f"Sound name, one of: {sorted(ALLOWED_SOUNDS)}")


class SoundResponse(BaseModel):
    ok: bool
    file: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/sound", response_model=SoundResponse, summary="Play a named sound file")
async def play_sound(req: SoundRequest) -> SoundResponse:
    """Play a sound from the sounds/ directory.

    Sounds are looked up by bare name (no extension) and resolved to either
    ``.mp3`` or ``.wav`` whichever is found first.
    """
    if req.sound not in ALLOWED_SOUNDS:
        raise HTTPException(
            status_code=400,
            detail={"ok": False, "error": f"unknown sound '{req.sound}'"},
        )

    sound_name = SOUND_MAP[req.sound]
    path = _resolve_path(sound_name)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail={"ok": False, "error": f"sound file not found for '{req.sound}'"},
        )

    try:
        play_fn = _load_play_sound()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "error": f"failed to load sound player: {exc}"},
        )

    # Run the blocking playback in a thread so we don't block the event loop.
    def blocking_play():
        try:
            play_fn(str(path))
            return {"ok": True, "file": str(path)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    result = await asyncio.to_thread(blocking_play)
    return SoundResponse(**result)
