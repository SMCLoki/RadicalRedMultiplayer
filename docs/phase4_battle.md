# Phase 4 — PvP battle engine hooks (research plan for the in-emulator session)

Status: **network side done and tested; game side not started.**

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
