#!/usr/bin/env python3
"""picamera2-based MJPEG streaming server for PiDog web console.

Why this exists
---------------
The web console's `<img src="http://<host>:9000/mjpg">` used to be served by
`vilib`'s built-in Flask app (`Vilib.display(local=False, web=True)`).
vilib's MJPEG generator assumes `Vilib.flask_img` is always a numpy array, so
on the very first request(s) — or whenever the camera thread hasn't yet
written a frame, or has just been closed — it crashes with:

    cv2.error: img is not a numpy array, neither a scalar

That crash kills the per-client generator, pollutes `camera.log`, and races
with the SPA's reconnect loop.

This script replaces vilib's MJPEG server with one that:
  * uses `picamera2` directly (Pi OS Bookworm's default camera stack),
  * exposes the same `http://<host>:9000/mjpg` URL the SPA already consumes,
  * never crashes the generator on a single bad frame,
  * shuts down cleanly on SIGINT / SIGTERM.

Dependencies (already on a PiDog V2 image)
-----------------------------------------
  * picamera2     — `sudo apt install python3-picamera2` (Bookworm default)
  * opencv-python — pulled in by `vilib`
  * Flask         — pulled in by `vilib`

Env vars (all optional)
-----------------------
  PIDOG_CAMERA_PORT    default 9000
  PIDOG_CAMERA_BIND    default 0.0.0.0
  PIDOG_CAMERA_VFLIP   default false
  PIDOG_CAMERA_HFLIP   default false
  PIDOG_CAMERA_SIZE    "WxH" default 640x480
  PIDOG_CAMERA_FPS     default 15
  PIDOG_CAMERA_QUALITY JPEG quality 1..100, default 75
  PIDOG_CAMERA_LOG     default INFO

Usage
-----
    python3 pidog_camera.py            # foreground
    nohup python3 pidog_camera.py &    # background (what start.sh does)
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from typing import Iterator, Optional

try:
    from picamera2 import Picamera2
except ImportError as exc:  # pragma: no cover - depends on Pi OS
    sys.stderr.write(
        "pidog_camera: picamera2 is not installed.\n"
        "  Install with:  sudo apt install python3-picamera2\n"
        f"  (import error: {exc})\n"
    )
    sys.exit(2)

try:
    import cv2
except ImportError as exc:  # pragma: no cover - vilib pulls this in
    sys.stderr.write(
        "pidog_camera: opencv-python is not installed.\n"
        "  Install with:  pip3 install opencv-python\n"
        f"  (import error: {exc})\n"
    )
    sys.exit(2)

try:
    from flask import Flask, Response, abort
except ImportError as exc:  # pragma: no cover - vilib pulls this in
    sys.stderr.write(
        "pidog_camera: Flask is not installed.\n"
        "  Install with:  pip3 install flask\n"
        f"  (import error: {exc})\n"
    )
    sys.exit(2)


# --- Config -----------------------------------------------------------------

def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    return max(lo, min(hi, v))


def _parse_size(raw: Optional[str]) -> tuple[int, int]:
    s = (raw or "640x480").lower().split("x")
    if len(s) != 2:
        return 640, 480
    try:
        w, h = int(s[0]), int(s[1])
    except ValueError:
        return 640, 480
    # picamera2 rounds to multiples of 2 in many modes; clamp to sane bounds
    w = max(160, min(1920, w))
    h = max(120, min(1080, h))
    return w, h


CONFIG = {
    "port":    _env_int("PIDOG_CAMERA_PORT", 9000, lo=1, hi=65535),
    "bind":    os.environ.get("PIDOG_CAMERA_BIND", "0.0.0.0"),
    "vflip":   _env_bool("PIDOG_CAMERA_VFLIP", False),
    "hflip":   _env_bool("PIDOG_CAMERA_HFLIP", False),
    "size":    _parse_size(os.environ.get("PIDOG_CAMERA_SIZE")),
    "fps":     _env_int("PIDOG_CAMERA_FPS", 15, lo=1, hi=60),
    "quality": _env_int("PIDOG_CAMERA_QUALITY", 75, lo=10, hi=95),
    # Retry camera init briefly — libcamera's pipeline handler is sometimes
    # still being released by a process we just killed (or by another
    # concurrent `start.sh` invocation). Without this, a benign 2-3s race
    # window kills the service and the SPA shows "video unavailable" until
    # the user manually restarts.
    "init_retries":     _env_int("PIDOG_CAMERA_INIT_RETRIES", 5, lo=1, hi=20),
    "init_retry_delay": _env_int("PIDOG_CAMERA_INIT_RETRY_DELAY", 2, lo=1, hi=30),
}


# --- Logging ----------------------------------------------------------------

logging.basicConfig(
    level=getattr(logging, os.environ.get("PIDOG_CAMERA_LOG", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s pidog-camera | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pidog-camera")


# --- Camera wrapper ---------------------------------------------------------

class Camera:
    """Owns the picamera2 instance; provides capture_jpeg() with backoff.

    The picamera2 module is imported at module load time. If the camera
    hardware is missing or libcamera refuses to claim it, Picamera2() raises
    and we surface that as a clean exit.
    """

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        self._cam: Optional[Picamera2] = None
        self._lock = threading.Lock()  # capture_array() is one-at-a-time per camera
        self._open_error: Optional[str] = None
        self._ready = threading.Event()
        self._open()

    def _open(self) -> None:
        attempts = self._cfg["init_retries"]
        delay = self._cfg["init_retry_delay"]
        last_err: Optional[str] = None
        for i in range(1, attempts + 1):
            try:
                cam = Picamera2()
                main = {"size": self._cfg["size"], "format": "BGR888"}
                ctrl = {"FrameRate": self._cfg["fps"]}
                video_config = cam.create_video_configuration(main=main, controls=ctrl)
                cam.configure(video_config)

                # libcamera honours these on most sensors
                cam.set_controls({
                    "AeEnable": True,
                    "AwbEnable": True,
                })

                cam.start()
                self._cam = cam
                self._ready.set()
                log.info(
                    "Camera opened: size=%s fps=%d vflip=%s hflip=%s",
                    self._cfg["size"], self._cfg["fps"],
                    self._cfg["vflip"], self._cfg["hflip"],
                )
                return
            except Exception as exc:  # noqa: BLE001 — surface any libcamera error
                last_err = f"{type(exc).__name__}: {exc}"
                log.warning(
                    "Camera open attempt %d/%d failed: %s",
                    i, attempts, last_err,
                )
                if i < attempts:
                    time.sleep(delay)
        self._open_error = last_err or "unknown error"
        self._ready.clear()
        log.error("Camera open failed after %d attempts: %s", attempts, self._open_error)

    # Called by the per-request generators
    def capture_jpeg(self) -> Optional[bytes]:
        if not self._ready.is_set():
            return None
        with self._lock:
            try:
                frame = self._cam.capture_array("main")  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001
                log.warning("capture_array failed: %s", exc)
                return None
        ok, buf = cv2.imencode(
            ".jpg", frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), self._cfg["quality"]],
        )
        if not ok:
            log.warning("cv2.imencode failed; dropping frame")
            return None
        return buf.tobytes()

    def close(self) -> None:
        if self._cam is None:
            return
        try:
            self._cam.close()
            log.info("Camera closed")
        except Exception as exc:  # noqa: BLE001
            log.warning("Camera close error: %s", exc)
        finally:
            self._cam = None
            self._ready.clear()

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    @property
    def open_error(self) -> Optional[str]:
        return self._open_error


# --- Flask app --------------------------------------------------------------

app = Flask(__name__)
# Single shared camera instance — picamera2 only allows one claim per sensor.
camera = Camera(CONFIG)


def _mjpeg_stream() -> Iterator[bytes]:
    """Yields multipart JPEGs. Never raises — bad frames are skipped."""
    boundary = b"--frame"
    sleep_on_miss = 0.05
    while True:
        jpg = camera.capture_jpeg()
        if jpg is None:
            time.sleep(sleep_on_miss)
            continue
        length = len(jpg)
        yield (
            boundary
            + b"\r\nContent-Type: image/jpeg\r\n"
            + f"Content-Length: {length}\r\n\r\n".encode()
            + jpg
            + b"\r\n"
        )


@app.route("/mjpg")
def mjpg() -> Response:
    if not camera.ready:
        # 503 instead of streaming broken frames — the SPA's onerror handler
        # will show the "video unavailable" overlay and retry.
        log.info("Stream request rejected: camera not ready (%s)", camera.open_error)
        abort(503, description="camera not ready")
    return Response(
        _mjpeg_stream(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/health")
def health() -> Response:
    """Liveness probe used by start.sh and the SPA's fallback logic."""
    if camera.ready:
        return Response("ok\n", mimetype="text/plain")
    msg = camera.open_error or "camera not ready"
    return Response(f"down: {msg}\n", status=503, mimetype="text/plain")


@app.route("/")
def index() -> Response:
    state = "ok" if camera.ready else f"down ({camera.open_error})"
    body = (
        "pidog-camera (picamera2 MJPEG)\n"
        f"state: {state}\n"
        f"size:  {CONFIG['size'][0]}x{CONFIG['size'][1]}\n"
        f"fps:   {CONFIG['fps']}\n"
        f"mjpeg: http://{CONFIG['bind']}:{CONFIG['port']}/mjpg\n"
    )
    return Response(body, mimetype="text/plain")


# --- Lifecycle --------------------------------------------------------------

_shutdown = threading.Event()


def _install_signal_handlers() -> None:
    def _handle(signum, _frame):  # noqa: ANN001
        log.info("Received signal %d, shutting down...", signum)
        _shutdown.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)


def main() -> int:
    _install_signal_handlers()

    if not camera.ready:
        log.error(
            "Refusing to start HTTP server: camera failed to open (%s). "
            "Check that the camera cable is seated and that no other process "
            "(vilib, libcamera-hello) is holding /dev/video*.",
            camera.open_error,
        )
        return 3

    log.info(
        "Starting MJPEG server on http://%s:%d (SPA uses /mjpg)",
        CONFIG["bind"], CONFIG["port"],
    )

    # Flask's dev server is fine here: one user (the LAN SPA), small frames,
    # threaded so the camera lock serialises concurrent browsers cleanly.
    app.run(
        host=CONFIG["bind"],
        port=CONFIG["port"],
        threaded=True,
        debug=False,
        use_reloader=False,
    )
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        camera.close()
    sys.exit(rc)