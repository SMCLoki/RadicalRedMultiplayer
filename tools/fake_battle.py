"""A small model of the FireRed single-battle loop inside tools/mgba_mock.FakeGBA memory, for testing
client/rr_battle.lua. Follows pret/pokefirered battle_main.c where the engine depends on it:

- CB2_InitBattle (callback2 = 0x0800FD9D) starts a battle from gPlayerParty / gEnemyParty and moves the save
  blocks (MoveSaveBlocks_ResetHeap), like the real game
- HandleTurnActionSelectionState: per-battler states in gBattleCommunication[0..1] (0 send CHOOSEACTION,
  1 wait action reply, 2 wait move/switch reply, 3/4 confirm), replies read from gBattleBufferB only when the
  battler's gBattleControllerExecFlags bit is clear
- controllers run after the main function each frame; the AI (battler 1) answers in the same frame with a
  random pick; the player (battler 0) answers from a queue of test inputs
- RNG (gRngValue LCG) is advanced every frame while waiting for input, so the two clients' RNGs differ unless
  the engine re-seeds; turn order ties, AI picks and damage rolls use it
- faint -> CHOOSEPOKEMON to the fainted side outside the selection state; Cmd_switchhandleorder copies
  gBattleBufferB[b][1] when byte 0 is CHOSENMONRETURNVALUE, else uses gBattleStruct->monToSwitchIntoId
- end: gBattleOutcome, flags cleared, callback2 = savedCallback, then back to CB2_Overworld

It is a test double built from the decomp, not the real engine (no abilities, types, items or status).
"""
from __future__ import annotations

import struct

GMAIN, FLAGS, MAINF, EXEC = 0x030030F0, 0x02022B4C, 0x03004F84, 0x02023BC8
BUFA, BUFB, MONS, COMM, OUTCOME = 0x02022BC4, 0x020233C4, 0x02023BE4, 0x02023E82, 0x02023E8A
CHOSEN_ACTION, RNG, EPARTY, ECOUNT, PPARTY, PCOUNT = 0x02023D7C, 0x03005000, 0x0202402C, 0x0202402A, 0x02024284, 0x02024029
BS_PTR, BS_ADDR, SB1PTR, SB2PTR = 0x02023FE8, 0x02000010, 0x03005008, 0x0300500C
CB_OVERWORLD, CB_INIT, CB_BATTLE = 0x080565B5, 0x0800FD9D, 0x08011101
SEL, ORDER, RUN, END = 0x08014041, 0x080150A9, 0x080154A1, 0x08015A31
CHOOSEACTION, CHOOSEMOVE, CHOOSEPOKEMON, TWO, CHOSENMON = 18, 20, 22, 33, 34
SB_SIZE = 0x1000


class FakeBattle:
    def __init__(self, gba, frames_per_action=20):
        self.g = gba
        self.r, self.w = gba.py.read, gba.py.write
        self.inputs: list[tuple] = []
        self.log: list[str] = []
        self.frames_per_action = frames_per_action
        self.state = "field"
        self.home = 0
        sb1 = self.r(SB1PTR, 4) or 0x02025000            # keep the overworld's SaveBlock1 as the first home
        self.homes = [(sb1, 0x02028000), (0x0202A000, 0x0202D000)]
        self.w(SB1PTR, 4, sb1)
        self.w(SB2PTR, 4, 0x02028000)
        self.w(BS_PTR, 4, BS_ADDR)
        self.battles = 0
        gba.pre_frame.append(self.step)

    # -- helpers -----------------------------------------------------------
    def rand(self):
        v = (self.r(RNG, 4) * 0x41C64E6D + 0x6073) & 0xFFFFFFFF
        self.w(RNG, 4, v)
        return v >> 16

    def mon(self, b):
        return MONS + b * 0x58

    def bufa(self, b):
        return BUFA + b * 0x200

    def bufb(self, b):
        return BUFB + b * 0x200

    def exec_bit(self, b):
        return self.r(EXEC, 4) & (1 << b)

    def set_exec(self, b, on):
        v = self.r(EXEC, 4)
        self.w(EXEC, 4, (v | (1 << b)) if on else (v & ~(1 << b)))

    def party_base(self, b):
        return PPARTY if b == 0 else EPARTY

    def party_hex(self, b, i):
        return bytes(self.g.py.read_range(self.party_base(b) + i * 100, 100))

    def load_battler(self, b, idx):
        raw = self.party_hex(b, idx)
        m = self.mon(b)
        for off in range(0, 0x58, 4):
            self.w(m + off, 4, 0)
        species = struct.unpack_from("<H", raw, 0x20)[0]
        moves = struct.unpack_from("<4H", raw, 0x2C)
        hp, maxhp, _atk, _def, speed = struct.unpack_from("<5H", raw, 0x56)
        self.w(m, 2, species)
        self.w(m + 6, 2, speed)
        for k, mv in enumerate(moves):
            self.w(m + 0x0C + 2 * k, 2, mv)
            self.w(m + 0x24 + k, 1, 10 if mv else 0)
        self.w(m + 0x28, 2, hp)
        self.w(m + 0x2A, 1, raw[0x54])
        self.w(m + 0x2C, 2, maxhp)
        self.active[b] = idx

    def save_battler(self, b):
        """battle HP flows back into the party, as in the real game"""
        hp = self.r(self.mon(b) + 0x28, 2)
        self.w(self.party_base(b) + self.active[b] * 100 + 0x56, 2, hp)

    def alive(self, b):
        count = self.r(PCOUNT if b == 0 else ECOUNT, 1)
        out = []
        for i in range(count):
            base = self.party_base(b) + i * 100
            if self.r(base, 4) and self.r(base + 0x56, 2) > 0:
                out.append(i)
        return out

    def move_save_blocks(self):
        old = self.homes[self.home]
        self.home ^= 1
        new = self.homes[self.home]
        for src, dst in zip(old, new):
            data = self.g.py.read_range(src, SB_SIZE)
            for off in range(0, SB_SIZE, 4):
                self.w(dst + off, 4, int.from_bytes(data[off:off + 4], "little"))
                self.w(src + off, 4, 0xDEADBEEF)   # heap garbage now
        self.w(SB1PTR, 4, new[0])
        self.w(SB2PTR, 4, new[1])

    # -- frame -------------------------------------------------------------
    def step(self):
        cb2 = self.r(GMAIN + 4, 4)
        if self.state == "field" and cb2 == CB_INIT:
            self.start()
        elif self.state == "battle":
            self.main()
            self.controllers()
        elif self.state == "ending":
            self.timer -= 1
            if self.timer <= 0:
                self.w(FLAGS, 4, 0)
                self.w(GMAIN + 4, 4, self.r(GMAIN + 8, 4))   # savedCallback
                self.state, self.timer = "returning", 5
        elif self.state == "returning":
            self.timer -= 1
            if self.timer <= 0:
                self.move_save_blocks()                      # InitOverworldBgs moves them too
                self.w(GMAIN + 4, 4, CB_OVERWORLD)
                self.state = "field"

    def start(self):
        self.battles += 1
        self.move_save_blocks()
        self.active = [0, 0]
        self.load_battler(0, self.alive(0)[0])
        self.load_battler(1, 0)
        self.w(FLAGS, 4, self.r(FLAGS, 4) | 0x04)
        self.w(GMAIN + 4, 4, CB_BATTLE)
        self.w(OUTCOME, 1, 0)
        self.w(EXEC, 4, 0)
        self.begin_selection()
        self.state = "battle"
        self.log.append(f"start {self.species(0)} vs {self.species(1)}")

    def begin_selection(self):
        self.w(MAINF, 4, SEL)
        self.w(COMM, 1, 0)
        self.w(COMM + 1, 1, 0)
        self.w(COMM + 4, 1, 0)
        self.chosen = [None, None]

    def species(self, b):
        return self.r(self.mon(b), 2)

    def send(self, b, cmd):
        self.w(self.bufa(b), 1, cmd)
        self.set_exec(b, True)

    def main(self):
        f = self.r(MAINF, 4)
        if f == SEL:
            self.rand()  # an idle animation using Random() every frame: input timing changes the RNG
            done = 0
            for b in (0, 1):
                st = self.r(COMM + b, 1)
                if st == 0:
                    self.send(b, CHOOSEACTION)
                    self.w(COMM + b, 1, 1)
                elif st == 1 and not self.exec_bit(b):
                    action = self.r(self.bufb(b) + 1, 1)
                    self.w(CHOSEN_ACTION + b, 1, action)
                    if action == 0:
                        self.send(b, CHOOSEMOVE)
                        self.w(COMM + b, 1, 2)
                    elif action == 2:
                        self.send(b, CHOOSEPOKEMON)
                        self.w(COMM + b, 1, 2)
                    else:
                        self.w(COMM + b, 1, 0)
                elif st == 2 and not self.exec_bit(b):
                    if self.r(CHOSEN_ACTION + b, 1) == 0:
                        if self.r(self.bufb(b) + 1, 1) == 10:
                            self.chosen[b] = ("m", self.r(self.bufb(b) + 2, 1) & 3)
                            self.w(COMM + b, 1, 3)
                        else:
                            self.w(COMM + b, 1, 0)
                    else:
                        idx = self.r(self.bufb(b) + 1, 1)
                        if idx >= 6:
                            self.w(COMM + b, 1, 0)
                        else:
                            self.chosen[b] = ("s", idx)
                            self.w(COMM + b, 1, 3)
                elif st == 3:
                    self.w(COMM + b, 1, 4)
                elif st == 4 and not self.exec_bit(b):
                    self.w(COMM + b, 1, 5)
                if self.r(COMM + b, 1) == 5:
                    done += 1
            if done == 2:
                self.w(MAINF, 4, ORDER)
        elif f == ORDER:
            self.order = self.turn_order()
            self.w(MAINF, 4, RUN)
            self.timer = self.frames_per_action
        elif f == RUN:
            self.timer -= 1
            if self.timer > 0:
                return
            if self.order:
                self.do_action(self.order.pop(0))
                self.timer = self.frames_per_action
                if self.r(OUTCOME, 1):
                    self.finish()
                return
            self.after_turn()
        elif f == 0x08000001:  # waiting for replacement choices (battle script openpartyscreen + switchhandleorder)
            if self.r(EXEC, 4) & 3:
                return
            for b in self.replacing:
                if self.r(self.bufb(b), 1) == CHOSENMON:
                    idx = self.r(self.bufb(b) + 1, 1)
                else:
                    idx = self.r(BS_ADDR + 0x5C + b, 1)
                self.load_battler(b, idx)
                self.log.append(f"replace b{b} -> {idx} ({self.species(b)})")
            self.replacing = []
            self.begin_selection()

    def turn_order(self):
        acts = []
        for b in (0, 1):
            kind, val = self.chosen[b]
            acts.append((0 if kind == "s" else 1, b, kind, val))
        s0, s1 = self.r(self.mon(0) + 6, 2), self.r(self.mon(1) + 6, 2)
        if acts[0][0] == acts[1][0]:
            if s0 == s1:
                first = 0 if self.rand() & 1 == 0 else 1   # battler-index tie break: mirrored on the other client
            else:
                first = 0 if s0 > s1 else 1
            return [acts[first], acts[1 - first]]
        return sorted(acts)

    def do_action(self, act):
        _, b, kind, val = act
        if kind == "s":
            self.save_battler(b)
            self.load_battler(b, val)
            self.log.append(f"b{b} switches to {val} ({self.species(b)})")
            return
        if self.r(self.mon(b) + 0x28, 2) == 0:
            return
        target = 1 - b
        move = self.r(self.mon(b) + 0x0C + 2 * val, 2)
        dmg = (10 + move % 20) * (85 + self.rand() % 16) // 100
        hp = max(0, self.r(self.mon(target) + 0x28, 2) - dmg)
        self.w(self.mon(target) + 0x28, 2, hp)
        self.log.append(f"b{b} move {move} -> b{target} hp {hp}")
        if hp == 0:
            self.save_battler(target)
            if not self.alive(target):
                self.w(OUTCOME, 1, 2 if target == 0 else 1)

    def after_turn(self):
        self.save_battler(0)
        self.save_battler(1)
        fainted = [b for b in (0, 1) if self.r(self.mon(b) + 0x28, 2) == 0]
        if fainted:
            self.replacing = fainted
            self.w(MAINF, 4, 0x08000001)
            for b in fainted:
                self.send(b, CHOOSEPOKEMON)
        else:
            self.begin_selection()

    def finish(self):
        self.save_battler(0)
        self.save_battler(1)
        sb1 = self.r(SB1PTR, 4)
        if self.r(OUTCOME, 1) == 2:   # loser pays prize money (the engine must restore it)
            self.w(sb1 + 0x290, 4, max(0, self.r(sb1 + 0x290, 4) - 500))
        self.state, self.timer = "ending", 30
        self.w(MAINF, 4, END)
        self.log.append(f"outcome {self.r(OUTCOME, 1)}")

    # -- controllers (run after the main function, same frame) -------------------
    def controllers(self):
        if self.state != "battle":
            return
        if self.exec_bit(0):
            self.player()
        if self.exec_bit(1):
            self.ai()

    def player(self):
        cmd = self.r(self.bufa(0), 1)
        if not self.inputs:
            return
        kind, val = self.inputs[0]
        b0 = self.bufb(0)
        if cmd == CHOOSEACTION and kind in ("m", "s"):
            self.w(b0, 4, TWO | ((0 if kind == "m" else 2) << 8))
        elif cmd == CHOOSEMOVE and kind == "m":
            self.w(b0, 4, TWO | (10 << 8) | (val << 16) | (1 << 24))
            self.inputs.pop(0)
        elif cmd == CHOOSEPOKEMON and kind in ("s", "r"):
            self.w(b0, 4, CHOSENMON | (val << 8))
            self.inputs.pop(0)
        else:
            return
        self.set_exec(0, False)

    def ai(self):
        cmd = self.r(self.bufa(1), 1)
        b1 = self.bufb(1)
        if cmd == CHOOSEACTION:
            self.w(b1, 4, TWO | ((2 if self.rand() % 4 == 0 and len(self.alive(1)) > 1 else 0) << 8))
        elif cmd == CHOOSEMOVE:
            self.w(b1, 4, TWO | (10 << 8) | ((self.rand() & 3) << 16))
        elif cmd == CHOOSEPOKEMON:
            options = [i for i in self.alive(1) if i != self.active[1]]
            pick = options[self.rand() % len(options)] if options else 0
            self.w(BS_ADDR + 0x5C + 1, 1, pick)
            self.w(b1, 4, CHOSENMON | (pick << 8))
        self.set_exec(1, False)
