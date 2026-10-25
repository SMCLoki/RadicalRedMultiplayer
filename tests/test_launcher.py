"""Offline tests for the launcher helpers and the UPnP client (a fake router runs on localhost)."""
import http.server
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "launcher"))
sys.path.insert(0, str(ROOT / "server"))

import asyncio  # noqa: E402
import lan  # noqa: E402
import rr_launcher  # noqa: E402
import rr_server  # noqa: E402
import upnp  # noqa: E402

ROUTE_PRINT = """===========================================================================
IPv4 Route Table
===========================================================================
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0         10.0.0.1     10.0.0.199     35
          0.0.0.0          0.0.0.0      192.168.56.1  192.168.56.10    281
        127.0.0.0        255.0.0.0         On-link         127.0.0.1    331
===========================================================================
"""
PROC_ROUTE = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask
eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000
eth0\t0001A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF
"""

DESC = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0"><device>
 <deviceType>urn:schemas-upnp-org:device:InternetGatewayDevice:1</deviceType>
 <deviceList><device><deviceList><device><serviceList>
  <service><serviceType>urn:schemas-upnp-org:service:WANCommonInterfaceConfig:1</serviceType>
   <controlURL>/ctl/CommonIfCfg</controlURL></service>
  <service><serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType>
   <controlURL>/ctl/IPConn</controlURL></service>
 </serviceList></device></deviceList></device></deviceList>
</device></root>"""


class FakeRouter(http.server.BaseHTTPRequestHandler):
    calls = []
    fail_add = False

    def log_message(self, *a):
        pass

    def do_GET(self):
        self._reply(200, DESC)

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        action = self.headers["SOAPAction"].strip('"').split("#")[1]
        FakeRouter.calls.append((self.path, action, body))
        st = "urn:schemas-upnp-org:service:WANIPConnection:1"
        if action == "AddPortMapping" and FakeRouter.fail_add:
            self._reply(500, '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><s:Fault>'
                             '<detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0"><errorCode>718</errorCode>'
                             '<errorDescription>ConflictInMappingEntry</errorDescription></UPnPError></detail>'
                             '</s:Fault></s:Body></s:Envelope>')
            return
        extra = "<NewExternalIPAddress>203.0.113.7</NewExternalIPAddress>" if action == "GetExternalIPAddress" else ""
        self._reply(200, f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
                         f'<u:{action}Response xmlns:u="{st}">{extra}</u:{action}Response></s:Body></s:Envelope>')

    def _reply(self, code, text):
        data = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class UpnpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), FakeRouter)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.loc = f"http://127.0.0.1:{cls.srv.server_port}/rootDesc.xml"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        FakeRouter.calls.clear()
        FakeRouter.fail_add = False

    def test_ssdp_location(self):
        resp = "HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=120\r\nLocation: http://192.168.1.1:5000/desc.xml\r\n\r\n"
        self.assertEqual(upnp.parse_ssdp_location(resp), "http://192.168.1.1:5000/desc.xml")
        self.assertIsNone(upnp.parse_ssdp_location("HTTP/1.1 200 OK\r\n\r\n"))

    def test_description_picks_wan_ip_service(self):
        url, st = upnp.parse_description(DESC, "http://192.168.1.1:5000/rootDesc.xml")
        self.assertEqual(url, "http://192.168.1.1:5000/ctl/IPConn")
        self.assertIn("WANIPConnection", st)

    def test_mapping_round_trip(self):
        url, st = upnp.parse_description(DESC, self.loc)
        gw = upnp.Gateway(url, st)
        gw.add_mapping(8765, "Radical Red Online 8765")
        self.assertEqual(gw.external_ip(), "203.0.113.7")
        gw.delete_mapping(8765)
        actions = [c[1] for c in FakeRouter.calls]
        self.assertEqual(actions, ["AddPortMapping", "GetExternalIPAddress", "DeletePortMapping"])
        add_body = FakeRouter.calls[0][2]
        self.assertIn("<NewExternalPort>8765</NewExternalPort>", add_body)
        self.assertIn("<NewProtocol>TCP</NewProtocol>", add_body)
        self.assertIn("<NewInternalClient>127.0.0.1</NewInternalClient>", add_body)

    def test_fault_raises(self):
        FakeRouter.fail_add = True
        url, st = upnp.parse_description(DESC, self.loc)
        with self.assertRaises(upnp.UpnpError) as cm:
            upnp.Gateway(url, st).add_mapping(8765, "x")
        self.assertIn("718", str(cm.exception))

    def test_private_addresses(self):
        for ip in ("10.0.0.1", "192.168.1.5", "172.20.1.1", "100.100.1.1", "127.0.0.1", "garbage"):
            self.assertTrue(upnp.is_private(ip), ip)
        for ip in ("203.0.113.7", "8.8.8.8", "172.32.0.1", "100.128.0.1"):
            self.assertFalse(upnp.is_private(ip), ip)


class LauncherHelperTests(unittest.TestCase):
    def test_parse_address(self):
        self.assertEqual(rr_launcher.parse_address("1.2.3.4"), ("1.2.3.4", 8765))
        self.assertEqual(rr_launcher.parse_address(" host.example:9000 "), ("host.example", 9000))
        for bad in ("", ":80", "1.2.3.4:0", "1.2.3.4:abc"):
            with self.assertRaises(ValueError):
                rr_launcher.parse_address(bad)

    def test_check_rom(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "rr.gba"
            head = bytearray(0xC0)
            head[0xA0:0xAC] = b"POKEMON FIRE"
            head[0xAC:0xB0] = b"BPRE"
            p.write_bytes(bytes(head) + bytes(32 * 1024 * 1024 - 0xC0))
            ok, msg = rr_launcher.check_rom(str(p))
            self.assertTrue(ok, msg)
            self.assertIn("CRC32", msg)
            p.write_bytes(bytes(head) + bytes(16 * 1024 * 1024 - 0xC0))
            self.assertFalse(rr_launcher.check_rom(str(p))[0])      # unpatched size
            head[0xAC:0xB0] = b"BPEE"
            p.write_bytes(bytes(head))
            self.assertFalse(rr_launcher.check_rom(str(p))[0])      # Emerald
            self.assertFalse(rr_launcher.check_rom(str(Path(d) / "missing.gba"))[0])

    def test_backup_save_keeps_latest(self):
        with tempfile.TemporaryDirectory() as d:
            old_work = rr_launcher.WORK
            rr_launcher.WORK = Path(d) / "work"
            try:
                rom = Path(d) / "rr.gba"
                rom.write_bytes(b"x")
                self.assertIsNone(rr_launcher.backup_save(str(rom)))
                Path(d, "rr.sav").write_bytes(b"save")
                dest = rr_launcher.backup_save(str(rom), keep=2)
                self.assertEqual(Path(dest).read_bytes(), b"save")
                for i in range(3):
                    (rr_launcher.WORK / "save_backups" / f"rr-2000010{i}-000000.sav").write_bytes(b"o")
                rr_launcher.backup_save(str(rom), keep=2)
                self.assertEqual(len(list((rr_launcher.WORK / "save_backups").glob("rr-*.sav"))), 2)
            finally:
                rr_launcher.WORK = old_work

    def test_server_command_runs_this_file(self):
        cmd = rr_launcher.server_command(9001, "Ash")
        self.assertIn("--run-server", cmd)
        self.assertEqual(cmd[cmd.index("--port") + 1], "9001")
        self.assertEqual(cmd[cmd.index("--host") + 1], "0.0.0.0")
        self.assertEqual(cmd[cmd.index("--lan-name") + 1], "Ash")

    def test_hosting_advice_is_fully_local(self):
        cases = [
            dict(external="203.0.113.7"),                    # UPnP worked, public address
            dict(external="100.70.1.2"),                     # carrier-grade NAT
            dict(external="192.168.0.20"),                   # double router
            dict(error="no UPnP router answered"),           # UPnP off (the user's gateway, run 15)
        ]
        for kw in cases:
            text = rr_launcher.hosting_advice(8765, "10.0.0.199", "10.0.0.1", **kw)
            for word in ("tailscale", "vpn", "zerotier", "hamachi", "ngrok", "playit"):
                self.assertNotIn(word, text.lower())
            self.assertIn("10.0.0.199", text)                # LAN play is always offered
        self.assertIn("join with:  203.0.113.7", rr_launcher.hosting_advice(8765, "10.0.0.199", "10.0.0.1",
                                                                         external="203.0.113.7"))
        self.assertIn("203.0.113.7:9000", rr_launcher.hosting_advice(9000, None, None, external="203.0.113.7"))
        self.assertIn("carrier-grade NAT", rr_launcher.hosting_advice(8765, None, None, external="100.70.1.2"))
        self.assertIn("bridge mode", rr_launcher.hosting_advice(8765, None, None, external="192.168.0.20"))
        off = rr_launcher.hosting_advice(8765, "10.0.0.199", "10.0.0.1", error="no UPnP router answered")
        self.assertIn("http://10.0.0.1", off)
        self.assertIn("forward TCP port 8765 to 10.0.0.199", off)


class LanTests(unittest.TestCase):
    def test_gateway_parsers(self):
        self.assertEqual(lan.parse_route_print(ROUTE_PRINT), "10.0.0.1")
        self.assertIsNone(lan.parse_route_print("no routes here"))
        self.assertEqual(lan.parse_proc_route(PROC_ROUTE), "192.168.1.1")

    def test_discovery_finds_a_running_server(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                server = rr_server.Server(rr_server.Config(port=0, data_dir=tmp, lan_port=0))
                server.host_name = "Ash"
                await server.start()
                try:
                    loop = asyncio.get_running_loop()
                    found = await loop.run_in_executor(None, lambda: lan.find_servers(
                        timeout=0.5, port=server.cfg.lan_port, targets=("127.0.0.1",)))
                    # a short probe gets no answer (the beacon never sends more than it receives)
                    import socket
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.settimeout(0.3)
                    s.sendto(lan.LAN_MAGIC, ("127.0.0.1", server.cfg.lan_port))
                    with self.assertRaises(socket.timeout):
                        s.recvfrom(512)
                    s.close()
                    return found, server.cfg.port
                finally:
                    await server.stop()
        found, port = asyncio.run(scenario())
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["ip"], found[0]["port"], found[0]["name"], found[0]["players"]),
                         ("127.0.0.1", port, "Ash", 0))

    def test_probe_matches_server(self):
        self.assertEqual((lan.LAN_MAGIC, lan.LAN_PROBE_SIZE, lan.LAN_PORT),
                         (rr_server.LAN_MAGIC, rr_server.LAN_PROBE_SIZE, rr_server.LAN_PORT))
        self.assertEqual(len(lan.probe()), lan.LAN_PROBE_SIZE)


if __name__ == "__main__":
    unittest.main(verbosity=1)
