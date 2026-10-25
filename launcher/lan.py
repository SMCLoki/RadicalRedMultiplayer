"""Local-network helpers for the launcher (standard library only, nothing leaves your network).

- find_servers(): broadcast a discovery probe; relay servers on the LAN answer with their port and player count.
- default_gateway(): the router's address, so the launcher can point at its admin page (UPnP / port forwarding).
- local_ip(): this PC's address on the home network (what LAN players type to join).
"""
from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import time

LAN_PORT = 8766
LAN_MAGIC = b"RRMP-DISCOVER"
LAN_PROBE_SIZE = 256   # must match rr_server.py; the server only answers probes at least this long
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def probe() -> bytes:
    return LAN_MAGIC + b" " * (LAN_PROBE_SIZE - len(LAN_MAGIC))


def find_servers(timeout: float = 1.5, port: int = LAN_PORT,
                 targets: tuple[str, ...] = ("255.255.255.255",)) -> list[dict]:
    """Return [{"ip", "port", "players", "name", ...}] for every relay server that answered, sorted by ip."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    found: dict[tuple[str, int], dict] = {}
    try:
        for t in targets:
            try:
                sock.sendto(probe(), (t, port))
            except OSError:
                pass
        end = time.monotonic() + timeout
        while (left := end - time.monotonic()) > 0:
            sock.settimeout(left)
            try:
                data, (ip, _) = sock.recvfrom(LAN_PROBE_SIZE)
            except (socket.timeout, OSError):
                break
            try:
                info = json.loads(data)
                tcp = int(info["port"])
            except (ValueError, KeyError, TypeError):
                continue
            if not isinstance(info, dict) or info.get("rrmp") != 1 or not 0 < tcp < 65536:
                continue
            info["ip"], info["port"] = ip, tcp
            info["name"] = re.sub(r"[^A-Za-z0-9_ -]", "", str(info.get("name", "")))[:32]
            found[(ip, tcp)] = info
    finally:
        sock.close()
    return [found[k] for k in sorted(found)]


def local_ip(towards: str = "192.168.0.1") -> str | None:
    """The address of the interface used to reach `towards` (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((towards, 9))
        ip = s.getsockname()[0]
        return None if ip.startswith("0.") else ip
    except OSError:
        return None
    finally:
        s.close()


def parse_route_print(text: str) -> str | None:
    """Default gateway from Windows `route print -4` output (lowest metric wins)."""
    best = None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0" and _is_ipv4(parts[2]):
            try:
                metric = int(parts[4])
            except ValueError:
                continue
            if best is None or metric < best[0]:
                best = (metric, parts[2])
    return best[1] if best else None


def parse_proc_route(text: str) -> str | None:
    """Default gateway from Linux /proc/net/route (little-endian hex)."""
    for line in text.splitlines()[1:]:
        f = line.split()
        if len(f) >= 3 and f[1] == "00000000" and f[2] != "00000000":
            return socket.inet_ntoa(int(f[2], 16).to_bytes(4, "little"))
    return None


def _is_ipv4(s: str) -> bool:
    try:
        socket.inet_aton(s)
        return s.count(".") == 3
    except OSError:
        return False


def default_gateway() -> str | None:
    try:
        if sys.platform == "win32":
            out = subprocess.run(["route", "print", "-4"], capture_output=True, text=True, timeout=5,
                                 creationflags=NO_WINDOW).stdout
            return parse_route_print(out)
        with open("/proc/net/route") as f:
            return parse_proc_route(f.read())
    except (OSError, subprocess.SubprocessError):
        return None
