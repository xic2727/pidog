"""Configuration loader for the PiDog web console.

Reads `web_server.toml` next to this package. Environment variables override
file values so a systemd unit can drop in overrides cleanly.
"""
from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "web_server.toml"
DEFAULT_MDNS_HOSTNAME = "pidog.local"


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"


@dataclass
class DaemonConfig:
    socket: str = "~/.openclaw/pidog-control/controller.sock"
    request_timeout: float = 5.0
    long_timeout: float = 15.0


@dataclass
class StatusConfig:
    ws_push_interval_ms: int = 1000


@dataclass
class CameraConfig:
    direct_mjpeg_url: str = "http://{host}:9000/mjpg"
    proxy_enabled: bool = False


@dataclass
class MdnsConfig:
    hostname: str = DEFAULT_MDNS_HOSTNAME
    enabled: bool = True


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    status: StatusConfig = field(default_factory=StatusConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    mdns: MdnsConfig = field(default_factory=MdnsConfig)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "AppConfig":
        path = path or DEFAULT_CONFIG_PATH
        data: dict = {}
        if path.is_file():
            with path.open("rb") as f:
                data = tomllib.load(f)
            logger.info("loaded config from %s", path)
        else:
            logger.info("no config file at %s, using defaults", path)

        cfg = cls()
        if "server" in data:
            cfg.server = ServerConfig(**data["server"])
        if "daemon" in data:
            cfg.daemon = DaemonConfig(**data["daemon"])
        if "status" in data:
            cfg.status = StatusConfig(**data["status"])
        if "camera" in data:
            cfg.camera = CameraConfig(**data["camera"])
        if "mdns" in data:
            cfg.mdns = MdnsConfig(**data["mdns"])

        # Env var overrides.
        if v := os.environ.get("PIDOG_WEB_HOST"):
            cfg.server.host = v
        if v := os.environ.get("PIDOG_WEB_PORT"):
            cfg.server.port = int(v)
        if v := os.environ.get("PIDOG_WEB_LOG_LEVEL"):
            cfg.server.log_level = v
        if v := os.environ.get("PIDOG_DAEMON_SOCKET"):
            cfg.daemon.socket = v
        if v := os.environ.get("PIDOG_MDNS_HOSTNAME"):
            cfg.mdns.hostname = v
        if v := os.environ.get("PIDOG_MDNS_ENABLED"):
            cfg.mdns.enabled = v.lower() in ("1", "true", "yes", "on")

        # Expand ~ in the socket path so we don't have to remember later.
        cfg.daemon.socket = os.path.expanduser(cfg.daemon.socket)
        return cfg
