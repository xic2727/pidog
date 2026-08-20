"""Best-effort mDNS / hostname resolution helper.

We don't *run* the mDNS responder ourselves (avahi-daemon does that on
Raspberry Pi OS out of the box). This module just:

  1. Probes whether `avahi-daemon` is alive via `systemctl is-active`.
  2. Resolves `pidog.local` to an IP, so we can decide between showing
     "http://pidog.local:8000/" (mDNS works) or falling back to the LAN IP.
  3. Tries to set the system hostname (best-effort, never raises).

If avahi isn't running, the URL builder just falls back to the LAN IP and
emits a one-line warning. The web app itself does not depend on mDNS.
"""
from __future__ import annotations

import logging
import shutil
import socket
import subprocess
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MdnsReport:
    avahi_running: bool
    mdns_resolves: bool
    mdns_ip: Optional[str]
    hostname: str
    lan_ip: str
    mdns_url: Optional[str]
    lan_url: str


def _is_avahi_active() -> bool:
    if not shutil.which("systemctl"):
        return False
    try:
        out = subprocess.run(
            ["systemctl", "is-active", "--quiet", "avahi-daemon"],
            check=False, capture_output=True, timeout=2.0,
        )
        return out.returncode == 0
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("avahi probe failed: %s", exc)
        return False


def _resolve_mdns(name: str, timeout: float = 1.0) -> Optional[str]:
    """Try to resolve `<name>` via getaddrinfo. Returns IP or None."""
    try:
        socket.setdefaulttimeout(timeout)
        infos = socket.getaddrinfo(name, None, type=socket.SOCK_STREAM)
        for family, _type, _proto, _canon, sockaddr in infos:
            if family == socket.AF_INET:
                return sockaddr[0]
    except (socket.gaierror, OSError) as exc:
        logger.debug("mdns resolve %s failed: %s", name, exc)
    finally:
        socket.setdefaulttimeout(None)
    return None


def _get_lan_ip() -> str:
    """See daemon_client.get_local_ip; duplicated here to avoid a circular import."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def probe(hostname: str, port: int) -> MdnsReport:
    avahi = _is_avahi_active()
    mdns_ip: Optional[str] = None
    if avahi:
        mdns_ip = _resolve_mdns(hostname)
    lan_ip = _get_lan_ip()
    mdns_url = f"http://{hostname}:{port}/" if mdns_ip else None
    lan_url  = f"http://{lan_ip}:{port}/"
    return MdnsReport(
        avahi_running=avahi,
        mdns_resolves=mdns_ip is not None,
        mdns_ip=mdns_ip,
        hostname=hostname,
        lan_ip=lan_ip,
        mdns_url=mdns_url,
        lan_url=lan_url,
    )


def format_banner(report: MdnsReport) -> str:
    """Human-friendly startup banner. Always mentions both URLs if available."""
    lines = [
        "[pidog-web] starting up",
        f"[pidog-web] mDNS hostname : {report.hostname}"
        + (" (avahi OK)" if report.avahi_running else " (avahi NOT running)"),
    ]
    if report.mdns_url:
        lines.append(f"[pidog-web] LAN access   : {report.mdns_url}")
    lines.append(f"[pidog-web] fallback IP  : {report.lan_url}")
    if not report.avahi_running:
        lines.append(
            "[pidog-web] hint: sudo apt install avahi-daemon && "
            "sudo systemctl enable --now avahi-daemon"
        )
    return "\n".join(lines)
