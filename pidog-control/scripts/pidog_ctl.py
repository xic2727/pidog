#!/usr/bin/env python3
import argparse
import importlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

EXPECTED_DIRS = [
    Path.home() / "pidog",
    Path.home() / "robot-hat",
    Path.home() / "vilib",
]

STATE_DIR = Path.home() / ".openclaw" / "pidog-control"
HOLD_PID_FILE = STATE_DIR / "hold.pid"
DAEMON_PID_FILE = STATE_DIR / "controller.pid"
SOCKET_PATH = STATE_DIR / "controller.sock"
LOG_FILE = STATE_DIR / "controller.log"

COLOR_MAP = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "purple": (128, 0, 255),
    "pink": (255, 0, 128),
    "cyan": (0, 255, 255),
    "white": (255, 255, 255),
    "orange": (255, 128, 0),
    "black": (0, 0, 0),
    "off": (0, 0, 0),
}

ACTION_MAP = {
    "stand": "stand",
    "sit": "sit",
    "lie": "lie",
    "wag-tail": "wag_tail",
    "bark": "bark",                   # special-cased in action() via speak()
    "stretch": "stretch",             # compound
    "push-up": "push_up",             # compound
    "forward": "forward",
    "backward": "backward",
    "turn-left": "turn_left",
    "turn-right": "turn_right",
}

# Compound actions: functions in `pidog.preset_actions` taking (my_dog, …).
# The daemon calls them with just the runtime dog; each function's optional
# yrp / pitch_comp args are left at their defaults.
COMPOUND_ACTIONS = {
    "pant":           "pant",
    "hand-shake":     "hand_shake",
    "high-five":      "high_five",
    "scratch":        "scratch",
    "howling":        "howling",
    "body-twisting":  "body_twisting",
}

# Continuous movement actions for press-and-hold control (web D-pad).
# The daemon runs a worker thread that keeps queueing one walk cycle at a
# time until cmd=stop - mirrors examples/11_keyboard_control.py.
MOVE_ACTIONS = {"forward", "backward", "turn-left", "turn-right"}

DEFAULT_BARK_SOUND = "single_bark_1"

LIGHT_MODE_MAP = {
    "off": ("breath", (0, 0, 0), 0.5, 0.0),
    "solid": ("boom", None, 1.0, 0.8),
    "breath": ("breath", None, 1.0, 0.8),
    "listen": ("listen", None, 0.6, 0.8),
    "boom": ("boom", None, 1.0, 0.8),
}

EXPERIMENTAL_LIGHT_MODES = {"solid"}


class PiDogRuntime:
    def __init__(self, controller_mode=False, lazy=False):
        self.module = None
        self.cls = None
        self.instance = None
        self.import_error = None
        self.controller_mode = controller_mode
        self.lazy = lazy
        if not self.lazy:
            self._load()

    def _load(self):
        try:
            mod = importlib.import_module("pidog")
            cls = getattr(mod, "Pidog", None)
            if cls is None:
                raise AttributeError("pidog.Pidog not found")
            self.module = mod
            self.cls = cls
        except Exception as exc:
            self.import_error = exc

    @property
    def available(self):
        if self.cls is None and self.import_error is None:
            self._load()
        return self.cls is not None

    def connect(self, *, head_init_angles=None):
        if not self.available:
            raise RuntimeError(f"PiDog Python module unavailable: {self.import_error}")
        if self.instance is None:
            kwargs = {}
            if head_init_angles is not None:
                kwargs["head_init_angles"] = head_init_angles
            if self.controller_mode:
                original_sensory = getattr(self.cls, "sensory_process_start", None)
                original_close = getattr(self.cls, "close", None)

                def patched_sensory_process_start(_self):
                    _self.sensory_process = None
                    return None

                def patched_close(_self):
                    try:
                        _self.close_all_thread()
                    except Exception:
                        pass
                    try:
                        if hasattr(_self, 'dual_touch') and _self.dual_touch:
                            _self.dual_touch.close()
                    except Exception:
                        pass
                    try:
                        if hasattr(_self, 'ears') and _self.ears:
                            _self.ears.close()
                    except Exception:
                        pass
                    try:
                        if hasattr(_self, 'legs_thread'):
                            _self.legs_thread.join(timeout=1)
                    except Exception:
                        pass
                    try:
                        if hasattr(_self, 'head_thread'):
                            _self.head_thread.join(timeout=1)
                    except Exception:
                        pass
                    try:
                        if hasattr(_self, 'tail_thread'):
                            _self.tail_thread.join(timeout=1)
                    except Exception:
                        pass
                    try:
                        if hasattr(_self, 'rgb_thread_run'):
                            _self.rgb_thread_run = False
                        if hasattr(_self, 'rgb_strip_thread'):
                            _self.rgb_strip_thread.join(timeout=1)
                        if hasattr(_self, 'rgb_strip') and _self.rgb_strip:
                            _self.rgb_strip.close()
                    except Exception:
                        pass
                    try:
                        if hasattr(_self, 'imu_thread'):
                            _self.imu_thread.join(timeout=1)
                    except Exception:
                        pass

                try:
                    if original_sensory is not None:
                        setattr(self.cls, "sensory_process_start", patched_sensory_process_start)
                    if original_close is not None:
                        setattr(self.cls, "close", patched_close)
                    self.instance = self.cls(**kwargs)
                finally:
                    if original_sensory is not None:
                        setattr(self.cls, "sensory_process_start", original_sensory)
                    if original_close is not None:
                        setattr(self.cls, "close", original_close)
            else:
                self.instance = self.cls(**kwargs)
        return self.instance

    def close(self):
        if self.instance is None:
            return
        try:
            self.instance.close()
        except Exception:
            pass

    def release(self):
        self.instance = None


def parse_color(value):
    value = value.strip().lower()
    if value in COLOR_MAP:
        return COLOR_MAP[value]
    if value.startswith("#"):
        value = value[1:]
    if len(value) == 6:
        try:
            return tuple(int(value[i:i+2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
    raise argparse.ArgumentTypeError("Color must be a known name or hex like #00aaff")


def json_ready(obj):
    if isinstance(obj, tuple):
        return list(obj)
    if isinstance(obj, dict):
        return {k: json_ready(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_ready(v) for v in obj]
    return obj


def play_sound_blocking(path, volume=100, music=None):
    """Play a sound file and return a structured status.

    Returns: dict {"ok": bool, "player": str | None, "detail": str}

    Player chain:
        .wav -> aplay (ALSA)
        .mp3 -> mpg123 (preferred), then ffplay / mplayer / cvlc fallback
    SDL-based players (ffplay) get `SDL_AUDIODRIVER=alsa` so they work in
    the headless daemon context (no DBUS / PulseAudio session).
    Last resort: robot-hat Music (pygame), which often fails in headless.
    """
    path = str(path)
    if not os.path.isfile(path):
        msg = f"file not found: {path}"
        print(f"[sound] {msg}", flush=True)
        return {"ok": False, "player": None, "detail": msg}
    vol = max(0, min(100, int(volume)))
    ext = os.path.splitext(path)[1].lower()

    cmd = None
    if ext == ".wav" and shutil.which("aplay"):
        cmd = ("aplay", ["-q", path])
    elif ext == ".mp3" and shutil.which("mpg123"):
        # -f scales the output amplitude; 32768 is mpg123's default (100%).
        cmd = ("mpg123", ["-q", "-f", str(int(32768 * vol / 100)), path])
    if cmd is None:
        for player, args in (
            ("mplayer", ["-really-quiet", "-volume", str(vol), path]),
            ("ffplay",  ["-nodisp", "-autoexit", "-loglevel", "quiet", "-volume", str(vol), path]),
            ("cvlc",    ["--play-and-exit", "--quiet", path]),
        ):
            if shutil.which(player):
                cmd = (player, args)
                break

    # SDL-based players (ffplay) default to PulseAudio. In a nohup'd daemon
    # there's no DBUS session, so audio silently drops. Force ALSA.
    def _build_env(player):
        env = os.environ.copy()
        if player in {"ffplay", "mplayer"}:
            env.setdefault("SDL_AUDIODRIVER", "alsa")
        return env

    if cmd is not None:
        player_name, player_args = cmd
        full_cmd = [player_name, *player_args]
        try:
            proc = subprocess.run(
                full_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=_build_env(player_name),
            )
        except OSError as exc:
            msg = f"failed to run {player_name}: {exc}"
            print(f"[sound] {msg}", flush=True)
            return {"ok": False, "player": player_name, "detail": msg}
        if proc.returncode == 0:
            return {"ok": True, "player": player_name, "detail": ""}
        err = proc.stderr.decode(errors="replace").strip()[:300]
        msg = f"{player_name} exit={proc.returncode}: {err}" if err else f"{player_name} exit={proc.returncode}"
        print(f"[sound] {msg}", flush=True)
        return {"ok": False, "player": player_name, "detail": msg}

    # Last resort: robot-hat Music. music_play() goes through
    # pygame.mixer.music, which (unlike mixer.Sound) also handles MP3.
    if music is not None:
        try:
            music.music_play(path, volume=vol)
            return {"ok": True, "player": "pygame", "detail": ""}
        except Exception as exc:
            msg = f"pygame fallback failed: {exc}"
            print(f"[sound] {msg}", flush=True)
            return {"ok": False, "player": "pygame", "detail": msg}
    return {"ok": False, "player": None, "detail": "no audio player available"}


def play_sound_threading(path, volume=100, music=None):
    """Non-blocking wrapper around play_sound_blocking()."""
    t = threading.Thread(
        target=play_sound_blocking, args=(path, volume, music),
        daemon=True, name="Sound Play")
    t.start()
    return t


def print_expected_dirs():
    for p in EXPECTED_DIRS:
        print(f"path:{p}={'yes' if p.exists() else 'no'}")


def ensure_state_dir():
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def read_pid(path):
    try:
        return int(path.read_text().strip())
    except Exception:
        return None


def write_pid(path, pid):
    ensure_state_dir()
    path.write_text(str(pid))


def clear_pid(path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def read_hold_pid():
    return read_pid(HOLD_PID_FILE)


def write_hold_pid(pid):
    write_pid(HOLD_PID_FILE, pid)


def clear_hold_pid():
    clear_pid(HOLD_PID_FILE)


def read_daemon_pid():
    return read_pid(DAEMON_PID_FILE)


def write_daemon_pid(pid):
    write_pid(DAEMON_PID_FILE, pid)


def clear_daemon_pid():
    clear_pid(DAEMON_PID_FILE)


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def daemon_running():
    pid = read_daemon_pid()
    return bool(pid and pid_alive(pid))


def stop_previous_hold(timeout=5.0):
    pid = read_hold_pid()
    if not pid:
        return False, "no previous hold pid"
    if pid == os.getpid():
        return False, "current process already owns hold pid"
    if not pid_alive(pid):
        clear_hold_pid()
        return False, "stale hold pid removed"
    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_alive(pid):
            clear_hold_pid()
            return True, f"stopped previous hold pid {pid}"
        time.sleep(0.1)
    return False, f"previous hold pid {pid} did not exit after SIGTERM"


class PiDogController:
    def __init__(self):
        self.runtime = PiDogRuntime(controller_mode=True, lazy=True)
        self.started_at = time.time()
        self.request_count = 0
        self.current_posture = None
        self.last_action = None
        self.last_light = None
        self.last_head = None
        self.head_state = [0, 0, 0]   # [yaw, roll, pitch] last sent to hardware
        self.voice_mode = False
        self.voice_pid = None
        self.should_stop = False
        self.server = None
        # RLock: action() runs under this lock and (via _stop_move) re-enters
        # it to flush the legs buffer.
        self.lock = threading.RLock()
        # press-and-hold continuous movement worker
        self.move_lock = threading.Lock()
        self.move_thread = None
        self.move_name = None
        self.move_stop_flag = False

    def close(self):
        self._stop_move()
        self.runtime.close()
        if self.server is not None:
            try:
                self.server.close()
            except Exception:
                pass
        try:
            SOCKET_PATH.unlink()
        except FileNotFoundError:
            pass
        clear_daemon_pid()

    def _connect(self):
        return self.runtime.connect()

    def action(self, name, speed=60, hold=False):
        dog = self._connect()
        # Stop any continuous movement first so gait cycles queued by the
        # movement worker do not mix with the requested action.
        if self._stop_move():
            try:
                dog.legs_stop()
            except Exception:
                pass

        # Result of the most recent sound playback attempt. Surfaced to the
        # web client so silent failures (missing player, SDL/PulseAudio in a
        # headless daemon) are visible instead of being swallowed.
        self.last_sound_result = None

        # Helper to resolve absolute sound file paths so playback never fails
        def resolve_sound(sound_name):
            candidates = [
                Path.home() / "pidog" / "sounds" / f"{sound_name}.mp3",
                Path.home() / "pidog" / "sounds" / f"{sound_name}.wav",
                Path(__file__).resolve().parents[2] / "sounds" / f"{sound_name}.mp3",
                Path(__file__).resolve().parents[2] / "sounds" / f"{sound_name}.wav",
            ]
            for cand in candidates:
                if cand.is_file():
                    return str(cand)
            return sound_name

        if name == "bark":
            sound_target = resolve_sound(DEFAULT_BARK_SOUND)
            if not os.path.isfile(sound_target):
                raise RuntimeError(f"PiDog sound '{DEFAULT_BARK_SOUND}' was not found in the installed sounds directory")
            sound_result = play_sound_blocking(sound_target, music=getattr(dog, "music", None))
            self.last_sound_result = sound_result
            self.last_action = {"name": name, "speed": speed, "hold": hold, "sound": DEFAULT_BARK_SOUND}
            return {
                "message": f"action 'bark' via play_sound('{DEFAULT_BARK_SOUND}')",
                "sound": sound_result,
            }

        # Compound actions: call the function from pidog.preset_actions
        # with the runtime dog instance. Monkey patch speak so preset
        # functions find their sound files and use the daemon-safe player
        # chain (external players first, pygame as fallback). Playback is
        # synchronous so the response can include a real sound status.
        if name in COMPOUND_ACTIONS:
            try:
                import pidog.preset_actions as _pa
            except Exception as exc:
                raise RuntimeError(f"failed to import pidog.preset_actions: {exc}")
            func = getattr(_pa, COMPOUND_ACTIONS[name], None)
            if func is None:
                raise RuntimeError(f"compound action '{name}' -> {COMPOUND_ACTIONS[name]} not found in pidog.preset_actions")

            orig_speak = dog.speak
            music = getattr(dog, "music", None)
            last_result = {"value": None}
            def patched_speak(sound_name, volume=100):
                result = play_sound_blocking(resolve_sound(sound_name), volume=volume, music=music)
                last_result["value"] = result
                return result["ok"]
            dog.speak = patched_speak
            try:
                func(dog)
            finally:
                dog.speak = orig_speak
            self.last_sound_result = last_result["value"]

            self.current_posture = name if hold and name in {"stand", "sit", "lie"} else None
            self.last_action = {"name": name, "kind": "compound", "mapped": COMPOUND_ACTIONS[name], "speed": speed, "hold": hold}
            return {
                "message": f"compound action '{name}' ok via preset_actions.{COMPOUND_ACTIONS[name]}()",
                "hold": hold,
                "posture": self.current_posture,
                "dispatched": True,
                "sound": last_result["value"],
            }

        action_name = ACTION_MAP[name]
        dog.do_action(action_name, speed=speed)
        self.current_posture = name if hold and name in {"stand", "sit", "lie"} else None
        self.last_action = {"name": name, "mapped": action_name, "speed": speed, "hold": hold}
        return {
            "message": f"action '{name}' dispatched via do_action('{action_name}')",
            "hold": hold,
            "posture": self.current_posture,
            "dispatched": True,
        }

    # --- continuous movement (press-and-hold, cf. examples/11_keyboard_control.py) ---

    def move_start(self, name, speed=98):
        """Start continuous walking in the given direction.

        Mirrors examples/11_keyboard_control.py: a worker thread keeps queueing
        one walk cycle at a time - but only once the legs finished the previous
        cycle - so the gait never stutters. Holding the button walks; sending
        cmd=stop halts the dog almost immediately.
        """
        if name not in MOVE_ACTIONS:
            raise RuntimeError(f"unknown move action '{name}' (expected one of {sorted(MOVE_ACTIONS)})")
        self._stop_move()
        dog = self._connect()
        with self.lock:
            self.move_stop_flag = False
            self.move_name = name
            self.move_thread = threading.Thread(
                target=self._move_loop, args=(ACTION_MAP[name], int(speed)),
                daemon=True, name=f"move-{name}")
            self.move_thread.start()
        self.current_posture = None
        self.last_action = {"name": name, "mapped": ACTION_MAP[name], "speed": int(speed), "hold": False, "continuous": True}
        return {
            "message": f"continuous move '{name}' started at speed {speed}; send cmd=stop to halt",
            "name": name,
            "speed": int(speed),
            "dispatched": True,
        }

    def _move_loop(self, action_name, speed):
        try:
            while not self.move_stop_flag:
                dog = self._connect()
                with self.lock:
                    if self.move_stop_flag:
                        break
                    if dog.is_legs_done():
                        dog.do_action(action_name, speed=speed)
                time.sleep(0.05)
        except Exception as exc:
            print(f"[move] '{action_name}' worker error: {exc}", flush=True)

    def _stop_move(self, join_timeout=0.5):
        """Stop the movement worker. Returns True if one was running."""
        with self.move_lock:
            self.move_stop_flag = True
            thread = self.move_thread
            was_active = thread is not None
            if thread is not None and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=join_timeout)
            self.move_thread = None
            self.move_name = None
        return was_active

    def light(self, mode, color, bps=None, brightness=None):
        dog = self._connect()
        mode_name, default_color, default_bps, default_brightness = LIGHT_MODE_MAP[mode]
        color = default_color if default_color is not None else color
        bps = bps if bps is not None else default_bps
        brightness = brightness if brightness is not None else default_brightness
        dog.rgb_strip.set_mode(mode_name, color=color, bps=bps, brightness=brightness)
        self.last_light = {
            "mode": mode,
            "mapped": mode_name,
            "color": list(color),
            "bps": bps,
            "brightness": brightness,
        }
        data = {
            "message": f"light '{mode}' ok via rgb_strip.set_mode('{mode_name}', color={list(color)}, bps={bps}, brightness={brightness})",
            "mode": mode,
            "mapped": mode_name,
            "color": list(color),
            "bps": bps,
            "brightness": brightness,
        }
        if mode in EXPERIMENTAL_LIGHT_MODES:
            data["note"] = "experimental mode mapped to tested 'boom' effect"
        return data

    def stop(self, scope="legs"):
        """Halt ongoing movement.

        Stops the continuous-movement worker first (press-and-hold D-pad),
        then clears the servo action buffers so the dog halts immediately
        instead of finishing every queued gait cycle.
        """
        self._stop_move()
        with self.lock:
            dog = self._connect()
            if scope == "body":
                dog.body_stop()
                msg = "body stopped (legs + head + tail)"
            else:
                dog.legs_stop()
                msg = "legs stopped"
        return {"message": msg, "scope": scope}

    def head(self, yaw=None, roll=None, pitch=None, speed=50):
        """Move head to absolute [yaw, roll, pitch] in degrees. None = keep current.

        We track `self.head_state` so subsequent partial updates are incremental.
        """
        if self.voice_mode:
            raise RuntimeError("VOICE_MODE_ACTIVE: actions are paused while voice mode is on")
        if not -90 <= (yaw   or 0) <= 90:  raise RuntimeError("yaw out of range [-90, 90]")
        if not -30 <= (roll  or 0) <= 30:  raise RuntimeError("roll out of range [-30, 30]")
        if not -30 <= (pitch or 0) <= 30:  raise RuntimeError("pitch out of range [-30, 30]")
        if yaw   is not None: self.head_state[0] = float(yaw)
        if roll  is not None: self.head_state[1] = float(roll)
        if pitch is not None: self.head_state[2] = float(pitch)
        dog = self._connect()
        dog.head_move([self.head_state], immediately=True, speed=speed)
        self.last_head = {
            "yaw": self.head_state[0],
            "roll": self.head_state[1],
            "pitch": self.head_state[2],
            "speed": speed,
        }
        return {
            "message": f"head moved to yaw={self.head_state[0]} roll={self.head_state[1]} pitch={self.head_state[2]}",
            "yaw":   self.head_state[0],
            "roll":  self.head_state[1],
            "pitch": self.head_state[2],
        }

    def head_home(self, speed=50):
        return self.head(yaw=0, roll=0, pitch=0, speed=speed)

    def head_nudge(self, axis, delta, speed=50, max_abs=90):
        """Relative head move (used by the D-pad). `axis` is 'yaw'|'roll'|'pitch'."""
        idx = {"yaw": 0, "roll": 1, "pitch": 2}[axis]
        new_val = max(-max_abs, min(max_abs, self.head_state[idx] + float(delta)))
        kwargs = {"speed": speed, axis: new_val}
        return self.head(**kwargs)

    def voice_set(self, on: bool):
        """Start/stop the voice companion subprocess (ASR + LLM + TTS).

        When voice is ON, the daemon refuses hardware actions because the spawned
        orchestrator owns the Pidog runtime. Turning voice OFF kills the subprocess
        and re-enables normal control.

        NOTE: this assumes the standard install layout under $HOME/pidog and looks
        for `examples/21_embodied_pet_companion.py`. Override with env var
        PIDOG_VOICE_SCRIPT.
        """
        # Reap any previous child if it has exited.
        if self.voice_pid is not None:
            if not pid_alive(self.voice_pid):
                self.voice_pid = None
                self.voice_mode = False
        if bool(on) == self.voice_mode:
            return {
                "voice_mode": self.voice_mode,
                "voice_pid":  self.voice_pid,
                "changed":    False,
                "message":    f"voice mode already {'on' if self.voice_mode else 'off'}",
            }
        if on:
            # The voice companion subprocess takes over the hardware.
            self._stop_move()
            script = os.environ.get(
                "PIDOG_VOICE_SCRIPT",
                str(Path.home() / "pidog" / "examples" / "21_embodied_pet_companion.py"),
            )
            if not os.path.isfile(script):
                raise RuntimeError(f"voice script not found: {script}")
            log_path = STATE_DIR / "voice.log"
            log_fh = open(log_path, "ab", buffering=0)
            proc = subprocess.Popen(
                [sys.executable, script],
                stdout=log_fh, stderr=subprocess.STDOUT,
                cwd=str(Path(script).parent.parent),  # ~/pidog
                env=os.environ.copy(),
                start_new_session=True,
            )
            self.voice_pid = proc.pid
            self.voice_mode = True
            return {
                "voice_mode": True,
                "voice_pid":  proc.pid,
                "changed":    True,
                "message":    f"voice mode on (pid={proc.pid}, log={log_path})",
            }
        else:
            pid = self.voice_pid
            if pid is not None:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
                # Give it a moment, then SIGKILL if still alive.
                for _ in range(20):
                    if not pid_alive(pid): break
                    time.sleep(0.1)
                if pid_alive(pid):
                    try: os.killpg(os.getpgid(pid), signal.SIGKILL)
                    except ProcessLookupError: pass
            self.voice_pid = None
            self.voice_mode = False
            return {
                "voice_mode": False,
                "voice_pid":  None,
                "changed":    pid is not None,
                "message":    f"voice mode off (was pid={pid})" if pid else "voice mode off",
            }

    def status(self):
        # If we spawned voice, reflect that it died in our state.
        if self.voice_pid is not None and not pid_alive(self.voice_pid):
            self.voice_pid = None
            self.voice_mode = False
        return {
            "running": True,
            "pid": os.getpid(),
            "socket": str(SOCKET_PATH),
            "uptime_s": round(time.time() - self.started_at, 1),
            "request_count": self.request_count,
            "runtime_available": self.runtime.available,
            "import_error": None if self.runtime.available else str(self.runtime.import_error),
            "connected": self.runtime.instance is not None,
            "current_posture": self.current_posture,
            "moving": self.move_name,
            "last_action": self.last_action,
            "last_light": self.last_light,
            "last_head": self.last_head,
            "head_state": list(self.head_state),
            "voice_mode": self.voice_mode,
            "voice_pid": self.voice_pid,
        }

    def shutdown(self):
        # If voice is on, kill it first so the orchestrator doesn't outlive us.
        if self.voice_mode:
            try: self.voice_set(False)
            except Exception: pass
        self.should_stop = True
        return {"message": "shutdown requested"}

    def handle(self, req):
        cmd = req.get("cmd")
        self.request_count += 1
        if cmd == "ping":
            return self.status()

        # Movement control runs a worker thread that takes the hardware lock
        # for every gait step, so it must not be dispatched while holding it
        # (we would block on join() for up to the join timeout).
        if cmd == "move":
            if self.voice_mode:
                raise RuntimeError("VOICE_MODE_ACTIVE: actions are paused while voice mode is on")
            return self.move_start(req.get("name"), speed=req.get("speed", 98))
        if cmd == "stop":
            return self.stop(scope=req.get("scope", "legs"))

        # Non-ping commands acquire hardware lock to prevent thread/servo conflict
        with self.lock:
            if cmd == "action":
                if self.voice_mode:
                    raise RuntimeError("VOICE_MODE_ACTIVE: actions are paused while voice mode is on")
                return self.action(req["name"], speed=req.get("speed", 60), hold=req.get("hold", False))
            if cmd == "light":
                if self.voice_mode:
                    raise RuntimeError("VOICE_MODE_ACTIVE: lights are paused while voice mode is on")
                return self.light(
                    req["mode"],
                    tuple(req.get("color", COLOR_MAP["white"])),
                    bps=req.get("bps"),
                    brightness=req.get("brightness"),
                )
            if cmd == "head":
                return self.head(
                    yaw=req.get("yaw"), roll=req.get("roll"), pitch=req.get("pitch"),
                    speed=req.get("speed", 50),
                )
            if cmd == "head_home":
                return self.head_home(speed=req.get("speed", 50))
            if cmd == "head_nudge":
                return self.head_nudge(
                    axis=req["axis"], delta=req.get("delta", 10),
                    speed=req.get("speed", 50),
                )
            if cmd == "voice":
                return self.voice_set(bool(req.get("on", False)))
            if cmd == "shutdown":
                return self.shutdown()
            raise RuntimeError(f"unknown controller command: {cmd}")

    def serve(self):
        ensure_state_dir()
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(SOCKET_PATH))
        os.chmod(SOCKET_PATH, 0o600)
        self.server.listen(4)
        write_daemon_pid(os.getpid())

        def _handle_signal(signum, frame):
            self.should_stop = True

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        def _handle_client(conn):
            with conn:
                try:
                    raw = conn.recv(65536)
                    req = json.loads(raw.decode("utf-8"))
                    resp = {"ok": True, "data": json_ready(self.handle(req))}
                except Exception as exc:
                    resp = {"ok": False, "error": str(exc)}
                try:
                    conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
                except Exception:
                    pass

        while not self.should_stop:
            try:
                self.server.settimeout(1.0)
                conn, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=_handle_client, args=(conn,), daemon=True).start()
        self.close()


def controller_request(payload, timeout=5.0):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(SOCKET_PATH))
        sock.sendall(json.dumps(json_ready(payload)).encode("utf-8"))
        raw = sock.recv(65536)
    finally:
        sock.close()
    if not raw:
        raise RuntimeError("controller returned no data")
    resp = json.loads(raw.decode("utf-8"))
    if not resp.get("ok"):
        raise RuntimeError(resp.get("error", "controller request failed"))
    return resp["data"]


def wait_for_controller(timeout=8.0):
    deadline = time.time() + timeout
    last_error = "controller did not start"
    while time.time() < deadline:
        if SOCKET_PATH.exists():
            try:
                return controller_request({"cmd": "ping"}, timeout=1.0)
            except Exception as exc:
                last_error = str(exc)
        time.sleep(0.2)
    raise RuntimeError(last_error)


def start_controller_process(force_restart=False):
    ensure_state_dir()
    if daemon_running():
        if force_restart:
            stop_controller_process(timeout=8.0)
        else:
            return {"started": False, "message": "controller already running", "pid": read_daemon_pid()}

    try:
        SOCKET_PATH.unlink()
    except FileNotFoundError:
        pass

    with LOG_FILE.open("ab") as log_handle:
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "serve"],
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    status = wait_for_controller()
    return {
        "started": True,
        "message": "controller started",
        "pid": status["pid"],
        "socket": status["socket"],
        "spawn_pid": proc.pid,
    }


def stop_controller_process(timeout=8.0):
    pid = read_daemon_pid()
    if not pid:
        return {"stopped": False, "message": "controller not running"}
    if not pid_alive(pid):
        clear_daemon_pid()
        try:
            SOCKET_PATH.unlink()
        except FileNotFoundError:
            pass
        return {"stopped": False, "message": f"stale controller pid {pid} removed"}

    try:
        result = controller_request({"cmd": "shutdown"}, timeout=2.0)
    except Exception:
        os.kill(pid, signal.SIGTERM)
        result = {"message": "SIGTERM sent to controller"}

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_alive(pid):
            clear_daemon_pid()
            try:
                SOCKET_PATH.unlink()
            except FileNotFoundError:
                pass
            return {"stopped": True, "message": result["message"], "pid": pid}
        time.sleep(0.1)
    return {"stopped": False, "message": f"controller pid {pid} did not exit", "pid": pid}


def print_json_or_text(data):
    print(json.dumps(json_ready(data), ensure_ascii=False, indent=2, sort_keys=True))


def cmd_status(args):
    runtime = PiDogRuntime()
    data = {
        "python": sys.executable,
        "expected_dirs": {str(p): p.exists() for p in EXPECTED_DIRS},
        "runtime_available": runtime.available,
        "import_error": None if runtime.available else str(runtime.import_error),
        "tools": {cmd: bool(shutil.which(cmd)) for cmd in ["espeak", "pico2wave", "aplay"]},
        "controller_pid": read_daemon_pid(),
        "controller_socket": str(SOCKET_PATH),
        "controller_socket_exists": SOCKET_PATH.exists(),
    }
    if daemon_running():
        try:
            data["controller"] = controller_request({"cmd": "ping"}, timeout=1.0)
        except Exception as exc:
            data["controller_error"] = str(exc)
    print_json_or_text(data)


def cmd_safe_test(args):
    runtime = PiDogRuntime()
    if not runtime.available:
        raise SystemExit(f"PiDog runtime unavailable: {runtime.import_error}")
    dog = runtime.connect()
    print("Running safe test: stand -> sit")
    try:
        dog.do_action("stand", speed=60)
        dog.wait_all_done()
        print("stand: ok via do_action('stand')")
        dog.do_action("sit", speed=60)
        dog.wait_all_done()
        print("sit: ok via do_action('sit')")
    finally:
        runtime.close()


def cmd_action(args):
    if daemon_running() and not args.direct:
        payload = {"cmd": "action", "name": args.name, "speed": args.speed, "hold": args.hold}
        print_json_or_text(controller_request(payload))
        return

    if args.hold:
        stopped, note = stop_previous_hold()
        print(f"hold-switch: {note}")
    runtime = PiDogRuntime()
    if not runtime.available:
        raise SystemExit(f"PiDog runtime unavailable: {runtime.import_error}")
    dog = runtime.connect()
    try:
        if args.name == "bark":
            result = dog.speak(DEFAULT_BARK_SOUND)
            if result is False:
                raise SystemExit(f"PiDog sound '{DEFAULT_BARK_SOUND}' was not found in the installed sounds directory")
            print(f"action 'bark' ok via speak('{DEFAULT_BARK_SOUND}')")
            if args.hold:
                print("hold: bark has no persistent posture to keep; leaving runtime active briefly is unnecessary")
            return
        action_name = ACTION_MAP[args.name]
        dog.do_action(action_name, speed=args.speed)
        dog.wait_all_done()
        print(f"action '{args.name}' ok via do_action('{action_name}')")
        if args.hold:
            write_hold_pid(os.getpid())
            print(f"hold: keeping PiDog in '{args.name}' posture; registered hold pid {os.getpid()}")
            runtime.release()
            return
    finally:
        runtime.close()
        if args.hold and read_hold_pid() == os.getpid():
            clear_hold_pid()


def cmd_light(args):
    if daemon_running() and not args.direct:
        payload = {
            "cmd": "light",
            "mode": args.mode,
            "color": list(args.color),
            "bps": args.bps,
            "brightness": args.brightness,
        }
        print_json_or_text(controller_request(payload))
        return

    runtime = PiDogRuntime()
    if not runtime.available:
        raise SystemExit(f"PiDog runtime unavailable: {runtime.import_error}")
    dog = runtime.connect()
    try:
        mode_name, default_color, default_bps, default_brightness = LIGHT_MODE_MAP[args.mode]
        color = default_color if default_color is not None else args.color
        bps = args.bps if args.bps is not None else default_bps
        brightness = args.brightness if args.brightness is not None else default_brightness
        dog.rgb_strip.set_mode(mode_name, color=color, bps=bps, brightness=brightness)
        print(
            f"light '{args.mode}' ok via rgb_strip.set_mode('{mode_name}', color={list(color)}, bps={bps}, brightness={brightness})"
        )
        if args.mode in EXPERIMENTAL_LIGHT_MODES:
            print("note: this light mode is experimental on current PiDog releases and is implemented via the tested 'boom' RGB mode, not a true steady solid mode")
    finally:
        runtime.close()


def cmd_say(args):
    runtime = PiDogRuntime()
    if runtime.available:
        dog = runtime.connect()
        try:
            result = dog.speak(args.name, volume=args.volume)
            if result is False:
                raise SystemExit(f"PiDog sound '{args.name}' was not found in the installed sounds directory")
            print(f"spoken via dog.speak('{args.name}', volume={args.volume})")
            return
        finally:
            runtime.close()

    if shutil.which("espeak"):
        subprocess.run(["espeak", args.name], check=True)
        print("spoken via espeak fallback")
        return

    if shutil.which("pico2wave") and shutil.which("aplay"):
        wav = "/tmp/pidog_tts.wav"
        subprocess.run(["pico2wave", "-w", wav, args.name], check=True)
        subprocess.run(["aplay", wav], check=True)
        print("spoken via pico2wave fallback")
        return

    print(args.name)
    raise SystemExit("No PiDog runtime or local TTS backend found")


def cmd_demo(args):
    path = Path(args.path).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"Demo file not found: {path}")
    if path.suffix != ".py":
        raise SystemExit("Demo path must be a .py file")
    proc = subprocess.run([sys.executable, str(path)])
    raise SystemExit(proc.returncode)


def cmd_start(args):
    print_json_or_text(start_controller_process(force_restart=args.force))


def cmd_stop(args):
    print_json_or_text(stop_controller_process())


def cmd_send_action(args):
    print_json_or_text(
        controller_request({"cmd": "action", "name": args.name, "speed": args.speed, "hold": args.hold})
    )


def cmd_send_light(args):
    print_json_or_text(
        controller_request(
            {
                "cmd": "light",
                "mode": args.mode,
                "color": list(args.color),
                "bps": args.bps,
                "brightness": args.brightness,
            }
        )
    )


def cmd_serve(args):
    controller = PiDogController()
    if not controller.runtime.available:
        raise SystemExit(f"PiDog runtime unavailable: {controller.runtime.import_error}")
    controller.serve()


def main():
    parser = argparse.ArgumentParser(description="Generic PiDog V2 control helper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status", help="Inspect PiDog runtime and controller status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("start", help="Start the persistent PiDog controller")
    p.add_argument("--force", action="store_true", help="Restart the controller if already running")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("stop", help="Stop the persistent PiDog controller")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("send-action", help="Send an action to the persistent controller")
    p.add_argument("name", choices=["stand", "sit", "lie", "bark", "wag-tail", "forward", "backward", "turn-left", "turn-right"])
    p.add_argument("--speed", type=int, default=60)
    p.add_argument("--hold", action="store_true")
    p.set_defaults(func=cmd_send_action)

    p = sub.add_parser("send-light", help="Send a light command to the persistent controller")
    p.add_argument("mode", choices=["off", "solid", "breath", "listen", "boom"])
    p.add_argument("--color", type=parse_color, default=COLOR_MAP["white"])
    p.add_argument("--bps", type=float)
    p.add_argument("--brightness", type=float)
    p.set_defaults(func=cmd_send_light)

    p = sub.add_parser("serve", help="Run the persistent PiDog controller server")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("safe-test", help="Run a minimal stand/sit motion test")
    p.set_defaults(func=cmd_safe_test)

    p = sub.add_parser("action", help="Run a named basic action (uses controller if running unless --direct)")
    p.add_argument("name", choices=["stand", "sit", "lie", "bark", "wag-tail", "forward", "backward", "turn-left", "turn-right"])
    p.add_argument("--speed", type=int, default=60)
    p.add_argument("--hold", action="store_true", help="Keep the resulting posture instead of calling close() at the end")
    p.add_argument("--direct", action="store_true", help="Bypass the persistent controller and talk to hardware directly")
    p.set_defaults(func=cmd_action)

    p = sub.add_parser("light", help="Control the light board (uses controller if running unless --direct)")
    p.add_argument("mode", choices=["off", "solid", "breath", "listen", "boom"])
    p.add_argument("--color", type=parse_color, default=COLOR_MAP["white"])
    p.add_argument("--bps", type=float)
    p.add_argument("--brightness", type=float)
    p.add_argument("--direct", action="store_true", help="Bypass the persistent controller and talk to hardware directly")
    p.set_defaults(func=cmd_light)

    p = sub.add_parser("say", help="Play a PiDog sound name or fallback local TTS")
    p.add_argument("name")
    p.add_argument("--volume", type=int, default=80)
    p.set_defaults(func=cmd_say)

    p = sub.add_parser("demo", help="Run an installed PiDog demo .py file")
    p.add_argument("path")
    p.set_defaults(func=cmd_demo)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
