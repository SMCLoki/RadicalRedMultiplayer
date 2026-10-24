#!/usr/bin/env python3
"""Talk-to-player menu end-to-end: two clients with puppets (fake overworld), the event script the client keeps in
RAM for the faced puppet is run by tools/fake_script.py with menu answers, and the resulting trade / battle goes
through the real server. Needs `pip install lupa`; skipped otherwise.   python tests/test_talk.py
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
sys.path.insert(0, os.path.join(REPO, "tools"))

from test_client import FakeGBA, ServerThread  # noqa: E402
from test_server import make_mon  # noqa: E402

if FakeGBA is not None:
    from fake_overworld import FakeOverworld  # noqa: E402
    from fake_script import run_script  # noqa: E402

SB1PTR = 0x03005008


@unittest.skipIf(FakeGBA is None, "lupa not installed (pip install lupa)")
class TalkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.srv = ServerThread(os.path.join(self.tmp, "srv"))
        self.gbas = []

    def tearDown(self):
        for g in self.gbas:
            g.kill()
        self.srv.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def client(self, name, x, party):
        work = os.path.join(self.tmp, name)
        os.makedirs(work)
        g = FakeGBA(work, profile=1, config={"host": "127.0.0.1", "port": self.srv.port, "room": "TALK",
                                             "name": name, "pvp_engine": "manual"})
        ow = FakeOverworld(g)
        ow.define_map(3, 0, [])
        ow.load_map(3, 0, x, 5)
        g.ow = ow
        g.set_party(party)
        self.gbas.append(g)
        return g

    def step(self, n=1):
        for _ in range(n):
            for g in self.gbas:
                g.frame()
            time.sleep(0.0005)

    def until(self, cond, frames=1500, msg=""):
        for _ in range(frames):
            if cond():
                return
            self.step()
        self.fail(f"timed out: {msg}\n" + "\n".join(str(g.events()[-4:]) for g in self.gbas))

    def setup_pair(self):
        self.ma = [make_mon(0x111, species=152), make_mon(0x112, species=16)]
        self.mb = [make_mon(0x221, species=4), make_mon(0x222, species=7)]
        a, b = self.client("Red", 5, self.ma), self.client("Blue", 6, self.mb)
        self.until(lambda: a.M.visuals.ok and b.M.visuals.ok and a.M.visuals.slotPeer[0] is not None
                   and b.M.visuals.slotPeer[0] is not None, msg="visuals ready")
        for g in (a, b):           # the game spawns NPCs on camera moves
            g.ow.walk(2); self.step(16)
            g.ow.walk(1); self.step(16)
        self.until(lambda: a.ow.puppets() and b.ow.puppets(), msg="puppets")
        a.ow.face(4)               # Red faces Blue (one tile to the right)
        b.ow.face(3)               # Blue faces Red
        self.step(5)
        return a, b

    def talk(self, g, answers):
        """Press A on the faced puppet: run the script the client prepared, then let the client read the result."""
        sb1 = g.py.read(SB1PTR, 4)
        addr = g.M.visuals.scriptAddr(sb1)
        puppet = g.ow.puppets()[0]
        tpl = sb1 + 0x8E0 + (g.M.visuals.baseCount + puppet["localId"] - 0x70) * 0x18
        self.assertEqual(g.py.read(tpl + 0x10, 4), addr, "puppet template points at the talk script")
        texts = run_script(g, addr, answers, species_names={152: "CHIKORITA", 4: "CHARMANDER", 7: "SQUIRTLE"},
                           last_talked=puppet["localId"])
        self.step(5)
        return texts

    def test_trade_through_the_game_menus(self):
        a, b = self.setup_pair()
        texts = self.talk(a, {"menu": 0, "slot": 0})            # TRADE CENTER, then party screen: slot 1
        self.assertEqual(texts, ["Blue: Let's link up!\nTrade or battle?"])
        self.until(lambda: b.M.S.trade is not None and b.M.S.trade.stage == b"offered", msg="offer arrives")
        texts = self.talk(b, {"yesno": 1, "slot": 1})           # YES, give Squirtle
        self.assertEqual(texts, ["Red offers CHIKORITA!\nTrade one of yours for it?"])
        self.until(lambda: a.M.S.trade is not None and a.M.S.trade.stage == b"preview", msg="preview")
        self.step(10)
        texts = self.talk(a, {"yesno": 1})
        self.assertEqual(texts, ["Blue offers SQUIRTLE\nfor your CHIKORITA. Trade?"])
        self.until(lambda: a.party()[0] == self.mb[1] and b.party()[1] == self.ma[0], msg="trade done")

    def test_battle_through_the_game_menus(self):
        a, b = self.setup_pair()
        self.talk(a, {"menu": 1})                                 # COLOSSEUM
        self.until(lambda: b.M.S.battle is not None and b.M.S.battle.stage == b"invite", msg="invite")
        texts = self.talk(b, {"yesno": 1})
        self.assertEqual(texts, ["Red wants to battle!\nAccept the challenge?"])
        self.until(lambda: a.M.S.battle is not None and a.M.S.battle.stage == b"active"
                   and b.M.S.battle.stage == b"active", msg="battle starts")

    def test_declining_and_cancelling(self):
        a, b = self.setup_pair()
        self.talk(a, {"menu": 1})
        self.until(lambda: b.M.S.battle is not None, msg="invite")
        texts = self.talk(a, {"yesno": 1})                        # Red talks again while waiting: cancel
        self.assertEqual(texts, ["Waiting for Blue...\nCancel your request?"])
        self.until(lambda: a.M.S.battle is None and b.M.S.battle is None, msg="cancelled")
        self.talk(a, {"menu": 2})                                 # EXIT: nothing happens
        self.step(30)
        self.assertIsNone(b.M.S.battle)
        self.assertIsNone(b.M.S.trade)


if __name__ == "__main__":
    unittest.main(verbosity=2)
