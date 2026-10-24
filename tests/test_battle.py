#!/usr/bin/env python3
"""PvP battle end-to-end: real server + two rr_mp.lua clients with client/rr_battle.lua, each running a fake
FireRed battle loop (tools/fake_battle.py) on top of a fake overworld. Checks that both games play the same
battle (mirrored), replacements and switches stay in sync, forfeits end both games, a speed tie is caught as a
desync, and party/money/options/e-reader slot are restored. Needs `pip install lupa`; skipped otherwise.
    python tests/test_battle.py
"""
import os
import shutil
import struct
import sys
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
sys.path.insert(0, os.path.join(REPO, "tools"))

from test_client import FakeGBA, ServerThread  # noqa: E402

if FakeGBA is not None:
    from fake_battle import BUFA, EXEC, OUTCOME, CHOOSEACTION, CHOOSEMOVE, CHOOSEPOKEMON, SEL, MAINF, FakeBattle  # noqa: E402
    from fake_overworld import FakeOverworld  # noqa: E402


def mon(pid, species, speed, hp=30, moves=(33, 45, 52, 10)):
    raw = bytearray(100)
    struct.pack_into("<II", raw, 0, pid, 0x4242)
    struct.pack_into("<HHI", raw, 0x20, species, 0, 1000)
    struct.pack_into("<4H", raw, 0x2C, *moves)
    raw[0x54] = 20
    struct.pack_into("<7H", raw, 0x56, hp, hp, 20, 20, speed, 20, 20)
    return raw.hex()


@unittest.skipIf(FakeGBA is None, "lupa not installed (pip install lupa)")
class BattleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.srv = ServerThread(os.path.join(self.tmp, "srv"))
        self.gbas = []

    def tearDown(self):
        for g in self.gbas:
            g.kill()
        self.srv.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def client(self, name, x, party, plan=None, extra=None):
        work = os.path.join(self.tmp, name)
        os.makedirs(work)
        g = FakeGBA(work, profile=1, config={"host": "127.0.0.1", "port": self.srv.port, "room": "PVP",
                                             "name": name, "visuals": "0", **(extra or {})})
        ow = FakeOverworld(g)
        ow.define_map(3, 0, [])
        ow.load_map(3, 0, x, 5)
        g.ow = ow
        g.fb = FakeBattle(g)
        g.set_party(party)
        g.start_party = list(party)
        sb1 = g.py.read(0x03005008, 4)
        g.py.write(sb1 + 0x290, 4, 1000)
        sb2 = g.py.read(0x0300500C, 4)
        for i in range(8):
            g.py.write(sb2 + 0x4A4 + i, 1, 0x50 + i)   # some existing e-reader name bytes
        g.plan = list(plan or [])
        g.pre_frame.append(lambda g=g: self.autoplay(g))
        self.gbas.append(g)
        return g

    def autoplay(self, g):
        """The person at the keyboard: follows g.plan, else uses move 1 / first healthy replacement."""
        fb = g.fb
        if fb.state != "battle" or fb.inputs or not g.py.read(EXEC, 4) & 1:
            return
        cmd = g.py.read(BUFA, 1)
        in_select = g.py.read(MAINF, 4) == SEL
        if cmd in (CHOOSEACTION, CHOOSEMOVE) and in_select:
            fb.inputs.append(g.plan.pop(0) if g.plan else ("m", 0))
        elif cmd == CHOOSEPOKEMON and not in_select:
            options = [i for i in fb.alive(0) if i != fb.active[0]]
            fb.inputs.append(("r", options[0]))

    def step(self, n=1):
        for _ in range(n):
            for g in self.gbas:
                g.frame()
            time.sleep(0.0003)

    def until(self, cond, frames=6000, msg=""):
        for _ in range(frames):
            if cond():
                return
            self.step()
        self.fail(f"timed out: {msg}\n" + "\n".join(f"{g.M.S.status} {g.events()[-4:]} {g.fb.log[-6:]}"
                                                  for g in self.gbas))

    def start(self, a, b):
        self.until(lambda: a.M.S.status == b"online" and b.M.S.status == b"online"
                   and any(p.name == b"Blue" for p in a.M.S.peers.values()), msg="online")
        a.call("battle", "Blue")
        self.until(lambda: b.M.S.battle is not None and b.M.S.battle.stage == b"invite", msg="invite")
        b.call("fight")
        self.until(lambda: a.fb.battles == 1 and b.fb.battles == 1, msg="both games in battle")

    def finished(self, a, b):
        return all(g.M.S.battle is None and g.M.engine.cur is None for g in (a, b))

    @staticmethod
    def mirrored(log):
        out = []
        for line in log:
            line = line.replace("b0", "bX").replace("b1", "b0").replace("bX", "b1")
            out.append({"outcome 1": "outcome 2", "outcome 2": "outcome 1"}.get(line, line))
        return out

    def assert_restored(self, g):
        self.assertEqual(g.party(), g.start_party, "party restored")
        sb1, sb2 = g.py.read(0x03005008, 4), g.py.read(0x0300500C, 4)
        self.assertEqual(g.py.read(sb1 + 0x290, 4), 1000, "money restored")
        self.assertEqual([g.py.read(sb2 + 0x4A4 + i, 1) for i in range(8)], [0x50 + i for i in range(8)])
        self.assertEqual(g.py.read(sb2 + 0x15, 1) & 2, 0, "battle style option restored")
        self.assertEqual(g.py.read(0x030030F4, 4), 0x080565B5, "back on the map")

    # ------------------------------------------------------------------

    def test_full_battle_stays_in_sync(self):
        a = self.client("Red", 5, [mon(1, 152, 60), mon(2, 25, 55)])
        b = self.client("Blue", 8, [mon(3, 4, 40), mon(4, 7, 35)])
        self.start(a, b)
        sb2 = a.py.read(0x0300500C, 4)
        self.assertEqual([a.py.read(sb2 + 0x4A4 + i, 1) for i in range(4)], [0xBC, 0xE0, 0xE9, 0xD9],
                         "opponent's name 'Blue' in the e-reader trainer slot")
        self.assertEqual(a.py.read(sb2 + 0x4A1, 1), 134, "class FACILITY_CLASS_RED for a male player sprite")
        self.until(lambda: self.finished(a, b), msg="battle finished and restored")
        self.assertEqual(a.fb.log[1:], self.mirrored(b.fb.log[1:]), "both games played the same battle")
        self.assertTrue(any("replace" in l for l in a.fb.log), "a faint replacement happened")
        res = list(self.srv.server.battle_log.read())[-1]
        self.assertEqual(res["reason"], "normal")
        self.assertEqual(res["winner"], 0, "Red is faster and wins")
        for g in (a, b):
            self.assert_restored(g)

    def test_switching_stays_in_sync(self):
        a = self.client("Red", 5, [mon(1, 152, 60), mon(2, 25, 55)], plan=[("s", 1)])
        b = self.client("Blue", 8, [mon(3, 4, 40), mon(4, 7, 35)], plan=[("m", 2), ("s", 1)])
        self.start(a, b)
        self.until(lambda: self.finished(a, b), msg="finished")
        self.assertIn("b0 switches to 1 (25)", a.fb.log)
        self.assertIn("b1 switches to 1 (25)", b.fb.log)
        self.assertEqual(a.fb.log[1:], self.mirrored(b.fb.log[1:]))
        self.assertEqual(list(self.srv.server.battle_log.read())[-1]["reason"], "normal")

    def test_z_move_pressed_by_a_player_is_used_on_both_machines(self):
        # Run 8: stale buffer bytes made the opponent side use a Z-Move. Now the reply carries explicit flags, and a
        # player's own Z choice travels with the decision.
        a = self.client("Red", 5, [mon(1, 152, 60, hp=80)], plan=[("mz", 0)])
        b = self.client("Blue", 8, [mon(3, 4, 40, hp=80)])
        self.start(a, b)
        self.until(lambda: self.finished(a, b), frames=20000, msg="finished")
        self.assertEqual(list(self.srv.server.battle_log.read())[-1]["reason"], "normal")
        self.assertIn("b0 surrounded itself with its Z-Power", a.fb.log)
        self.assertIn("b1 surrounded itself with its Z-Power", b.fb.log)
        self.assertEqual(sum("Z-Power" in l for l in a.fb.log), 1, "only Red used a Z-Move, once")
        self.assertEqual(a.fb.log[1:], self.mirrored(b.fb.log[1:]))

    def test_forfeit_ends_both_games(self):
        a = self.client("Red", 5, [mon(1, 152, 60, hp=200), mon(2, 25, 55)])
        b = self.client("Blue", 8, [mon(3, 4, 40, hp=200), mon(4, 7, 35)])
        self.start(a, b)
        self.until(lambda: a.M.S.battle.turn >= 3, msg="a few turns")
        b.call("forfeit")
        self.until(lambda: self.finished(a, b), msg="both games ended")
        res = list(self.srv.server.battle_log.read())[-1]
        self.assertEqual((res["reason"], res["winner"]), ("forfeit", 0))
        self.assertEqual(a.py.read(OUTCOME, 1), 1)
        for g in (a, b):
            self.assert_restored(g)

    def control(self, extra):
        """Same long battle with one protection switched off: must end in desync, proving the fake reproduces
        the problem that protection fixes."""
        a = self.client("Red", 5, [mon(1, 152, 60, hp=150), mon(2, 25, 55, hp=150)], extra=extra)
        b = self.client("Blue", 8, [mon(3, 4, 40, hp=150), mon(4, 7, 35, hp=150)], extra=extra)
        self.start(a, b)
        self.until(lambda: self.finished(a, b), frames=20000, msg="ended")
        self.assertEqual(list(self.srv.server.battle_log.read())[-1]["reason"], "desync")

    def test_control_without_rng_lock(self):
        # VBlank Random() + side-dependent animation lengths (real mGBA run 5)
        self.control({"rng_lock": "0"})

    def test_control_without_lag_guard(self):
        # frame callback landing mid-logic on the side that lags (player-side attacker's damage calculation)
        self.control({"lag_guard": "0"})

    def test_control_without_parking(self):
        # the AI runs (and leaves prediction side effects) only on the machine where that Pokémon is the opponent
        self.control({"controller_dummy": "0"})

    def test_speed_tie_is_detected_as_desync(self):
        # Battler-index tie breaks run mirrored on the two machines (docs/phase4_battle.md, "known limit"):
        # the per-round state hash must catch it and both games must still end cleanly.
        a = self.client("Red", 5, [mon(1, 152, 50, hp=60)])
        b = self.client("Blue", 8, [mon(3, 4, 50, hp=60)])
        self.start(a, b)
        self.until(lambda: self.finished(a, b), msg="ended")
        res = list(self.srv.server.battle_log.read())[-1]
        self.assertEqual(res["reason"], "desync")
        for g in (a, b):
            self.assert_restored(g)


if __name__ == "__main__":
    unittest.main(verbosity=2)
