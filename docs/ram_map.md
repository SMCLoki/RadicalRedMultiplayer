# Radical Red RAM map (ROM sha1 964f951a0fdaf209e4ea1344883ef0d557bb3a80)

Base: vanilla FireRed US v1.0 (BPRE), sha1 41cb23d8dccc8ebd7c649cd8fbb58eeace6e2fdc. Radical Red text confirmed in ROM.
Emulator: mGBA 0.10.5 Qt (C:\Program Files\mGBA). The `-g` GDB stub does not listen in `mgba-sdl`; use Lua only.

Verified by `client/probe.lua` during real play (new game -> Chikorita starter -> rival battle).

## Key finding: party data is NOT encrypted on Radical Red
Vanilla Gen 3 XORs the four 12-byte substructs with PID^OTID, shuffles them by PID%24 and stores a checksum.
In Radical Red the 100-byte party struct is **plain**: checksum field (+0x1C) is 0, substructs sit in fixed order
G(+0x20) A(+0x2C) E(+0x38) M(+0x44). Observed on the starter (PID%24 = 10, which vanilla would shuffle) and on the
enemy party. Consequence: trade/PvP code can copy/validate mons by simple byte layout. **Still to confirm for PC box
mons** (check in Phase 3).

## Verified
| Name | Address | Size | How verified |
|---|---|---|---|
| gPlayerPartyCount | 0x02024029 | u8 | 0 on new game, 1 after starter |
| gPlayerParty | 0x02024284 | 6 x 100 | slot 0 decodes: species 152 (Chikorita), Tackle(33)/Growl(45), lvl 5, HP 20/20, PP 35/15 |
| gEnemyParty | 0x0202402C | 6 x 100 | rival fight: species 4 (Charmander), moves 10/45/206, lvl 5 |
| gBattleTypeFlags | 0x02022B4C | u32 | 0 overworld; 0x0000000C in rival (trainer) battle; back to 0 after |
| gSaveBlock1Ptr | 0x03005008 | u32 | -> 0x0202552C |
| SaveBlock1 pos | *ptr+0 (x), +2 (y) s16 | | changes while walking; value = tile coord (object coords are this +7) |
| SaveBlock1 map | *ptr+4 group, +5 num (u8) | | seen 4/1 (player 2F), 3/0 (Pallet), 4/3 (Oak's lab) |
| gMain | **0x030030F0** | struct | callback1@+0 = 0x080123E5, callback2@+4 = 0x08011101 (ROM thumb ptrs). Vanilla-Emerald-ish 0x030022C0 was WRONG |
| gObjectEvents | 0x02036E38 | 16 x 0x24 | [0] is the player (localId 0xFF) |
| ObjectEvent layout (empirical) | +0 flags (bit0 active), +8 localId, +9 mapNum, +10 mapGroup, +0x0C initialCoords, **+0x10 currentCoords (x,y s16)**, +0x14 previousCoords | | currentCoords == SaveBlock1 pos + 7; NPCs have localId 1,2,3... |
| gPlayerAvatar | 0x02037078 | struct | byte0 = 1 (on foot); byte2 changes (2 while moving) |
| gRngValue | 0x03005000 | u32 | changes every frame |

## Not yet verified (Phase 2+)
- ObjectEvent graphicsId/spriteId byte positions and facing nibble (+0x18 area: 0x44 / 0x24 observed)
- gSaveBlock2Ptr 0x0300500C, gBattleMons, battle controller/AI action hook points
- Whether Radical Red's added Mega/Z/Dynamax-style state lives outside the 100-byte struct (affects PvP)
- PC box mon format and trainer name/ID fields for trade validation

## Tooling status
- mGBA Lua scripting works. `socket` API works: `socket.connect(host, port)` -> object with `send`, `receive`,
  `add("received"|"error", cb)`. Round-trip with a Python TCP server verified. After the peer closes, the `received`
  callback keeps firing with `err="disconnected"` -> the client must close/remove the socket on that error.
- `client/pilot.lua` autopilot (reads `work/pilot.txt`) drives buttons and save/load slots from inside the emulator;
  needed because keystrokes sent from outside lose focus within seconds.
- Save states in `work/states/`: `lab_after_starter.ss`, `overworld_lab.ss`, `rival_battle_menu.ss` (copies of rr.ss1/2/3).
- Beware: do NOT use `open_application` for mGBA from automation - it spawns a second empty instance.

## Verification procedure (repeat if the ROM changes)
1. Launch `work/rr.gba` in mGBA Qt, Tools > Scripting > File > Load script > `client/probe.lua`.
2. Report: `work/probe_out.txt` (rewritten every 2 s) incl. raw hex dumps.
