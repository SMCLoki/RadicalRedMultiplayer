"""Minimal UPnP IGD client (standard library only): ask the home router to forward a TCP port to this PC.

Used by the launcher's Host button so friends can reach the relay server without manual port forwarding.
Everything here is best effort: routers with UPnP turned off simply don't answer, and the launcher falls back to
showing port-forward / Tailscale instructions.
"""
from __future__ import annotations

import socket
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

SSDP_ADDR = ("239.255.255.250", 1900)
SEARCH_TARGETS = (
    "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
    "urn:schemas-upnp-org:service:WANIPConnection:1",
    "urn:schemas-upnp-org:service:WANPPPConnection:1",
    "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
    "urn:schemas-upnp-org:service:WANIPConnection:2",
)
WAN_SERVICES = ("WANIPConnection", "WANPPPConnection")


class UpnpError(Exception):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def discover(timeout: float = 2.5) -> list[str]:
    """Return the description URLs (LOCATION headers) of the gateways that answered an SSDP search."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(timeout)
    try:
        for st in SEARCH_TARGETS:
            msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
                   f"MX: 2\r\nST: {st}\r\n\r\n")
            sock.sendto(msg.encode(), SSDP_ADDR)
        found: list[str] = []
        while True:
            try:
                data, _ = sock.recvfrom(4096)
            except (socket.timeout, OSError):
                break
            loc = parse_ssdp_location(data.decode("latin-1", "replace"))
            if loc and loc not in found:
                found.append(loc)
        return found
    finally:
        sock.close()


def parse_ssdp_location(response: str) -> str | None:
    for line in response.split("\r\n"):
        k, sep, v = line.partition(":")
        if sep and k.strip().lower() == "location":
            return v.strip()
    return None


def parse_description(xml_text: str, location: str) -> tuple[str, str] | None:
    """From a gateway description, return (control URL, service type) of its WAN IP/PPP connection service."""
    root = ET.fromstring(xml_text)
    base = location
    for el in root.iter():
        if _local(el.tag) == "URLBase" and el.text and el.text.strip():
            base = el.text.strip()
    for svc in root.iter():
        if _local(svc.tag) != "service":
            continue
        fields = {_local(c.tag): (c.text or "").strip() for c in svc}
        stype = fields.get("serviceType", "")
        if any(w in stype for w in WAN_SERVICES) and fields.get("controlURL"):
            return urllib.parse.urljoin(base, fields["controlURL"]), stype
    return None


def soap_envelope(service_type: str, action: str, args: dict[str, str]) -> bytes:
    body = "".join(f"<{k}>{_xml_escape(str(v))}</{k}>" for k, v in args.items())
    return (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action} xmlns:u="{service_type}">{body}</u:{action}>'
        "</s:Body></s:Envelope>"
    ).encode()


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def parse_soap_response(xml_text: str) -> dict[str, str]:
    root = ET.fromstring(xml_text)
    out: dict[str, str] = {}
    for el in root.iter():
        if _local(el.tag) == "Fault":
            codes = [c.text for c in el.iter() if _local(c.tag) in ("errorCode", "errorDescription")]
            raise UpnpError("router refused: " + " ".join(c or "" for c in codes))
    for el in root.iter():
        if _local(el.tag).endswith("Response"):
            for c in el:
                out[_local(c.tag)] = (c.text or "").strip()
    return out


@dataclass
class Gateway:
    control_url: str
    service_type: str

    def call(self, action: str, args: dict[str, str] | None = None, timeout: float = 5.0) -> dict[str, str]:
        req = urllib.request.Request(
            self.control_url, data=soap_envelope(self.service_type, action, args or {}),
            headers={"Content-Type": 'text/xml; charset="utf-8"',
                     "SOAPAction": f'"{self.service_type}#{action}"'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return parse_soap_response(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:   # SOAP faults arrive as HTTP 500 with a body
            return parse_soap_response(e.read().decode("utf-8", "replace"))

    def local_ip(self) -> str:
        """The address of this PC on the router's network (the interface that reaches the router)."""
        host = urllib.parse.urlparse(self.control_url).hostname or "192.168.1.1"
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((host, 1900))
            return s.getsockname()[0]
        finally:
            s.close()

    def external_ip(self) -> str:
        return self.call("GetExternalIPAddress").get("NewExternalIPAddress", "")

    def add_mapping(self, port: int, description: str, lease: int = 0) -> None:
        self.call("AddPortMapping", {
            "NewRemoteHost": "", "NewExternalPort": str(port), "NewProtocol": "TCP",
            "NewInternalPort": str(port), "NewInternalClient": self.local_ip(), "NewEnabled": "1",
            "NewPortMappingDescription": description, "NewLeaseDuration": str(lease)})

    def delete_mapping(self, port: int) -> None:
        self.call("DeletePortMapping", {"NewRemoteHost": "", "NewExternalPort": str(port), "NewProtocol": "TCP"})


def find_gateway(timeout: float = 2.5) -> Gateway:
    errors = []
    for loc in discover(timeout):
        try:
            with urllib.request.urlopen(loc, timeout=5) as resp:
                found = parse_description(resp.read().decode("utf-8", "replace"), loc)
            if found:
                return Gateway(*found)
        except Exception as e:  # noqa: BLE001 - try the next answer
            errors.append(f"{loc}: {e}")
    raise UpnpError("no UPnP router answered" + (" (" + "; ".join(errors) + ")" if errors else ""))


def is_private(ip: str) -> bool:
    """True for addresses that are not reachable from the internet (CGNAT 100.64/10 included)."""
    try:
        a, b, *_ = (int(x) for x in ip.split("."))
    except ValueError:
        return True
    return a == 10 or a == 127 or (a == 172 and 16 <= b < 32) or (a == 192 and b == 168) or \
        (a == 100 and 64 <= b < 128) or a == 0
