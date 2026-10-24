#!/usr/bin/env python3
"""Compare two client/battle_trace.lua logs (one per window) and say where and how the two games first differ.

    python tools/compare_traces.py work/btrace_pilot.txt work/btrace_pilot2.txt

Steps are matched by (gBattleMainFunc, gBattlescriptCurrInstr, occurrence). Window 2 is mirrored (its battler 0
is window 1's battler 1). Reported, in this order:
1. battle data (gBattleMons) differences at turn starts, field by field - e.g. ability, types, stat stages;
2. the first step (present in both logs) where damage, crit multiplier or HP differ, with whether the random value
   was equal there (equal rng + different damage = the game computes differently per side);
3. the first step entered with exec=0 where the random value differs (the RNG lock did not hold);
4. the first step that exists in only one log (the battle scripts took different paths).
crit is CFRU's gCritMultiplier: 10 = normal hit, 15 = critical.
"""
import collections
import re
import sys

STEP = re.compile(r"(\d+) (\S+) main=(\w+) instr=(\w+) exec=(\w+) rng=(\w+) dmg=(-?\d+) crit=(\d+) atk=(\d+) "
                  r"tgt=(\d+) hp0=(\d+) hp1=(\d+)")
MONS = re.compile(r"(\d+) MONS b0=([0-9A-F]+) b1=([0-9A-F]+)")

# struct BattlePokemon (pokefirered include/battle.h; CFRU keeps the layout)
FIELDS = [(0x00, 2, "species"), (0x02, 2, "attack"), (0x04, 2, "defense"), (0x06, 2, "speed"),
          (0x08, 2, "spAttack"), (0x0A, 2, "spDefense"), (0x0C, 8, "moves"), (0x14, 4, "ivs"),
          (0x18, 8, "statStages"), (0x20, 1, "ability"), (0x21, 1, "type1"), (0x22, 1, "type2"),
          (0x23, 1, "byte23"), (0x24, 4, "pp"), (0x28, 2, "hp"), (0x2A, 1, "level"), (0x2B, 1, "friendship"),
          (0x2C, 2, "maxHP"), (0x2E, 2, "item"), (0x30, 11, "nickname"), (0x3B, 1, "ppBonuses"),
          (0x3C, 8, "otName"), (0x44, 4, "experience"), (0x48, 4, "personality"), (0x4C, 4, "status1"),
          (0x50, 4, "status2"), (0x54, 4, "otId")]


def load(path, mirror):
    steps, mons, occ, lags = [], [], collections.Counter(), 0
    for raw in open(path, encoding="utf-8", errors="replace"):
        line = raw.strip()
        m = MONS.match(line)
        if m:
            b0, b1 = bytes.fromhex(m.group(2)), bytes.fromhex(m.group(3))
            mons.append((m.group(1), (b1, b0) if mirror else (b0, b1)))
            continue
        m = STEP.match(line)
        if not m:
            continue
        frame, state, main, instr, exe, rng, dmg, crit, atk, tgt, hp0, hp1 = m.groups()
        if state == "LAG":
            lags += 1
            continue
        key = (main, instr)
        occ[key] += 1
        hp = (int(hp1), int(hp0)) if mirror else (int(hp0), int(hp1))
        a = int(atk)
        if mirror and a < 2:
            a = 1 - a
        steps.append({"key": (main, instr, occ[key]), "exec": exe, "rng": rng, "dmg": int(dmg),
                      "crit": int(crit), "atk": a, "hp": hp, "frame": frame})
    return steps, mons, lags


def mon_diff(a, b):
    out = []
    for off, size, name in FIELDS:
        if a[off:off + size] != b[off:off + size]:
            out.append(f"{name} {a[off:off + size].hex()} vs {b[off:off + size].hex()}")
    return out


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    s1, m1, l1 = load(sys.argv[1], False)
    s2, m2, l2 = load(sys.argv[2], True)
    print(f"window 1: {len(s1)} steps, {len(m1)} turn dumps, {l1} lag frames; "
          f"window 2: {len(s2)} steps, {len(m2)} turn dumps, {l2} lag frames")

    found = False
    for i, ((f1, (a0, a1)), (f2, (b0, b1))) in enumerate(zip(m1, m2)):
        for side, x, y in (("side 0's Pokemon (window 1 battler 0)", a0, b0),
                           ("side 1's Pokemon (window 1 battler 1)", a1, b1)):
            d = mon_diff(x, y)
            if d:
                print(f"turn start #{i + 1} (frames {f1}/{f2}): battle data of {side} differs: " + "; ".join(d))
                found = True
        if found:
            break
    if not found and m1:
        print("battle data of both Pokemon identical at every common turn start")

    by2 = {s["key"]: s for s in s2}
    common = [(s, by2[s["key"]]) for s in s1 if s["key"] in by2]
    first_val = next(((s, o) for s, o in common
                      if s["dmg"] != o["dmg"] or s["crit"] != o["crit"] or s["hp"] != o["hp"]), None)
    if first_val:
        s, o = first_val
        what = [f for f in ("dmg", "crit", "hp") if s[f] != o[f]]
        same_rng = s["rng"] == o["rng"]
        print(f"first damage/HP difference at main={s['key'][0]} instr={s['key'][1]} #{s['key'][2]} "
              f"(frames {s['frame']}/{o['frame']}): " + ", ".join(f"{f} {s[f]} vs {o[f]}" for f in what)
              + f"; attacker = side {s['atk']}; rng {'EQUAL' if same_rng else 'different'} ({s['rng']} / {o['rng']})")
        if same_rng:
            print("  -> same random value, different result: the game computes this differently per side")
    else:
        print("no damage/HP difference in the common steps")

    first_rng = next(((s, o) for s, o in common if s["exec"] == "00000000" and s["rng"] != o["rng"]), None)
    if first_rng:
        s, o = first_rng
        print(f"first random-value difference at an exec=0 step: main={s['key'][0]} instr={s['key'][1]} "
              f"#{s['key'][2]} (frames {s['frame']}/{o['frame']}): {s['rng']} vs {o['rng']} -> the RNG lock did not hold")
    else:
        print("random value equal at every common exec=0 step (RNG lock held)")

    only1 = next((s for s in s1 if s["key"] not in by2), None)
    keys1 = {s["key"] for s in s1}
    only2 = next((s for s in s2 if s["key"] not in keys1), None)
    for label, s in (("window 1", only1), ("window 2", only2)):
        if s:
            print(f"first step only in {label}: main={s['key'][0]} instr={s['key'][1]} #{s['key'][2]} "
                  f"(frame {s['frame']}): script paths differ from here")


if __name__ == "__main__":
    main()
