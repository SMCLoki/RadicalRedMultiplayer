#!/usr/bin/env python3
"""End-to-end tests: real relay server + client/rr_mp.lua running in a fake mGBA (tools/mgba_mock.py).

Needs `pip install lupa`; skipped otherwise.   python tests/test_client.py
"""
import asyncio
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "server"))
sys.path.insert(0, os.path.join(REPO, "tools"))

import rr_server  # noqa: E402
from test_server import make_mon  # noqa: E402

try:
    from mgba_mock import FakeGBA  # noqa: E402
except ImportError:  # lupa missing
    FakeGBA = None


class ServerThread:
    def __init__(self, data_dir, port=0, **cfg):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.cfg = rr_server.Config(port=port, data_dir=data_dir, tick=0.05, **cfg)
        self.server = rr_server.Server(self.cfg)
        self.port = self.run(self.server.start())

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(5)

    def stop(self):
        self.run(self.server.stop())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()


@unittest.skipIf(FakeGBA is None, "lupa not installed (pip install lupa)")
class ClientTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.srv = ServerThread(os.path.join(self.tmp, "srvdata"))
        self.gbas = []

    def tearDown(self):
        for g in self.gbas:
            try:
                g.kill()
            except Exception:
                pass
        self.srv.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def gba(self, name, room="ROOM", work=None, profile=1, port=None):
        work = work or os.path.join(self.tmp, name)
        os.makedirs(work, exist_ok=True)
        g = FakeGBA(work, profile=profile,
                    config={"host": "127.0.0.1", "port": port or self.srv.port, "room": room, "name": name})
        g.set_position(3, 0, 5, 5)
        self.gbas.append(g)
        return g

    def step(self, frames=1, gbas=None):
        for _ in range(frames):
            for g in gbas or self.gbas:
                g.frame()
            time.sleep(0.0005)

    def until(self, cond, frames=600, gbas=None):
        for _ in range(frames):
            if cond():
                return True
            self.step(1, gbas)
        self.fail("condition not reached; logs:\n" + "\n".join(sum((g.logs[-8:] for g in self.gbas), [])))

    def online(self, *gbas):
        self.until(lambda: all(g.status == "online" for g in gbas))

    # ------------------------------------------------------------------

    def test_json_roundtrip(self):
        g = self.gba("Red")
        r = g.lua.eval(b"""(function()
            local j = mp and _RRMP_INSTANCE.json
            local s = j.encode({a = 1, b = j.array({1, 2.5, "x\\n\\"y"}), c = true, d = j.array({})})
            local t = j.decode(s)
            local u = j.decode('{"k":"\\\\u00e9\\\\ud83d\\\\ude00","n":-1.5e2,"z":null,"e":[]}')
            return t.a == 1 and t.b[2] == 2.5 and t.b[3] == 'x\\n"y' and t.c == true and #t.d == 0
               and u.k == "\\xc3\\xa9\\xf0\\x9f\\x98\\x80" and u.n == -150 and u.z == j.null
               and j.decode("{bad") == nil
        end)()""")
        self.assertTrue(r)

    def test_two_clients_see_each_other_and_presence(self):
        a, b = self.gba("Red"), self.gba("Blue")
        self.online(a, b)
        self.until(lambda: len(a.peers()) == 1 and len(b.peers()) == 1)
        self.assertEqual(list(a.peers().values())[0]["name"], "Blue")
        a.set_position(3, 0, 12, 8)
        self.until(lambda: (b.peers().get(a.my_id) or {}).get("state") is not None
                   and b.peers()[a.my_id]["state"].x == 12)
        st = b.peers()[a.my_id]["state"]
        self.assertEqual((st.y, st.map[1], st.map[2]), (8, 3, 0))
        self.step(70)
        self.assertIn("Red", b.panel())
        self.assertIn("HERE", b.panel())
        with open(os.path.join(self.tmp, "Blue", "mp_status_1.txt"), encoding="utf-8") as f:
            self.assertIn("Red", f.read())

    def test_eight_players_and_ninth_refused(self):
        gs = [self.gba(f"P{i}") for i in range(8)]
        self.online(*gs)
        self.until(lambda: all(len(g.peers()) == 7 for g in gs))
        ninth = self.gba("P9")
        self.until(lambda: ninth.status == "stopped")
        self.assertTrue(any("room_full" in e for e in ninth.events()))

    def test_chat(self):
        a, b = self.gba("Red"), self.gba("Blue")
        self.online(a, b)
        a.call("say", "hello there")
        self.until(lambda: "Red: hello there" in b.panel(), frames=200)

    def test_profiles_on_shared_work_dir(self):
        work = os.path.join(self.tmp, "shared")
        a = self.gba("A", work=work, profile=None)
        b = self.gba("B", work=work, profile=None)
        self.online(a, b)
        self.until(lambda: len(a.peers()) == 1)  # distinct uids -> they don't replace each other
        self.assertTrue(os.path.exists(os.path.join(work, "mp_uid_1.txt")))
        self.assertTrue(os.path.exists(os.path.join(work, "mp_uid_2.txt")))

    def setup_trade(self):
        a, b = self.gba("Red"), self.gba("Blue")
        self.ma = [make_mon(0x111, species=152), make_mon(0x112, species=16)]
        self.mb = [make_mon(0x221, species=4), make_mon(0x222, species=7, level=9)]
        a.set_party(self.ma)
        b.set_party(self.mb)
        self.online(a, b)
        self.until(lambda: len(a.peers()) == 1 and len(b.peers()) == 1)
        return a, b

    def trade_to_preview(self, a, b):
        a.call("trade", "Blue", 1)
        self.until(lambda: b.M.S.trade is not None and b.M.S.trade.stage == b"offered")
        self.assertTrue(b.call("accept", 2))
        self.until(lambda: a.M.S.trade is not None and a.M.S.trade.stage == b"preview"
                   and b.M.S.trade.stage == b"preview")

    def test_trade_end_to_end(self):
        a, b = self.setup_trade()
        self.trade_to_preview(a, b)
        a.call("confirm")
        b.call("confirm")
        self.until(lambda: a.party()[0] == self.mb[1] and b.party()[1] == self.ma[0])
        self.assertEqual(a.party()[1], self.ma[1])
        self.assertEqual(b.party()[0], self.mb[0])
        self.until(lambda: not self.srv.server.pending_commits, frames=100)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "Red", "mp_trades_1.jsonl")))

    def test_reload_old_save_reapplies_trade(self):
        a, b = self.setup_trade()
        self.trade_to_preview(a, b)
        a.call("confirm")
        b.call("confirm")
        self.until(lambda: a.party()[0] == self.mb[1])
        a.set_party(self.ma)  # player loads the save from before the trade
        self.assertEqual(a.M.reconcile(), 1)
        self.assertEqual(a.party()[0], self.mb[1])
        self.assertEqual(a.M.reconcile(), 0)

    def test_kill_before_commit_changes_nothing(self):
        a, b = self.setup_trade()
        self.trade_to_preview(a, b)
        a.call("confirm")
        self.step(5)
        a.kill()
        self.until(lambda: b.M.S.trade is None, gbas=[b])
        self.assertTrue(any("disconnect" in e for e in b.events()))
        b.call("confirm")
        self.step(30, [b])
        self.assertEqual(a.party(), self.ma)
        self.assertEqual(b.party(), self.mb)
        self.assertFalse(self.srv.server.pending_commits)

    def test_kill_after_commit_is_redelivered(self):
        a, b = self.setup_trade()
        self.trade_to_preview(a, b)
        a.call("confirm")
        self.step(10)
        b.call("confirm")
        # Blue's commit is in flight; Blue dies before processing it
        b.frame()  # flush the confirm
        self.until(lambda: a.party()[0] == self.mb[1], gbas=[a])
        b.kill()
        self.assertEqual(b.party(), self.mb)
        work_b = os.path.join(self.tmp, "Blue")
        b2 = FakeGBA(work_b, profile=1,
                     config={"host": "127.0.0.1", "port": self.srv.port, "room": "ROOM", "name": "Blue"})
        self.gbas.append(b2)
        b2.set_party(self.mb)
        b2.set_position(3, 0, 5, 5)
        self.until(lambda: b2.party()[1] == self.ma[0])
        self.until(lambda: not self.srv.server.pending_commits, frames=100)

    def test_server_restart_reconnects(self):
        a = self.gba("Red")
        self.online(a)
        port = self.srv.port
        self.srv.stop()
        self.until(lambda: a.status != "online", gbas=[a], frames=1000)
        self.srv = ServerThread(os.path.join(self.tmp, "srvdata"), port=port)
        self.until(lambda: a.status == "online", gbas=[a], frames=2000)

    def test_battle_plumbing_with_manual_actions(self):
        a, b = self.setup_trade()
        a.call("battle", "Blue")
        self.until(lambda: b.M.S.battle is not None and b.M.S.battle.stage == b"invite")
        b.call("fight")
        self.until(lambda: a.M.S.battle is not None and a.M.S.battle.stage == b"active"
                   and b.M.S.battle is not None and b.M.S.battle.stage == b"active")
        self.assertEqual(a.M.S.battle.seed, b.M.S.battle.seed)
        self.assertEqual((a.M.S.battle.side, b.M.S.battle.side), (0, 1))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "Red", "mp_party_backup_1.txt")))
        a.call("act", a.lua.table_from({b"move": 1}))
        b.call("act", b.lua.table_from({b"move": 2}))
        self.until(lambda: a.M.S.battle.turn == 2 and b.M.S.battle.turn == 2)
        b.call("forfeit")
        self.until(lambda: a.M.S.battle is None and b.M.S.battle is None)
        self.assertTrue(any("YOU WIN" in e for e in a.events()))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "Red", "mp_party_backup_1.txt")))
        self.assertEqual(a.party(), self.ma)

    def test_trade_refused_during_battle(self):
        a, b = self.setup_trade()
        a.set_battle_flags(0xC)
        a.call("trade", "Blue", 1)
        self.assertIsNone(a.M.S.trade)


if __name__ == "__main__":
    unittest.main(verbosity=2)
