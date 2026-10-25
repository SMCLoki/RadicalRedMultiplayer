"""Interpreter for the subset of FireRed's event-script bytecode that client/rr_mp.lua's talk-to-player scripts
use (opcodes from pokefirered data/script_cmd_table.inc). Stands in for the game's script engine in tests: it
runs the script the client left in RAM, answers the text/menu/party-screen prompts from a dict, and writes the
special variables like the real commands do. A model, not the game.
"""
from __future__ import annotations

SPECIAL_VARS = {0x8004: 0x020370C0, 0x8005: 0x020370C2, 0x8006: 0x020370C4, 0x8007: 0x020370C6,
                0x800D: 0x020370D0, 0x800F: 0x020370D2}
CHARS = {0x00: " ", 0xAB: "!", 0xAC: "?", 0xAD: ".", 0xAE: "-", 0xB8: ",", 0xF0: ":", 0xB4: "'", 0xFE: "\n"}


def decode_text(read, addr, strvars):
    out = []
    while True:
        b = read(addr, 1)
        addr += 1
        if b == 0xFF:
            return "".join(out)
        if b == 0xFD:
            out.append(strvars.get(read(addr, 1) - 2, "?"))
            addr += 1
        elif 0xBB <= b <= 0xD4:
            out.append(chr(65 + b - 0xBB))
        elif 0xD5 <= b <= 0xEE:
            out.append(chr(97 + b - 0xD5))
        elif 0xA1 <= b <= 0xAA:
            out.append(chr(48 + b - 0xA1))
        else:
            out.append(CHARS.get(b, "#"))


def run_script(gba, addr, answers, species_names=None, last_talked=None, max_steps=200):
    """answers: {'menu': 0/1/2/127, 'yesno': 0/1, 'slot': 0-5 or 0xFF}. Returns the list of shown texts."""
    r, w = gba.py.read, gba.py.write
    if last_talked is not None:
        w(SPECIAL_VARS[0x800F], 2, last_talked)

    def getvar(v):
        return r(SPECIAL_VARS[v], 2) if v in SPECIAL_VARS else v

    def setvar(v, val):
        w(SPECIAL_VARS[v], 2, val & 0xFFFF)

    texts, strvars, cmp, pc = [], {}, 1, addr
    for _ in range(max_steps):
        op = r(pc, 1)
        pc += 1
        if op == 0x02:                                  # end
            return texts
        if op in (0x6A, 0x5A, 0x66, 0x68, 0x6C, 0x27, 0x6D):
            continue                                    # lock faceplayer waitmessage closemessage release waitstate waitbuttonpress
        if op == 0x16:                                  # setvar
            setvar(r(pc, 2), r(pc + 2, 2)); pc += 4
        elif op == 0x19:                                # copyvar
            setvar(r(pc, 2), getvar(r(pc + 2, 2))); pc += 4
        elif op == 0x21:                                # compare_var_to_value
            a, b = getvar(r(pc, 2)), r(pc + 2, 2); pc += 4
            cmp = 0 if a < b else (1 if a == b else 2)
        elif op == 0x06:                                # goto_if
            cond, dest = r(pc, 1), r(pc + 1, 4); pc += 5
            if {0: cmp == 0, 1: cmp == 1, 2: cmp == 2, 3: cmp != 2, 4: cmp != 0, 5: cmp != 1}[cond]:
                pc = dest
        elif op == 0x67:                                # message
            texts.append(decode_text(r, r(pc, 4), strvars)); pc += 4
        elif op == 0x6F:                                # multichoice x y id ignoreB
            pc += 4
            setvar(0x800D, answers.get("menu", 127))
        elif op == 0x6E:                                # yesnobox x y
            pc += 2
            setvar(0x800D, answers.get("yesno", 0))
        elif op == 0x25:                                # special
            sid = r(pc, 2); pc += 2
            if sid == 159:                              # ChoosePartyMon (RR; real mGBA run 11)
                setvar(0x8004, answers.get("slot", 0xFF))
        elif op == 0x7D:                                # bufferspeciesname
            idx, sp = r(pc, 1), r(pc + 1, 2); pc += 3
            strvars[idx] = (species_names or {}).get(sp, f"#{sp}")
        else:
            raise ValueError(f"unknown script opcode {op:#x} at {pc - 1:#x}")
    raise RuntimeError("script did not end")
