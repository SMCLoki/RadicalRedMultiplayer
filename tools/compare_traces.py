#!/usr/bin/env python3
"""Compare two client/battle_trace.lua logs (one per window) and report the first battle step where the games
differ. Steps are matched by (gBattleMainFunc, gBattlescriptCurrInstr, occurrence); battler-specific fields are
mirrored (window 2's battler 0 is window 1's battler 1).

    python tools/compare_traces.py work/btrace_pilot.txt work/btrace_pilot2.txt

What the first difference means:
- rng differs at a step entered with exec=00000000  -> the RNG lock did not hold (check LAG lines around it)
- rng equal but dmg/crit/hp differ                   -> the game computes differently per side (deterministic
                                                         asymmetry: the next step is the link-battle approach)
- a step exists in only one log                      -> the battle scripts took different paths
"""
import collections
import re
import sys

LINE = re.compile(r"(\d+) (\S+) main=(\w+) instr=(\w+) exec=(\w+) rng=(\w+) dmg=(-?\d+) crit=(\d+) atk=(\d+) "
                  r"tgt=(\d+) hp0=(\d+) hp1=(\d+)")


def load(path, mirror):
    steps, occ, lags = [], collections.Counter(), 0
    for raw in open(path, encoding="utf-8", errors="replace"):
        m = LINE.match(raw.strip())
        if not m:
            continue
        frame, state, main, instr, exe, rng, dmg, crit, atk, tgt, hp0, hp1 = m.groups()
        if state == "LAG":
            lags += 1
            continue
        key = (main, instr)
        occ[key] += 1
        a, t = int(atk), int(tgt)
        if mirror:
            hp0, hp1 = hp1, hp0
            a, t = 1 - a if a < 2 else a, 1 - t if t < 2 else t
        steps.append({"key": (main, instr, occ[key]), "exec": exe, "rng": rng, "dmg": int(dmg),
                      "crit": int(crit), "atk": a, "tgt": t, "hp": (int(hp0), int(hp1)), "frame": frame})
    return steps, lags


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    s1, l1 = load(sys.argv[1], False)
    s2, l2 = load(sys.argv[2], True)
    print(f"window 1: {len(s1)} steps, {l1} lag frames; window 2: {len(s2)} steps, {l2} lag frames")
    by2 = {s["key"]: s for s in s2}
    for s in s1:
        o = by2.get(s["key"])
        if o is None:
            print(f"step {s['key']} (frame {s['frame']}) only in window 1: script paths differ")
            return
        for field in ("rng", "dmg", "crit", "hp"):
            if s[field] != o[field] and not (field == "rng" and s["exec"] != "00000000"):
                print(f"first difference at step main={s['key'][0]} instr={s['key'][1]} #{s['key'][2]} "
                      f"(frames {s['frame']} / {o['frame']}): {field} {s[field]} vs {o[field]}")
                print(f"  window 1: {s}\n  window 2: {o}")
                return
    print("no difference found in the common steps")


if __name__ == "__main__":
    main()
