"""Tiny ARM7TDMI Thumb disassembler for reading ROM code (no dependencies).

    py -3 tools/thumbdis.py work/rr.gba 0x0805E4C8 [count]

Covers the 16-bit Thumb formats 1-19, including BL pairs and PC-relative literal loads (the literal is shown).
"""
import struct
import sys

R = ["r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9", "r10", "r11", "r12", "sp", "lr", "pc"]
COND = ["eq", "ne", "cs", "cc", "mi", "pl", "vs", "vc", "hi", "ls", "ge", "lt", "gt", "le", "al", "nv"]
ALU = ["and", "eor", "lsl", "lsr", "asr", "adc", "sbc", "ror", "tst", "neg", "cmp", "cmn", "orr", "mul", "bic", "mvn"]


def rlist(bits, extra=None):
    regs = [R[i] for i in range(8) if bits >> i & 1]
    if extra:
        regs.append(extra)
    return "{" + ", ".join(regs) + "}"


def dis(rom: bytes, addr: int, count: int):
    out = []
    a = addr
    i = 0
    while i < count:
        o = a - 0x08000000
        h = struct.unpack_from("<H", rom, o)[0]
        txt = f".hword 0x{h:04x}"
        size = 2
        if h >> 13 == 0 and (h >> 11) & 3 != 3:                      # 1: shift imm
            op = ["lsl", "lsr", "asr"][(h >> 11) & 3]
            txt = f"{op} {R[h & 7]}, {R[h >> 3 & 7]}, #{h >> 6 & 31}"
        elif h >> 11 == 3:                                            # 2: add/sub
            op = "sub" if h >> 9 & 1 else "add"
            rn = f"#{h >> 6 & 7}" if h >> 10 & 1 else R[h >> 6 & 7]
            txt = f"{op} {R[h & 7]}, {R[h >> 3 & 7]}, {rn}"
        elif h >> 13 == 1:                                            # 3: mov/cmp/add/sub imm
            op = ["mov", "cmp", "add", "sub"][h >> 11 & 3]
            txt = f"{op} {R[h >> 8 & 7]}, #0x{h & 0xFF:x}"
        elif h >> 10 == 0x10:                                         # 4: alu
            txt = f"{ALU[h >> 6 & 15]} {R[h & 7]}, {R[h >> 3 & 7]}"
        elif h >> 10 == 0x11:                                         # 5: hi reg / bx
            op = h >> 8 & 3
            rd = (h & 7) | (h >> 4 & 8)
            rs = h >> 3 & 15
            txt = (f"bx {R[rs]}" if op == 3 else f"{['add', 'cmp', 'mov'][op]} {R[rd]}, {R[rs]}")
        elif h >> 11 == 9:                                            # 6: ldr pc-rel
            lit = ((a + 4) & ~3) + (h & 0xFF) * 4
            val = struct.unpack_from("<I", rom, lit - 0x08000000)[0]
            txt = f"ldr {R[h >> 8 & 7]}, =0x{val:08x}"
        elif h >> 12 == 5:                                            # 7/8: reg offset
            ro, rb, rd = R[h >> 6 & 7], R[h >> 3 & 7], R[h & 7]
            if h >> 9 & 1:
                op = ["strh", "ldsb", "ldrh", "ldsh"][h >> 10 & 3]
            else:
                op = ["str", "strb", "ldr", "ldrb"][h >> 10 & 3]
            txt = f"{op} {rd}, [{rb}, {ro}]"
        elif h >> 13 == 3:                                            # 9: imm offset
            b, l = h >> 12 & 1, h >> 11 & 1
            off = (h >> 6 & 31) * (1 if b else 4)
            txt = f"{'ldr' if l else 'str'}{'b' if b else ''} {R[h & 7]}, [{R[h >> 3 & 7]}, #0x{off:x}]"
        elif h >> 12 == 8:                                            # 10: halfword
            txt = f"{'ldrh' if h >> 11 & 1 else 'strh'} {R[h & 7]}, [{R[h >> 3 & 7]}, #0x{(h >> 6 & 31) * 2:x}]"
        elif h >> 12 == 9:                                            # 11: sp-rel
            txt = f"{'ldr' if h >> 11 & 1 else 'str'} {R[h >> 8 & 7]}, [sp, #0x{(h & 0xFF) * 4:x}]"
        elif h >> 12 == 10:                                           # 12: load address
            txt = f"add {R[h >> 8 & 7]}, {'sp' if h >> 11 & 1 else 'pc'}, #0x{(h & 0xFF) * 4:x}"
        elif h >> 8 == 0xB0:                                          # 13: sp adjust
            txt = f"{'sub' if h >> 7 & 1 else 'add'} sp, #0x{(h & 0x7F) * 4:x}"
        elif h >> 12 == 11 and (h >> 9 & 3) == 2:                     # 14: push/pop
            if h >> 11 & 1:
                txt = "pop " + rlist(h & 0xFF, "pc" if h >> 8 & 1 else None)
            else:
                txt = "push " + rlist(h & 0xFF, "lr" if h >> 8 & 1 else None)
        elif h >> 12 == 12:                                           # 15: ldm/stm
            txt = f"{'ldmia' if h >> 11 & 1 else 'stmia'} {R[h >> 8 & 7]}!, {rlist(h & 0xFF)}"
        elif h >> 12 == 13 and (h >> 8 & 15) < 14:                    # 16: cond branch
            off = (h & 0xFF) - (0x100 if h & 0x80 else 0)
            txt = f"b{COND[h >> 8 & 15]} 0x{a + 4 + off * 2:08x}"
        elif h >> 8 == 0xDF:                                          # 17: swi
            txt = f"swi 0x{h & 0xFF:x}"
        elif h >> 11 == 0x1C:                                         # 18: branch
            off = (h & 0x7FF) - (0x800 if h & 0x400 else 0)
            txt = f"b 0x{a + 4 + off * 2:08x}"
        elif h >> 11 == 0x1E:                                         # 19: bl pair
            h2 = struct.unpack_from("<H", rom, o + 2)[0]
            off = ((h & 0x7FF) << 12) | ((h2 & 0x7FF) << 1)
            if off & 0x400000:
                off -= 0x800000
            txt = f"bl 0x{a + 4 + off:08x}"
            size = 4
        out.append(f"{a:08x}: {txt}")
        a += size
        i += 1
    return out


if __name__ == "__main__":
    data = open(sys.argv[1], "rb").read()
    start = int(sys.argv[2], 16)
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 40
    print("\n".join(dis(data, start, n)))
