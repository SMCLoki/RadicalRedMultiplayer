"""Offline tests for the launcher helpers and the UPnP client (a fake router runs on localhost)."""
import http.server
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "launcher"))

import rr_launcher  # noqa: E402
import upnp  # noqa: E402

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
        cmd = rr_launcher.server_command(9001)
        self.assertIn("--run-server", cmd)
        self.assertEqual(cmd[cmd.index("--port") + 1], "9001")
        self.assertEqual(cmd[cmd.index("--host") + 1], "0.0.0.0")


if __name__ == "__main__":
    unittest.main(verbosity=1)
