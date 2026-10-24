# Phase 4 — PvP battle engine hooks (research plan for the in-emulator session)

Status: **engine implemented (`client/rr_battle.lua`) and tested against a model of the battle loop; not yet run in the real emulator.**

## The engine (client/rr_battle.lua) - how it works

Loaded automatically by `rr_mp.lua` (set `pvp_engine=manual` to get the old manual stub back). Each client runs
an ordinary single battle where the local player is battler 0 and the other player is battler 1 ("opponent").

**Starting** (once both players are standing still on the map; the panel says so if not):
| Write | Value | Why |
|---|---|---|
| gEnemyParty 0x0202402C (+ gEnemyPartyCount 0x0202402A) | the other player's team | their real Pokémon |
| gBattleTypeFlags 0x02022B4C | `TRAINER \| EREADER_TRAINER` (0x808; `pvp_flags` overrides) | with EREADER_TRAINER `CreateNPCTrainerParty` does nothing (keeps our gEnemyParty), no EXP, no items, no Pokédex marks; CFRU keeps the flag at 0x800 |
| SaveBlock2+0x4A4 (e-reader trainer name, 5 chars) | other player's name | "... would like to battle!" shows their name; class at +0x4A1 only if `pvp_class` is set |
| SaveBlock2+0x15 bit 1 | battle style SET | otherwise "Shift" asks only one side whether to switch, which would desync |
| gTrainerBattleOpponent_A 0x020386AE | 0 | not TRAINER_SECRET_BASE / UNION_ROOM |
| gRngValue | battle seed | |
| gMain.savedCallback (+8) | CB2_ReturnToField 0x080567DD | the wild-battle return path: no `CB2_EndTrainerBattle`, so no black-out and no trainer flag |
| gMain.callback2 (+4) | CB2_InitBattle 0x0800FD9D | starts the battle next frame (no screen transition, map music continues) |

**Decision rounds.** One protocol "turn" = one moment where the game asks for choices:
- *turn round*: gBattleMainFunc enters HandleTurnActionSelectionState (0x08014041). Local choice is read when
  battler 0's selection state (gBattleCommunication[0]) reaches 3/4: gChosenActionByBattler[0] = 0 → move
  `{k="m", s=slot, t=target}` from gBattleBufferB[0][2..3]; = 2 → switch `{k="s", i=party index}` from [1].
- *replacement round* (after a faint, U-turn...): CHOOSEPOKEMON (22) sent to battler 0 (exec bit stays set
  while the player picks) and/or battler 1 (the AI answers in the same frame: seen as gBattleBufferB[1][0] = 34
  CHOSENMONRETURNVALUE). Local choice `{k="r", i}`; a side not asked sends `{k="n"}`.
- Battler 1 is held ("stalled") while the other player's choice is unknown: the engine keeps bit 1 of
  gBattleControllerExecFlags (0x02023BC8) set every frame, so the main loop waits and the AI controller just
  re-answers its last command. When the choice arrives it overwrites gBattleBufferB[1] right before the main
  loop reads it: `{0x21, 0|2}` at state 1, `{0x21, 10, slot, target}` or `{34, index}` at state 2,
  `{34, index}` (+ gBattleStruct->monToSwitchIntoId[1], offset 0x5C) for replacements. Targets are mirrored
  (their battler 0 is our battler 1).
- **RNG** (fixed after real mGBA run 5): `VBlankCB_Battle` calls `Random()` every frame and animations drawn on
  different sides take different numbers of frames (239 vs 240 for the same turn), so seeding once per turn
  drifted within the turn. Now the engine keys the random value to the battle's *logical* position: at the end
  of every frame in which gBattleControllerExecFlags == 0 (the gate in `RunBattleScriptCommands`, i.e. the next
  frame may run battle logic) it writes `FNV(seed, round, gBattleMainFunc, gBattlescriptCurrInstr 0x02023D74,
  occurrence of that position in this round)` to gRngValue. Every damage roll, accuracy check, crit and
  secondary effect therefore reads the same value on both machines however long the animations took, while
  animations themselves still get varying random numbers. `rng_lock=0` turns it off (for comparison).
- **Hash**: FNV-1a over both active mons (species, HP, max HP, PP, stat stages, status1), side 0 first on both
  machines, sent with each round's choice; the server ends the battle with `desync` on mismatch.

**End**: gBattleOutcome 1/2 → winner reported to the server. If the server ends the battle first (forfeit,
disconnect, timer, desync) the engine writes the outcome so the game ends at the next action check. When the
game is back on the map (callback2 = CB2_Overworld, flags 0) it restores the party (rr_mp backup), money,
battle-style option and e-reader slot. The save blocks move at battle start and on returning to the field;
everything is re-read through gSaveBlock1Ptr/gSaveBlock2Ptr. `client/rr_visuals.lua` now restores
gMapHeader.events whenever the game leaves the overworld, for the same reason.

**Tested** in `tests/test_battle.py` with `tools/fake_battle.py` (a model of the selection state machine,
controllers, VBlank Random(), side-dependent animation lengths, faints and battle exit built from the decomp; with
`rng_lock=0` it reproduces the run-5 desync, with the lock the same battle stays in sync): full 2v2 battle with a faint replacement -
both games log the identical (mirrored) battle; switching on both sides; forfeit mid-battle ends both games;
a speed tie is caught as a desync; party/money/option/e-reader slot restored and back on the map.

**Known limits**
- Mirrored simulation: each game has its own player as battler 0. Anything decided by battler index - speed
  ties, some end-of-turn ordering - comes out differently on the two machines. The hash check turns that into a
  clean `desync` ending, not silent divergence. The real fix is Option A below (link battle mode).
- Items are blocked by the EREADER flag; "Run" shows the game's can't-escape message.
- The opponent's name shows at most 5 characters (`CopyEReaderTrainerName5`). Class = FACILITY_CLASS_RED (134) or
  LEAF (135) by their sprite; set `pvp_class=<n>` if RR's tables show something else.
- RNG lock assumption: battle logic only consumes random numbers in frames that start with exec flags 0. A main
  function that is not gated and uses Random() while animations run would still drift; `client/battle_debug.lua`
  logs will show it.
- No battle intro transition or battle music (map music keeps playing).
- Abilities/items that make the AI controller or the game ask extra questions (e.g. Shift prompt) are untested.

## Real-emulator verification (for the local session)

> 2026-10-24 first run: start, turn sync, desync detection and restore work; every battle desynced within 2-4 turns
> because RNG drifts during turn animations (see progress.md "real mGBA run 5" and docs/evidence_bdbg_*.txt).
Two windows, both scripts loaded, both standing on a map. Window 1: `mp.battle("Player2")`, window 2: `mp.fight()`.
1. Both games cut to a battle against the other player's team; the intro names the other player.
2. Pick moves in both windows; each game waits for the other (panel: `waiting for opponent`), then both play
   the same turn. Compare HP after each turn.
3. Switch, let a Pokémon faint (replacement), forfeit with `mp.forfeit()`.
4. After the battle: party, money and the Options battle style are back; you are on the map; save works.
5. Things most likely to need adjusting, in order: the CB2_InitBattle start without the transition task (if the
   screen stays black, try starting from the transition callback 0x080565A9 or set gMain.state = 0); the
   return callback; whether the opponent's replacement really goes through CONTROLLER_CHOOSEPOKEMON on RR;
   the auto-answer seen after one A tap (check that pilot.lua releases A - a held key answers every prompt).

## Local findings (2026-10-24)

Local findings for the engine (all verified in real mGBA on RR, vanilla FR addresses hold):
- Opponent override point (Option B): while gBattleMainFunc == 0x08014041, the AI writes gBattleBufferB[1]
  (0x020235C4: 0x21, 10, slot, target) and gChosenMoveByBattler[1] (0x02023DC6) within ~7 frames. Overwrite both
  after that and before gBattleMainFunc changes (>= ~20 frames of slack in the rival fight).
- localAction: read gBattleBufferB[0] (0x020233C4) once byte 0 = 0x21 during 0x08014041; byte 2 = move slot.
- result: gBattleOutcome 0x02023E8A (1 win / 2 loss expected; 2 observed).
- Starting a battle: callbacks and savedCallback values are in ram_map.md (CB2 0x0800FD9D = init, return-to-field
  0x090B14C5). Trainer battles load the trainer's party during init, so gEnemyParty must be written after
  0x08010509 runs or the trainer id must point at an empty/controlled party - not tested yet.
- RNG: gRngValue 0x03005000 differed between the Tackle and Growl runs right after the choice (input timing changes RNG
  use), so the per-turn reseed must happen after both actions are locked in, as planned.

Done (cloud session, tested in `server/test_server.py`, `tests/test_client.py`, `server/soak.py`):
- Server lockstep coordinator: challenge/accept, team exchange + validation, shared u32 seed, per-turn
  action barrier, state-hash comparison, end-result agreement, forfeit, 120 s turn timer, 30 s reconnect grace
  with resume, several battles per room, results log.
- Client plumbing in `client/rr_mp.lua`: `mp.battle(who)`, `mp.fight()`, `mp.forfeit()`, auto team upload,
  party backup to `work/mp_party_backup_<profile>.txt` at battle start and restore at the end, and a pluggable
  `M.engine` table that the battle loop calls every frame.

What is missing is `M.engine`: the six functions that connect the protocol to the actual game.

```lua
M.engine = {
  begin      = function(b) end,              -- start the in-game battle vs b.opp.party, seed b.seed; return true
  localAction= function(b) end,              -- this side's chosen action for turn b.turn, or nil while choosing
  stateHash  = function(b) end,              -- hash of battle state at start of b.turn, same on both sides
  applyTurn  = function(b, turn, actions) end, -- feed actions[1] (side 0) and actions[2] (side 1) to the game
  result     = function(b) end,              -- 0/1 winner once over, else nil
  finish     = function(b) end,              -- tear down; the client restores the real party afterwards
}
```
`b` fields: `bid, side (0 = challenger), seed, turn, party (own team hex), opp = {id, name, party}, rules`.
The engine can be developed in a separate file that does `_RRMP_INSTANCE.engine = {...}` after rr_mp.lua loads.

## The core problem: both emulators must run the same simulation

Lockstep only works if both games execute bit-identical battle logic. Two traps:

1. **RNG.** `gRngValue` (0x03005000) advances every frame in the overworld and on many battle frames.
   Both sides must load the same value at the same logical points: at battle start (`b.seed`) and at the start
   of every turn's execution (e.g. `seed ~ turn * 0x9E3779B1`), *after* both actions are locked in.
2. **Battler indexing.** In a normal trainer battle the local player is always battler 0. If each client
   injects the other player as the "trainer", the two simulations are mirrored: speed ties, RNG-order and
   "who moves first" logic see different battler ids on each machine and will desync.

### Option A — Link battle mode (recommended to investigate first)
Gen 3 link battles already solve trap 2: with `BATTLE_TYPE_LINK`, the master's mons are battler 0 on *both*
machines (vanilla `SetControllersVariables`: the non-master assigns battler 0 to the link-opponent controller
and battler 1 to the player). The engine then exchanges every choice through link buffers. If the Lua script
can stand in for the link cable — fill the receive buffer with the remote player's buffer contents and report
the serial transfer as complete — the game itself does the lockstep, and side 0 (challenger) is the master.
To find on RR: `gBattleTypeFlags` link bits, link status/`gReceivedRemoteLinkPlayers`, the battle link
send/receive buffers, and the serial/link state machine variables. Expect CFRU changes here.

### Option B — Trainer battle + action override (faster to prototype, may desync)
1. Start a trainer battle (see "Starting a battle" below), overwrite `gEnemyParty` (0x0202402C, verified) with
   `b.opp.party` before the battle copies it into the battle mons.
2. When the opponent AI has chosen, overwrite its chosen action/move/target with the remote action; when the
   local player has chosen, read it as `localAction`. Hold the turn until the remote action arrives.
3. Re-seed RNG at each turn start, hash a **side-canonical** state (side 0's mons first on both machines).
4. Measure desync rate over 20 battles. If speed ties / RNG ordering desync, go to Option A.

## Starting a battle from Lua (no function calls available)
mGBA Lua can only read/write memory and press keys, so a battle has to be started by data:
- Record `gMain.callback1/callback2` (0x030030F0, +0 and +4, verified) every frame while a normal trainer
  battle starts (use the `rival_battle_menu` state or walk into a trainer). The sequence reveals the RR
  addresses of the battle-init callback (vanilla: `CB2_InitBattle`) and the return-to-field callback
  (`gMain.savedCallback`, +8).
- Then try: write enemy party + `gBattleTypeFlags` (+ opponent trainer id, if needed) and set
  `gMain.savedCallback` = return-to-field, `gMain.callback2` = battle-init while in the overworld.
- Fallback: a designated NPC/trainer the player talks to (hijack its battle at the 0 → non-zero transition of
  `gBattleTypeFlags`).
- Battle rewards: make sure no money/exp/items change — the client restores the party afterwards anyway, but
  money and bag are not restored. Find which flags suppress rewards (vanilla: `BATTLE_TYPE_LINK`/`FRONTIER`-like).

## Addresses to find (record each in docs/ram_map.md with evidence)
Use `client/ramsearch.lua`: `rs.callbacks(true)` logs every gMain callback change with battle flags (the
battle-start sequence), `rs.start(2)` + `rs.filter("dec")` after taking damage finds HP fields, and
`rs.snap`/`rs.diff` around choosing Fight vs Switch finds the chosen-action bytes.

| What | How to find |
|---|---|
| battle main function pointer (`gBattleMainFunc`) | IWRAM/EWRAM word holding a ROM thumb pointer that changes at turn phases |
| chosen action / move / target per battler (`gChosenActionByBattler`, `gChosenMoveByBattler`, `gBattleStruct->moveTarget`) | pick Fight vs Bag vs Switch and diff memory |
| battle mons (`gBattleMons`, 4 × ~0x58) | contains current HP/stats of active mons; diff while taking damage |
| battle outcome (`gBattleOutcome`) | 0 during battle, 1 win / 2 loss after |
| `gBattlersCount`, `gActiveBattler`, battler positions | needed for Option A |
| link buffers and status (Option A) | start a real link battle in two mGBA instances connected with mGBA's built-in multiplayer (File → New multiplayer window) and diff |

Tip: mGBA's *built-in local multiplayer* (two windows, emulated link cable) can run a **real** link battle
between `rr.gba` and `rr2.gba` on one PC. That is the best reference for Option A: you can watch exactly what
the engine writes and expects at each step.

## Definition of done for Phase 4
20 consecutive 6v6 battles between two machines with 0 `desync` results (server log
`server/data/battles.jsonl`), real party/money/bag unchanged afterwards, forfeit and disconnect handled.
