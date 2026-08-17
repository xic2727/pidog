"""Dog voice sound library.

Maps semantic voice tags (e.g. 'coquettish', 'happy_bark') to built-in sound
effect files under the project 'sounds/' directory, with:

- Multi-variant random selection (e.g. confused_1/2/3.mp3)
- Per-tag cooldown to avoid sound spam on rapid repeated triggers
- Auto-registration of user-supplied variants: drop files named
  '<tag>.mp3' or '<tag>_2.mp3' into ~/.config/pidog/sounds/ and they are
  picked up automatically on the next start.
"""
import os
import re
import time
import random
import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# Project built-in sounds directory (pidog/repo root -> sounds/)
BUILTIN_SOUND_DIR = Path(__file__).resolve().parents[3] / "sounds"

# User extensible sounds directory
USER_SOUND_DIR = Path.home() / ".config" / "pidog" / "sounds"

AUDIO_SUFFIXES = {".mp3", ".wav"}

# Semantic voice tag -> built-in sound file names (variants picked randomly)
# The mute dog "speaks" exclusively through these tags.
DOG_SOUND_MAP: Dict[str, List[str]] = {
    # 撒娇 / 陶醉: relaxed pleased groaning
    "coquettish": ["woohoo.mp3"],
    "enchanted": ["woohoo.mp3"],
    # 开心 / 兴奋的叫声
    "happy_bark": ["single_bark_1.mp3", "single_bark_2.mp3"],
    "excited_bark": ["single_bark_2.mp3", "single_bark_1.mp3"],
    # 嚎叫 (回应、远处呼唤)
    "howling": ["howling.mp3"],
    # 轻声喘息哼哼 (开心、安慰主人)
    "pant": ["pant.mp3"],
    # 打呼噜 (睡着)
    "snore": ["snoring.mp3"],
    # 疑惑
    "confused": ["confused_1.mp3", "confused_2.mp3", "confused_3.mp3"],
    # 低吼 (警惕、不满)
    "growl": ["growl_1.mp3", "growl_2.mp3"],
    # 生气
    "angry": ["angry.wav"],
    # 哀鸣 (伤心、闹脾气)
    "whine": ["howling.mp3"],
}

# Stem pattern: 'tag' or 'tag_N' (variant index)
_VARIANT_RE = re.compile(r"^(?P<tag>[a-z][a-z0-9_]*?)(?:_(?P<idx>\d+))?$", re.IGNORECASE)


class SoundLibrary:
    """Registry of dog voice sounds with variant selection and cooldown."""

    def __init__(
        self,
        sound_map: Optional[Dict[str, List[str]]] = None,
        sound_dirs: Optional[List[Path]] = None,
        cooldown: float = 2.0,
    ):
        """
        :param sound_map: semantic tag -> list of sound file names (variants)
        :param sound_dirs: directories scanned for sound files (builtin first, user dir last wins)
        :param cooldown: minimum seconds between two plays of the same tag
        """
        self.sound_map = dict(sound_map or DOG_SOUND_MAP)
        dirs = [d for d in (sound_dirs or [BUILTIN_SOUND_DIR, USER_SOUND_DIR]) if d is not None]
        self.sound_dirs = dirs
        self.cooldown = cooldown
        self._lock = threading.RLock()
        # tag -> list of absolute file paths
        self._variants: Dict[str, List[str]] = {}
        # tag -> last play timestamp
        self._last_played: Dict[str, float] = {}
        self.reload()

    def reload(self):
        """(Re)scan sound directories and build the tag -> variant paths registry."""
        with self._lock:
            self._variants = {}

            # 1. Seed from the semantic map (keeps known tags even if files missing)
            for tag, names in self.sound_map.items():
                for name in names:
                    path = self._locate(name)
                    if path:
                        self._variants.setdefault(tag, []).append(str(path))

            # 2. Auto-register every audio file found in the sound directories.
            #    '<tag>.mp3' or '<tag>_2.mp3' both register under '<tag>'.
            for d in self.sound_dirs:
                try:
                    if not d.is_dir():
                        continue
                    for file in sorted(d.iterdir()):
                        if not file.is_file() or file.suffix.lower() not in AUDIO_SUFFIXES:
                            continue
                        m = _VARIANT_RE.match(file.stem)
                        if not m or not m.group("tag"):
                            continue
                        tag = m.group("tag").lower()
                        path = str(file.resolve())
                        variants = self._variants.setdefault(tag, [])
                        if path not in variants:
                            variants.append(path)
                except Exception as e:
                    logger.debug(f"Failed scanning sound dir {d}: {e}")

            for tag in list(self._variants):
                if not self._variants[tag]:
                    del self._variants[tag]

            logger.info(
                f"SoundLibrary loaded {sum(len(v) for v in self._variants.values())} "
                f"sound files across {len(self._variants)} tags from {[str(d) for d in self.sound_dirs]}."
            )

    def _locate(self, name: str) -> Optional[Path]:
        """Locate a sound file by name, checking dirs then plain path."""
        for d in self.sound_dirs:
            candidate = d / name
            if candidate.is_file():
                return candidate
        if os.path.isfile(name):
            return Path(name)
        return None

    def is_known_tag(self, tag: str) -> bool:
        with self._lock:
            return tag in self._variants

    def available_tags(self) -> List[str]:
        with self._lock:
            return sorted(self._variants.keys())

    def resolve(self, tag: str, force: bool = False) -> Optional[str]:
        """
        Resolve a semantic tag to one variant file path (random choice),
        honoring the per-tag cooldown.

        :param tag: semantic voice tag, e.g. 'coquettish'
        :param force: bypass cooldown check
        :return: absolute file path, or None if unknown tag / cooling down
        """
        if not tag:
            return None
        tag = tag.strip().lower()
        with self._lock:
            variants = self._variants.get(tag)
            if not variants:
                return None

            now = time.time()
            last = self._last_played.get(tag, 0.0)
            if not force and (now - last) < self.cooldown:
                return None

            self._last_played[tag] = now
            return random.choice(variants)
