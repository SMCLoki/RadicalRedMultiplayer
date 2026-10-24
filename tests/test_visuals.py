#!/usr/bin/env python3
"""Player visuals end-to-end: real server + two rr_mp.lua clients in fake mGBA, each with a fake FireRed
overworld (tools/fake_overworld.py). Checks that each client draws the other as a puppet NPC that follows
their steps. Needs `pip install lupa`; skipped otherwise.   python tests/test_visuals.py
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

if FakeGBA is not None:
    from fake_overworld import FakeOverworld  # noqa: E402


@unittest.skipIf(FakeGBA is None, "lupa not installed (pip install lupa)")
class VisualsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.srv = ServerThread(os.path.join(self.tmp, "srv"))
        self.gbas = []

    def tearDown(self):
        for g in self.gbas:
            g.kill()
        self.srv.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def client(self, name, x, y, group=3, num=0, gfx=0, extra=None):
        work = os.path.join(self.tmp, name)
        os.makedirs(work)
        g = FakeGBA(work, profile=1, config={"host": "127.0.0.1", "port": self.srv.port, "room": "VIS",
                                             "name": name, **(extra or {})})
        ow = FakeOverworld(g)
        ow.define_map(3, 0, [(1, 50, 2, 2), (2, 51, 12, 3)])
        ow.define_map(3, 1, [])
        ow.load_map(group, num, x, y, gfx=gfx)
        g.ow = ow
        self.gbas.append(g)
        return g

    def step(self, n=1):
        for _ in range(n):
            for g in self.gbas:
                g.frame()
            time.sleep(0.0005)

    def until(self, cond, frames=900, msg=""):
        for _ in range(frames):
            if cond():
                return
            self.step()
        dump = "\n".join(f"{g.M.S.status}: {g.ow.puppets()} {g.events()[-3:]}" for g in self.gbas)
        self.fail(f"timed out: {msg}\n{dump}")

    def walk(self, g, d, n=1, action=None):
        for _ in range(n):
            g.ow.walk(d, action)
            self.step(16)

    def two(self):
        a = self.client("Red", 5, 5)
        b = self.client("Blue", 8, 5, gfx=7)
        self.until(lambda: all(g.M.S.status == b"online" for g in self.gbas), msg="online")
        self.until(lambda: a.M.visuals.ok and b.M.visuals.ok, msg="validated")
        return a, b

    def appear(self, a, b):
        """Both players step up and back once their positions are known: the game only spawns NPCs
        (and so puppets) when the camera moves."""
        self.until(lambda: a.M.visuals.slotPeer[0] is not None and b.M.visuals.slotPeer[0] is not None,
                   msg="peers assigned")
        for g in (a, b):
            self.walk(g, 2)
            self.walk(g, 1)

    def puppet(self, g):
        p = [o for o in g.ow.puppets() if not o["invisible"]]
        return p[0] if p else None

    # ------------------------------------------------------------------

    def test_puppet_spawns_after_a_step_and_follows_walking(self):
        a, b = self.two()
        # The game only spawns new NPCs when the camera moves, so each side takes a step first.
        self.appear(a, b)
        self.until(lambda: self.puppet(a) and self.puppet(b), msg="puppets spawned")
        pa = self.puppet(a)
        self.assertEqual((pa["x"], pa["y"], pa["localId"]), (8, 5, 0x70))
        self.assertEqual(pa["gfx"], 0, "default puppet_gfx=self uses the viewer's own sprite")
        # Blue walks 3 tiles right; Red's puppet walks there with WALK actions, not teleports
        a.ow.actions.clear()
        self.walk(b, 4, 3)
        self.until(lambda: self.puppet(a)["x"] == 11 and not self.puppet(a)["moving"], msg="followed")
        walks = [act for lid, act in a.ow.actions if lid == 0x70]
        self.assertEqual(walks.count(0x13), 3, walks)   # WALK_NORMAL_RIGHT x3
        pa = self.puppet(a)
        self.assertEqual(pa["facing"], 4)
        px, py = a.ow.player_sprite_xy()
        self.assertEqual((pa["sx"] - px, pa["sy"] - py), ((11 - 5) * 16, 0),
                         "sprite drawn where the map coords say")

    def test_turning_in_place_and_running(self):
        a, b = self.two()
        self.appear(a, b)
        self.until(lambda: self.puppet(a), msg="spawn")
        b.ow.face(1)
        self.until(lambda: self.puppet(a)["facing"] == 1, msg="face down")
        a.ow.actions.clear()
        self.walk(b, 3, 2, action=0x3F)   # PLAYER_RUN_LEFT
        self.until(lambda: self.puppet(a)["x"] == 6 and not self.puppet(a)["moving"], msg="ran")
        self.assertIn(0x1F, [act for lid, act in a.ow.actions if lid == 0x70])  # WALK_FAST_LEFT

    def test_teleport_and_leaving_the_map(self):
        a, b = self.two()
        self.appear(a, b)
        self.until(lambda: self.puppet(a), msg="spawn")
        # Blue jumps 4 tiles (e.g. a ledge chain or fly-in-place): puppet teleports
        b.ow.load_map(3, 0, 12, 7, gfx=7)
        self.until(lambda: self.puppet(a) and (self.puppet(a)["x"], self.puppet(a)["y"]) == (12, 7), msg="teleport")
        pa = self.puppet(a)
        px, py = a.ow.player_sprite_xy()
        self.assertEqual((pa["sx"] - px, pa["sy"] - py), ((12 - 5) * 16, (7 - 5) * 16))
        # Blue warps to another map: the puppet disappears and the game deletes it on Red's next step
        b.ow.load_map(3, 1, 4, 4, gfx=7)
        self.until(lambda: self.puppet(a) is None, msg="hidden")
        self.walk(a, 1)
        self.until(lambda: a.ow.puppets() == [], msg="removed by the game")

    def test_disconnect_hides_puppet_and_restores_header(self):
        a, b = self.two()
        self.appear(a, b)
        self.until(lambda: self.puppet(a), msg="spawn")
        hdr_events = a.py.read(0x02036E00, 4)
        self.assertNotEqual(hdr_events, a.ow.maps[(3, 0)], "header points at the patched MapEvents copy")
        self.assertEqual(a.py.read(hdr_events, 1), 2 + 7, "map count + 7 puppet slots")
        b.kill()
        self.gbas.remove(b)
        self.until(lambda: self.puppet(a) is None, frames=1500, msg="hidden after leave")
        a.shutdown()
        self.assertEqual(a.py.read(0x02036E00, 4), a.ow.maps[(3, 0)], "original MapEvents restored")

    def test_real_npcs_untouched(self):
        a, b = self.two()
        before = sorted((o["localId"], o["x"], o["y"]) for o in a.ow.objects() if o["localId"] < 0x70)
        self.appear(a, b)
        self.until(lambda: self.puppet(a), msg="spawn")
        self.walk(b, 4, 2)
        after = sorted((o["localId"], o["x"], o["y"]) for o in a.ow.objects() if o["localId"] < 0x70)
        self.assertEqual(before, after)
        for i in range(2):  # map templates 0-1 unchanged in SaveBlock1
            t = 0x0202552C + 0x8E0 + i * 0x18
            self.assertEqual(a.py.read(t, 1), i + 1)

    def test_validation_failure_keeps_visuals_off(self):
        a = self.client("Red", 5, 5)
        a.py.write(0x0202063C + 0x2E, 2, 9)   # player sprite no longer points back at object 0
        self.until(lambda: a.M.S.status == b"online", msg="online")
        self.step(400)
        self.assertFalse(a.M.visuals.ok)
        self.assertIn("gSprites", a.M.visuals.reason.decode())
        self.assertEqual(a.py.read(0x02036E00, 4), a.ow.maps[(3, 0)], "nothing patched")

    def test_visuals_can_be_disabled(self):
        a = self.client("Red", 5, 5, extra={"visuals": "0"})
        b = self.client("Blue", 8, 5)
        self.until(lambda: a.M.S.status == b"online" and b.M.S.status == b"online", msg="online")
        self.step(120)
        for g in (b, a, b, a):
            self.walk(g, 2)
        self.until(lambda: self.puppet(b), msg="the enabled side still sees Red")
        self.assertEqual(a.ow.puppets(), [])
        self.assertEqual(a.ow.puppets(), [])
        self.assertEqual(a.py.read(0x02036E00, 4), a.ow.maps[(3, 0)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
