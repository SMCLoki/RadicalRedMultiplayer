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
- gSprites 0x0202063C (0x44 each) and gMapHeader 0x02036DFC: vanilla FR US (pokefirered symbols); used by
  client/rr_visuals.lua, which validates both at runtime (docs/visuals.md). SaveBlock1+0x8E0 = objectEventTemplates[64].
- ObjectEvent: +4 spriteId, +5 graphicsId, +0x0B elevation (low nibble), +0x1C movementActionId; flags bit6/7
  heldMovementActive/Finished, bit8 frozen, bit13 invisible, bit16 isPlayer (pokefirered global.fieldmap.h).
- ObjectEvent graphicsId/spriteId byte positions and facing nibble (+0x18 area: 0x44 / 0x24 observed)
- gSaveBlock2Ptr 0x0300500C, gBattleMons, battle controller/AI action hook points
- Whether Radical Red's added Mega/Z/Dynamax-style state lives outside the 100-byte struct (affects PvP)
- PC box mon format and trainer name/ID fields for trade validation

## mGBA 0.10 scripting API facts (read from mGBA 0.10 source by the cloud session, not yet re-checked live)
- `socket.connect(host, port)` returns the socket, or `nil, err`. On success the wrapper registers a frame
  callback that polls the socket every frame (`socket:poll()` → `select(0)`), dispatching `received`/`error`.
- Socket callbacks are invoked as `cb(socket, ...)`; the `error` callback gets `(socket, errString)`.
- `receive(n)` returns data, or `nil, "disconnected"` when the peer closed (fires every frame → close it),
  or `nil, socket.ERRORS[code]`; would-block is `"temporary failure"` (not `"again"`).
- `send(data)` returns bytes written or `nil, err`.
- `emu:checksum([type])` defaults to CRC32 and returns 4 bytes big-endian. `emu:readRange(addr, len)` returns
  a string. Callbacks available: `frame`, `start`, `reset`, `shutdown`, `keysRead`, ...
- Text buffers: `clear, print, setSize, moveCursor, advance, setName, getX/getY, cols/rows`.
- mGBA 0.10 Qt has **no `--script` command-line option**; scripts are loaded from the Scripting window.

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

## Battle (verified 2026-10-24, local session, deterministic dump diffs in real mGBA)
Method: `pilot.lua` LOADSTATE + frame-exact inputs + DUMP, two runs differing in one input (Tackle vs Growl from
`states/rival_battle_menu.ss`), diffed offline; repeat runs differ only in 3 noise bytes (0x0300539C, 0x03005E8D/E,
0x03005EA7/8). All match vanilla FireRed 1.0 symbols.

| Name | Address | Size | Evidence |
|---|---|---|---|
| gBattleMons | 0x02023BE4 | 4 x 0x58 | species +0 (152 / 4), moves +0x0C (33,45 / 10,45,206), HP +0x28, level +0x2A, maxHP +0x2C; Tackle took Charmander 20 -> 18, Growl run stayed 20 |
| gBattlersCount | 0x02023BCC | u8 | 2 in a single battle |
| gCurrentMove | 0x02023D4A | u16 | 0x21 (Tackle) vs 0x2D (Growl) right after choosing |
| gChosenActionByBattler | 0x02023D7C | 4 x u8 | 0x00 (use move) while chosen, 0xFF when cleared at turn start |
| gChosenMoveByBattler | 0x02023DC4 | 4 x u16 | [0] = player's move id, [1] = AI's move id (0x2D, 0x0A, 0xCE seen) |
| gMoveSelectionCursor | 0x02023FFC | 4 x u8 | 0 on Tackle, 1 after pressing Right |
| gBattleBufferB | 0x020233C4 | 4 x 0x200 | controller reply per battler: [0]=0x21 (TWO_RETURN_VALUES), [1]=10, [2]=move slot, [3]=target. Battler 1 at 0x020235C4 |
| gBattleOutcome | 0x02023E8A | u8 | 0 during battle, 2 after losing the rival fight |
| gBattleMainFunc | 0x03004F84 | ptr | 0x08014041 while choosing actions; 0x080150A9 -> 0x080154A1 -> 0x080155C9 when the turn runs; 0x08013B1D -> 0x08013BD5 at turn end; 0x08015A31 -> 0x08015B59 at battle end |
| gBattleStruct (pointer) | 0x02023FE8 | ptr | 0x02000010 |
| gBattleTypeFlags values | 0x02022B4C | u32 | 0x08 trainer during transition, 0x0C (trainer + master) in battle, 0 after |

### gMain callbacks (gMain 0x030030F0: callback1 +0, callback2 +4, savedCallback +8)
| Phase | callback1 | callback2 | savedCallback |
|---|---|---|---|
| overworld | 0x08056535 | 0x080565B5 | - |
| trainer battle transition | 0x08056535 | 0x080565A9 | 0x090B14C5 (RR return-to-field, in expanded ROM) |
| battle init | 0x08056535 | 0x0800FD9D, then 0x08010509 (gBattleMainFunc 0x080123BD/0x080123C1) | 0x090B14C5 |
| battle | 0x080123E5 | 0x08011101 | 0x090B14C5 |
| back to field | 0x08056535 | 0x080568E1 -> 0x08056809 -> 0x080565B5 | 0x090B14C5 |
Recorded with `CBLOG` while the rival battle started (`states/lab_after_starter.ss`, walk down) and ended.

### Turn timing (one turn, from `turn_cblog.txt`)
Turn start: gBattleMainFunc 0x08014041, actions reset to 0xFF. ~7 frames later the AI's reply lands in gBattleBufferB[1]
and gChosenMoveByBattler[1]; the player's reply lands in gBattleBufferB[0] when the move is picked;
gChosenMoveByBattler[0] is copied 1 frame later; 2 frames after that gBattleMainFunc leaves 0x08014041 (turn executes).
Open question: after one A tap from the move menu, later turns were answered for the player without input (~12 frames
after the prompt). With no input at all the battle waits. Not explained yet - check before relying on localAction.
