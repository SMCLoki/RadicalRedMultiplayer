# Progress log

Legend: **VERIFIED** = seen working on the real ROM in mGBA · **TESTED** = automated tests pass (server real,
client in `tools/mgba_mock.py` fake mGBA) · **WRITTEN** = code exists, never run · **TODO**

## Phase 0 — RAM map + harness: VERIFIED (local session)
See `docs/ram_map.md`. Party structs unencrypted on RR.

## Phase 1 — Networking: TESTED, real-emulator check pending
- `server/rr_server.py`: stdlib asyncio, newline JSON, rooms of **8**, hello/welcome/join/leave, ping/pong,
  10 s hello + idle timeouts, token-bucket rate limit (60/s, burst 120), ROM-id check, uid reconnect
  ("replaced"), slow-consumer drop, presence snapshot in welcome. 34 tests in `server/test_server.py`.
- `client/rr_mp.lua`: JSON lib, event-driven socket reads, outbound buffer with partial-send handling,
  6 s dead-link detection, reconnect backoff 1→10 s, no auto-retry on fatal errors, per-window profiles,
  "RR Online" text buffer + `work/mp_status_<profile>.txt`, console commands (`mp.help()`).
  `socket.connect` blocks the emulator; if one attempt takes ≥3 s (unreachable host) the client stops
  auto-retrying and asks for `mp.reload()` instead of freezing the game every 10 s.
- Checked against mGBA 0.10 source (cloud session): socket callbacks are called as `cb(socket, ...)`;
  would-block is reported as `"temporary failure"`; sockets are auto-polled every frame after `connect`;
  `emu:checksum()` defaults to CRC32 (4 bytes big-endian); `"shutdown"` callback exists; **no `--script`
  command-line option in 0.10** (load the script via the Scripting window; mGBA remembers recent scripts).
- 13 end-to-end tests in `tests/test_client.py` run the real Lua script against the real server.
- Real-emulator demo: TODO → `docs/local_checklist.md` §1.

## Phase 2 — Presence: TESTED (data path), rendering TODO
- Position/map from SaveBlock1 (verified addresses), broadcast on change ≤15/s, panel shows players on your map
  with tile distance. Facing nibble at `objectEvents[0]+0x18` is a **hypothesis**.
- Sprite rendering via object-event slot: not started (needs in-emulator research). Text panel is the fallback.

## Phase 3 — Trading: TESTED, real-emulator check pending
- Server two-phase commit with fsynced ledger `server/data/trades.jsonl`; commits redelivered until acked
  (survives client crash, network loss, server restart). Validation of the 100-byte struct.
- Client: journal-before-write, atomic slot overwrite between frames, deferred during battles, reconciliation
  on connect + every 5 s (handles kill-before-save and old-save reloads).
- Tests: happy path, invalid mons, kill before commit (no change), kill after commit (redelivered and applied
  on restart), old-save reload (re-applied once), trade blocked in battle.
- Soak (`server/soak.py`, 8 bots, ~1 % chance per action to drop, 10 % crash right after commit, 5 % vanish
  before confirm): 10-minute run → 1654 trades, 759 battles, 0 dupes/0 losses/0 desyncs (table below).
- Party-only; PC boxes not supported. Trade evolutions not triggered.

## Phase 4 — PvP: network TESTED, engine TODO
- Server lockstep coordinator + client plumbing done (see `docs/phase4_battle.md`). Game hooks (`M.engine`)
  not implemented; research plan in `docs/phase4_battle.md`.

## Phase 5/6 — QoL + hardening: partial
- Done: chat, 8-player rooms with parallel battles/trades, rate limits, legality-lite checks, soak test,
  `start_server.bat`, `start_client.bat`, `run_tests.bat`, README quick start.
- TODO: Showdown team import, rules beyond `level_cap`, spectators, ladder, species names in the panel.

## Soak results
| date | clients | duration | trades | battles (per side) | disconnects | dupes | losses | desyncs | result |
|---|---|---|---|---|---|---|---|---|---|
| 2026-10-24 | 8 bots | 0.5 min | 62 | 37 | 12 (+11 crash-after-commit) | 0 | 0 | 0 | PASS |
| 2026-10-24 | 8 bots | 10 min | 1654 | 759 | 230 (+197 crash-after-commit, 86 vanish-before-confirm) | 0 | 0 | 0 | PASS |


## 2026-10-24 - first run in real mGBA 0.10.5 (local Windows session)

- Offline tests: 34/34 server tests pass after a test fix (`test_disconnect_past_grace_loses` raced the turn timer on
  Windows; it now has its own class with a 5 s turn timer). Soak 1 min: PASS, 0 dupes/lost.
- **Crash found and fixed:** mGBA aborted (exit code 0x40000015) about 3 s after the client loaded. Cause: the trailing
  `return M` in `client/rr_mp.lua`. mGBA 0.10 runs a script file with `lua_pcall(..., LUA_MULTRET)` and never pops the
  results, so the table stayed on the Lua stack and the next callback (the socket poll) aborted. Removed, plus a static
  regression test in `tests/test_client.py`. The fake mGBA did not model this.
- Also: socket `close()` is now deferred to the client's own frame tick (closing from inside a socket callback removes the
  socket's poll callback while mGBA is iterating the frame list).
- Opt-in trace log: create `work/mp_trace_on` to get `work/mp_trace.txt`.
- **Verified in the emulator:** connect, welcome, ping/pong, "RR Online" panel, both windows list each other with
  map/x/y and `<< HERE`; killing and restarting the server -> both clients back online within ~3 s.
- Not yet checked: `mp.*` console commands, game id in server log, pause-timeout, script reload, walking presence,
  trading, battles.

## 2026-10-24 - cloud follow-up to the first real run
- `tools/mgba_mock.py` read the client through the script's return value; after `return M` was removed it got
  nil, so the 12 lupa end-to-end tests could not run (they are skipped on the Windows PC without lupa). The mock
  now reads `_RRMP_INSTANCE` and, like real mGBA, rejects any script that returns values.
- That surfaced a bug from the trace patch: `_RRMP_TRACE("handled " .. msg.t)` ran after `handlers.state` had
  cleared `msg.t`, raising an error on every presence message (console error spam and the rest of that network
  read delayed until the next one). Fixed by reading `msg.t` before calling the handler. 14/14 client tests and
  34/34 server tests pass.
- New: `client/ramsearch.lua` (console RAM search: narrowing filters, snapshot diffs, watches, gMain callback
  log) for the Phase 2/4 address hunting; tested in the fake mGBA. All `client/*.lua` are checked for a
  trailing `return`.
- Recommendation for the local session: `pip install lupa` (with the user's OK) so `tests/test_client.py`
  actually runs on Windows instead of skipping.



## 2026-10-24 - real mGBA run 2 (local): presence, facing, commands, trading

Setup: two mGBA windows (work/rr.gba, work/rr2.gba) from `overworld_lab.ss`, local server, `pilot.lua` in window 1.
Both windows start with the same Chikorita, so window 2's PID was edited in RAM to tell them apart (test data only).

- **Walking presence works:** window 1 walked via pilot, window 2's panel followed (x10 y10 -> x9 y9, "<< HERE").
- **Facing verified:** objectEvents[0]+0x18 = 0x11/0x22/0x33/0x44 for down/up/left/right; low nibble 1-4 is right.
- **Console commands work:** `mp.say`, `mp.who`, `mp.party`, `mp.trade`, `mp.accept`, `mp.confirm`.
- **Trade works:** PIDs swapped (12345678 <-> 8C233F8A); the summary screen shows the received Pokémon normally
  (nature follows the edited PID, as expected).
- **Kill mid-trade works:** window 2 confirmed, was killed before window 1 confirmed -> window 1 got
  "trade cancelled (disconnect)", party unchanged.
- **Kill after commit works** (window 2 killed 3 s after commit, relaunched from a pre-trade state -> trade re-applied).
- **Bug found and fixed in `reconcile()`:** on that relaunch it also re-applied an OLDER journal entry whose given
  Pokémon had come back in the newer trade, replacing it with a Pokémon already traded away (a dupe). In real play:
  trade A away, later get A back, reload -> the old entry fires. Fix: an entry is skipped once a later entry received its
  `give` Pokémon. Re-tested: only the latest trade re-applied, window 2 ends with the right Pokémon.
  Known limit: reloading a save from before SEVERAL chained trades can still mis-reconcile; a full fix would replay the
  journal in order against the loaded party. Worth a fake-mGBA test.
- Not done: in-game save -> reset -> reload after a trade; trade evolutions; PC boxes; pause-timeout; script reload.

## 2026-10-24 - cloud: player visuals (Phase 2 rendering)
- New `client/rr_visuals.lua` (auto-loaded by rr_mp.lua): remote players on your map are spawned by the game
  itself as NPC "puppets" (extra object-event templates + a patched `gMapHeader.events` copy with a larger
  count) and walked with held movements (`FACE_*`, `WALK_NORMAL_*`, `WALK_FAST_*`); big jumps teleport;
  leaving hides the puppet and lets the game delete it. Design, addresses and limits: `docs/visuals.md`.
- Addresses from pret/pokefirered (symbols branch): `gSprites` 0x0202063C, `gMapHeader` 0x02036DFC; the four
  already-verified RR addresses match vanilla, and the script validates both before writing (EWRAM scan
  fallback for gSprites). SaveBlock1 template array at +0x8E0 (pokefirered `global.h`).
- Presence now carries the last 8 steps, on-foot sprite and elevation. Commands: `mp.visuals(on)`, `mp.vis()`.
- Tested: `tests/test_visuals.py` (7 tests: spawn after a step, follows walking with WALK actions, running ->
  WALK_FAST, turning, teleport, leaving map -> removed by the game, disconnect -> header restored, real NPCs
  untouched, validation failure -> nothing written, disabled). These run against `tools/fake_overworld.py`,
  a model of the engine built from the decomp, NOT the real ROM. Real-emulator check: TODO (docs/visuals.md).


## 2026-10-24 - real mGBA run 3 (local): player visuals (commit a8f2074)

Two windows in Oak's lab (map 4.3), autopilot in both (`PILOT_FILE="pilot2.txt"` set in window 2's console first;
`pilot.lua` now takes that global and writes `<file>_done.txt`).

- Address check passed on the real ROM: "visuals: addresses validated".
- **Spawn works:** after a step, each window draws the other player as a Red NPC on the right tile and facing.
- **Walking works:** the puppet walked the same 4-step loop in real time (seen mid-walk on the doormat) and stopped
  on the matching tile.
- **Leaving/entering works:** when Player1 walked outside (map 3.0), window 2's puppet vanished ("0 drawn"); walking
  back in, window 2 drew it on the doormat immediately. Window 1 drew Player2 again after its next step, as documented.
- **Start menu works:** with window 1's start menu open, the puppet stayed visible; after closing, both screens matched.
- **Script reload works:** reloading rr_mp.lua in window 1 left exactly one puppet (old instance cleaned up).
- Side note: mGBA pauses emulation while its own file dialog is open, so loading a second script can idle the client
  long enough for a server `timeout`; it reconnects by itself.
- Not tested yet: running (pilot holds one key at a time), ledges, battle-and-return, `puppet_gfx=remote`,
  real NPCs unaffected over a long session.

## 2026-10-24 - real mGBA run 4 (local): running, battle-and-return

Window 1 from `overworld_lab.ss`, window 2 from `rival_battle_menu.ss` (mid rival battle), pilot combos from 0796d7e.
`pilot.lua` now replaces an already-loaded copy on reload (`_PILOT_CB`), so reloading never runs two autopilots.

- **Battle-and-return works:** while window 2 fought the rival, window 1 kept drawing Player2 on the tile where the
  battle started, panel "(in battle)"; window 2's panel said "waiting for the overworld". After the battle window 2
  drew Player1 after one step, and window 1's copy of Player2 walked again normally.
- **Running works:** `HOLD B+LEFT 40` moved Player1 5 tiles (x9 -> x4, running speed); window 2's copy ended on the
  same tile.
- Not tested: ledges (none indoors; needs Route 1), `puppet_gfx=remote` (needs a Leaf save; both test saves are Red).

## 2026-10-24 - cloud: PvP battle engine
- New `client/rr_battle.lua` (auto-loaded): starts an EREADER-trainer battle against the other player's team
  from the overworld, runs lockstep "decision rounds" (turns and faint replacements), holds the AI battler via
  gBattleControllerExecFlags until the remote choice arrives and overwrites its gBattleBufferB reply, re-seeds
  gRngValue at matching points, hashes state per round, ends on gBattleOutcome or a server result, restores
  party/money/battle style/e-reader slot back on the map. Details and verification steps: docs/phase4_battle.md.
- Built on the local session's verified battle addresses plus pokefirered for gBattleControllerExecFlags,
  gBattleBufferA, gTrainerBattleOpponent_A, CB2_ReturnToField, controller command ids and BattleStruct offsets.
- `client/rr_visuals.lua`: restores gMapHeader.events as soon as the game leaves the overworld (save blocks move
  at battle start and field init) and uses the verified CB2_Overworld instead of learning it.
- `tools/fake_battle.py` + `tests/test_battle.py` (4 tests, all pass 5/5 runs): synced 2v2 with replacement,
  switching, forfeit, speed-tie desync detection, full restore. Model of the engine, not the ROM.
- Not run in the real emulator yet.


## 2026-10-24 - real mGBA run 5 (local): PvP battle engine (commit 6572b6a)

Two windows in Oak's lab, both with the same level-5 Chikorita (holding an Oran Berry). 3 battles via
`mp.battle("Player2")` / `mp.fight()`, moves picked with pilot taps.

Works in the real game:
- **Start:** both games cut straight into the battle (no black screen) and show the other player's team.
- **Turn sync:** each game waits for the other's choice ("waiting for opponent"), then both play the same moves.
- **Desync detection:** the per-round hash caught every divergence; the server ended the battle as `desync` in both
  games (`server/data/battles.jsonl`).
- **Restore:** after every battle both games returned to the map with party HP, money and options restored; puppets
  still drawn.

Problems found:
1. **Desync within 2-4 turns, every battle.** Battle 1 had a speed tie (identical mons, the known limit). Battles 2-3
   had different Speed (window 2's Speed set to 30 in RAM as test data), still desynced at turn 3.
   Evidence: `docs/evidence_bdbg_window1.txt` / `_window2.txt` (from `client/battle_debug.lua`, one line per
   gBattleMainFunc change). Both games load the same reseed at the same logical point (turn 1 `C7BD2DF9`, turn 2
   `65E4A72A`) and the turn-order phase produces identical RNG values, but by the end of turn 1 window 1 is one RNG step
   ahead (`38701368 -> 981253BB` vs `981253BB`), and turn 1 took 239 vs 240 frames. In turn 2 the damage rolls then
   differ (window 2: P1 dropped to <=10 HP, ate its Oran Berry, ended on 20; window 1: P1 ended on 12).
   **Likely cause:** battle animations/controllers consume `Random()` and run for different lengths depending on
   which side a Pokémon is drawn on (back sprite vs front sprite), so RNG drifts mid-turn even with an identical
   reseed. Suggested fix to try: reseed before *each* damage-relevant step (e.g. when gBattlerAttacker /
   gCurrentMove changes for a new move), or snapshot gRngValue whenever gBattleControllerExecFlags goes 0 -> non-zero
   and restore it when it returns to 0 (the battle script waits while controllers run, so only visual code should use
   RNG in that window - verify that order within a frame first). Option A (link mode) avoids this entirely.
2. **Opponent name:** the intro says "You are challenged by Rocket Admin Playe!" in both games - the e-reader trainer
   class shows as "Rocket Admin" and the name is cut to 5 characters.
3. Not tested yet: switching, a faint/replacement, `mp.forfeit()`, a battle reaching a winner (none lasted long enough).

Tools: `client/battle_debug.lua` (state log per battle-loop step, file per window via `PILOT_FILE` tag).

## 2026-10-24 - cloud: fix for the run-5 RNG drift + opponent class
- Root cause confirmed in pokefirered: `VBlankCB_Battle` calls `Random()` every frame ("Change gRngSeed every
  vblank"), so any difference in frame counts (animations drawn on the other side: 239 vs 240 frames) shifts every
  later roll in the turn.
- Fix (`client/rr_battle.lua`): gRngValue is keyed to the battle's logical position instead of time. At the end of
  each frame where gBattleControllerExecFlags == 0 (battle script may run next frame) it is set from
  (seed, round, gBattleMainFunc, gBattlescriptCurrInstr, occurrence count). Replaces the per-turn reseed.
- `tools/fake_battle.py` now models VBlank Random(), move animations in the attacker's controller with
  side-dependent length and extra Random() calls, and script logic gated on exec flags. New control test: with
  `rng_lock=0` the battle desyncs exactly like run 5; with the lock all sync tests pass (5/5 tests, 3 runs).
- Intro class: e-reader trainer class is now written (FACILITY_CLASS_RED 134 / LEAF 135 from the opponent's
  sprite) instead of the save's leftover value ("Rocket Admin"). The name is limited to 5 characters by the game.
- To verify in real mGBA: run `client/battle_debug.lua` in both windows again; rng should now match at every
  gBattleMainFunc change, and battles should reach a winner without `desync` (except speed ties).


## 2026-10-24 - real mGBA run 6 (local): RNG fix e18ef91

Two windows, each side given 2 Chikorita (second one copied in RAM with a new PID, test data); window 2's are Speed 30
(window 1 Speed 10), so no speed ties. Logs: `docs/evidence_bdbg_run6_window1/2.txt` (battles 4-6, appended).

Verified:
1. **RNG matches at every battle-loop step** in both games for whole battles (same gRngValue at every
   gBattleMainFunc change). The VBlank drift is fixed.
   Side check: with a battle menu idle, gRngValue advances exactly 1 LCG step per frame (VBlankCB_Battle). A frame
   callback that rewinds one step (x = (x - 0x6073) * 0xEEB9EB65) makes it advance 0 steps per frame, i.e. mGBA's
   frame callback runs before the VBlank handler - an alternative way to cancel the per-frame call if needed.
2. **Switching works:** window 1 switched to its second Chikorita on turn 2; window 2 showed the same opponent and
   both agreed on HP.
3. **Intro:** "You are challenged by Player Playe!" with the Red trainer sprite and 2 Poké Balls (class fixed;
   name still cut to 5 characters).
4. **mp.forfeit() works:** window 1 "you lose (forfeit)", window 2 "YOU WIN (forfeit)", server winner=1 reason
   forfeit; both back on the map with party/money/options restored.

Still desyncs (battles 4, 5, 6 at turn 4, 3, 2): **side asymmetry, not RNG.** With identical RNG at every step:
- Battle 6 turn 1: Player1's Tackle did 5 damage in window 1 (Player1 = player side, battler 0) but 4 in window 2
  (Player1 = opponent side, battler 1). Same attacker stats in both (lvl 5, Atk 10, Def 10 checked).
- Battle 5 turn 2: in window 2 only, the opponent-side Pokémon (Player1's) ended the turn at 20 HP instead of 12, and
  that turn took 631 frames vs 239 in window 1 - something extra played only on the opponent side.
Candidates: a player-side-only damage modifier (vanilla's badge boost is skipped for e-reader battles, but CFRU/RR's
damage code may only exclude LINK), and trainer-AI item use for the opponent side (healing at low HP) that isn't
blocked by overwriting the action byte. Either way each game treats "its" player differently from the opponent,
so Option B can't be fully symmetric - this points to Option A (link battle mode, both games run battler 0 = master).
Not tested yet: a faint/replacement round and a battle reaching a natural winner (desync always came first).

## 2026-10-24 - cloud: response to run 6 (side asymmetry)
Analysis of run 6 (`docs/evidence_bdbg_run6_*`): battle 5's extra 400 frames and full heal fit Player1's Oran Berry
(it triggers at <= half HP, so after a bigger hit on the opponent side), i.e. the root symptom is the damage roll:
the player side dealt more (5 vs 4). Open-source CFRU has no player-side damage bonus for EREADER battles (badge
boosts are excluded) and the roll uses Random(), so two engine-side causes were fixed and a third is diagnosable:
1. **CFRU AI side effects.** The old hold re-ran battler 1's AI every frame; CFRU's AI_TrySwitchOrUseItem /
   BattleAI_SetupAIData temporarily Mega Evolve all battlers, compute damage predictions and set switch/item state,
   only on the machine where that Pokémon is the opponent. Now battler 1 is **parked**: when the main loop is about
   to ask it for a choice (selection state 0) its controller function (gBattlerControllerFuncs[1], 0x03004FE4) is
   set to BattleControllerDummy (0x0802E310, `bx lr`), so the AI never runs for choices we answer; the engine writes
   the reply, clears the exec bit and restores the controller after the last stage. No race with the frame callback
   either. Falls back to the old exec-flag hold if the dummy isn't a `bx lr` (`controller_dummy=0` forces that).
2. **Lag frames.** `VBlankIntr` also calls Random() and mGBA's frame callback fires mid-logic when a frame overruns
   (animations), so the RNG lock (and the hold) could write in the middle of a command on only one machine. The engine
   now detects a mid-iteration callback with the play-time counter (SaveBlock2+0x10, advances once per finished game
   loop): on such frames it touches nothing and rewinds the RNG by `vblank_rng_steps` (default 1, run-6 measurement)
   to cancel the VBlank call. `lag_guard=0` turns it off.
3. **Diagnosis if it still desyncs:** `client/battle_trace.lua` logs every battle-script step (gBattleMainFunc +
   gBattlescriptCurrInstr) with RNG, gBattleMoveDamage, crit, attacker/target, HP and lag frames;
   `tools/compare_traces.py w1.txt w2.txt` aligns the two windows and prints the first differing step and what it
   means (lock not holding vs deterministic per-side difference vs different script paths).
Tests: `tools/fake_battle.py` now models VBlank Random() after the callback, the play-time counter, lag frames that
split a damage calculation, controller dispatch with the dummy, and battler-specific AI side effects. 7 battle tests
pass 5/5 runs; three control tests show a desync with each protection switched off (rng_lock, lag_guard, parking).
If a deterministic per-side difference remains (RR's closed changes), the structural answer is the link battle
mode; see docs/phase4_battle.md "Option A".


## 2026-10-24 - real mGBA run 7 (local): dummy AI controller + lag guard (cc8efa7)

Setup as run 6 (2 Chikorita each, window 2 Speed 30). `battle_trace.lua` in both windows; traces in
`docs/evidence_btrace_run7_window1/2.txt`.

- **Opponent hold works:** battler 1's controller sits on BattleControllerDummy (`f1=0802E311`, 17/18 trace lines)
  during choice, then is released (`08035A99`); the opponent's turn never froze. No "BattleControllerDummy not found".
- **Still desyncs after turn 1.** `compare_traces.py` only reports the battle-end step
  (`('08015B59','081D886E',1) only in window 1: script paths differ`) - the real divergence is earlier:
  - Turn order is right in both (Player2, Speed 30, moves first).
  - Window 1: Player2 (opponent side) hits for 8, Player1 (player side) for 4.
    Window 2: Player2 (player side) hits for 4, Player1 (opponent side) for 9.
    The opponent side's hit is a crit in both games: base damage 10 vs 5 at instr 081D6932.
  - Timing: for the opponent-side attacker, 081D6926 -> 081D6932 takes ~370 frames (window 1: 2391 -> 2764,
    window 2: 3171 -> 3545; the "X used Tackle!" message) versus ~8 frames for the player side. Those are exec != 0
    frames the lock leaves alone, and VBlankCB_Battle advances gRngValue once per frame (measured in run 6), so the crit
    roll right after the message uses a drifted, unlocked value.
  - **Suggested fix:** cancel the VBlank Random() every battle frame from the frame callback
    (`x = ((x - 0x6073) * 0xEEB9EB65) & 0xFFFFFFFF`; verified in run 6 to give 0 steps/frame when idle, i.e. the frame
    callback runs before the VBlank handler), so no time-based drift exists at all; keep the logical-position seeding.
- Trace note: the `crit` field (0x02023D71) is 10 on every line, so that address is not the crit multiplier on RR.
- `compare_traces.py` should report the first differing dmg/hp per (main, instr, occurrence) step, not only path
  differences.
- Not reached yet: a faint replacement and a natural winner.

## 2026-10-24 - cloud: analysis of run 7 + changes
Re-reading `docs/evidence_btrace_run7_*` step by step (new `tools/compare_traces.py` output):
- **The RNG lock held.** gRngValue is identical in both windows at every exec=0 step, including the crit and damage
  steps (turn 1: ...081D6931 `4E36D729`, 081D6932 `161FF192`; turn 2: `9E63163A`, `C5F3B281`).
- **No critical hit happened.** 0x02023D71 *is* CFRU's gCritMultiplier (`include/new/ram_locs_battle.h`); CFRU stores
  10 for a normal hit and 15 for a crit (1.5x), so `crit=10` on every line means no crit in either game.
- **Same random value, double base damage for the opponent side** (10 vs 5 at 081D6932, both turns, both windows).
- **Where it comes from:** before the opponent-side battler's first move, both games run a Radical-Red-only battle
  script (`0x0900188A`-`0x090018B1`, expanded ROM) for battler 1, including ~280 frames of a battler-1 controller
  animation (`f1=08036501`). It never runs for the player side. Exactly 2x fits a +2 Attack boost (Gen 7+ X Attack),
  i.e. a trainer item/boost given to the opponent side. CFRU's open source has no such side rule, so it is RR's
  own trainer logic (possibly triggered by the AI's one unparked turn in run 7, or by trainer data).
Changes:
- `client/battle_trace.lua` now writes a `MONS` line at every turn start with both active Pokémon's full battle data
  (gBattleMons 0x58 bytes); `compare_traces.py` diffs them field by field (stat stages, ability, types...) and
  reports the first damage/HP difference among common steps, the first RNG difference, and path differences.
- Battler 1 is parked even on lag frames (safe mid-iteration), closing the window where the AI could answer once.
- VBlank Random() is cancelled on every battle frame (`vblank_cancel=0` disables), as suggested, so gRngValue never
  moves with time. Not the cause of run 7's desync, but removes a class of drift.
Next run: the `MONS` diff at turn 2 will show whether battler 1's Attack stage is +2 in each game. If the extra
script still runs with the AI fully parked, it is RR trainer logic tied to the opponent side, and the fix is to run
both games from the same side (the challenger as battler 0 on both machines) - see docs/phase4_battle.md.


## 2026-10-24 - real mGBA run 8 (local): VBlank cancel + lag-frame hold (250898f)

Same setup as run 7. Battles 8 and 9, both desync after turn 1. Traces: `docs/evidence_btrace_run8_window1/2.txt`
(both battles appended in one file each, which confuses `compare_traces.py` - clear the files between battles).

**Cause found on screen:** right before the opponent side's first attack, window 1 shows
**"The opposing Chikorita surrounded itself with its Z-Power!"** - the RR script 0x0900188A-0x090018B1 is the Z-Move
intro. The opponent side uses a Z-Move (Z-Tackle = Breakneck Blitz), which is why its damage is doubled (base 10 vs 5)
in both games. The player side never does (nobody pressed the Z button). So the remote choice is applied as
"use move slot X" but the opponent controller still turns it into a Z-Move.

Answers to the 4 questions:
1. `compare_traces.py` (battle 8 alone): RNG lock held at every exec=0 step; turn start #2 battle data differs only in
   HP (side 0: 0x0b vs 0x10, side 1: 0x10 vs 0x0c); no statStages difference (all stages 6 in the turn dumps). The
   "first damage/HP difference" it reports (frames 858/941, main 08015B59) is leftover gBattleMons from the previous
   battle before the new one starts - the tool should ignore steps before the battle-init callbacks.
2. The 0x0900188A script still appears (16 / 17 lines), once per battle, before the opponent side's first move.
3. The opponent is held (`f1=0802E311`) on every choice line where both sides are choosing (exec=3, 28 lines per
   window). On exec=2 lines (only battler 1 pending) it is released to 0x08035A99, where the remote reply is consumed -
   this release is the likely point where CFRU's opponent controller/AI decides to use the Z-Move.
4. Not reached (no battle stayed in sync past turn 1).

Suggested fix: when feeding battler 1's reply, also clear CFRU's per-battler Z-Move / Mega / Dynamax "to be used"
flags for battler 1 (and set them only if the remote decision says so, once the client sends a Z/Mega flag), or make
the opponent controller's move handler skip the AI's Z decision. The remote action format may need a `z` field so a
player who presses the Z button gets the same on both machines.

## 2026-10-24 - cloud: fix for run 8 (opponent side used a Z-Move)
- Cause (confirmed in CFRU source): CFRU's move reply is 8 bytes, `EmitMoveChosen` =
  {0x21, 10, slot, target, mega, ultraBurst, zMove, dynamax}, and `mega.c` ORs bytes 4-7 into gNewBS's per-battler
  "use it" flags when the reply is processed. The engine wrote only the vanilla 4 bytes for battler 1, so stale bytes
  from earlier buffer traffic turned the remote player's move into a Z-Move (once per battle -> only the first attack).
- Fix (`client/rr_battle.lua`): battler 1's replies always write all 8 bytes; the move reply carries the remote
  player's real Mega/Ultra/Z/Dynamax choice, read from the local player's own reply bytes 4-7 and sent as `g` in the
  decision. Pressing the Z button (or Mega/Dynamax) now applies on both machines.
- `tools/fake_battle.py` models the 8-byte reply, the OR into the Z flag, stale intro bytes in battler 1's buffer and
  a once-per-battle Z-Move; new test `test_z_move_pressed_by_a_player_is_used_on_both_machines`. 8/8 battle tests
  pass (3 runs).
- `tools/compare_traces.py` only looks at the first battle in each file and skips leftover pre-init lines; on the
  run-8 traces it now reports the Z-Move script step (0x0900188A) as the first difference, with equal RNG.

## 2026-10-24 - cloud: talk-to-player menu (beta UX)
- Pressing A on another player's character now opens the game's own UI: text box, TRADE CENTER / COLOSSEUM /
  EXIT menu, YES/NO box and party screen (details in docs/visuals.md). Trade: offer via the party screen, the other
  player answers by talking to you (picking their Pokémon confirms for them), you confirm by talking again.
  Battle: COLOSSEUM sends a challenge, the other player accepts by talking to you. Talking while waiting cancels.
- `tools/fake_script.py` interprets the script opcodes used; `tests/test_talk.py` (3 tests: trade, battle,
  cancel/exit) passes 3/3 runs. All suites pass. Not yet run in the real game.

## 2026-10-24 - report on the 250898f battles (run 8 traces, per battle)
Source: the local session's real-mGBA battles 8 and 9 on 250898f with `client/battle_trace.lua` in both windows
(`docs/evidence_btrace_run8_window1/2.txt` = the two windows' `work/btrace_pilot*.txt`), split per battle and compared
with the current `tools/compare_traces.py`.
1. **compare_traces.py, battle 8:** RNG lock held at every common exec=0 step; no statStages difference (the only
   turn-start battle-data difference is HP at turn 2: side 0 0x0b vs 0x10, side 1 0x10 vs 0x0c); first damage/HP
   difference at the step 0x0900188A with equal RNG (709FC712). **Battle 9:** same picture (equal RNG 370CBA13;
   HP 0x0c vs 0x10 / 0x10 vs 0x0b at turn 2; no statStages difference).
2. **0x0900188A still appears:** once per battle in each window (15-16 lines of the 0x090018xx script), always before
   the opponent side's first move. Run 8 identified it on screen as the Z-Move intro ("surrounded itself with its
   Z-Power!").
3. **Opponent hold:** f1=0802E311 on every choice line where both sides are choosing (exec=3: 14/14 in each battle,
   each window). On exec=2 lines (only the opponent still pending, after the remote reply was written) the controller
   is the real one (0x08035A99) by design - that is the moment the reply is consumed.
4. **Sync:** neither battle stayed in sync past turn 1, so the faint replacement and natural winner could not be
   tested on 250898f. Cause: the Z-Move from stale bytes 4-7 of CFRU's 8-byte move reply, fixed in d6a72b1 (not yet
   run in the real game). Next real-game step: `docs/beta_checklist.md` section 1.


## 2026-10-24 - real mGBA run 9 (local): Z-Move fix (39b3753), beta checklist section 1

Battles 10 and 11 (traces per battle; battle 11: `docs/evidence_btrace_run9_b11_window1/2.txt`). Both desync after
turn 1, but now only ONE game uses the Z-Move:
- Battle 10: `0x0900188A` in window 1 only (1 line), window 2: 0. Window 1's opponent hit for base 10 (Z), every other
  hit was base 5. compare_traces: "random value equal at every common exec=0 step (RNG lock held)"; first difference
  at 080155C9/081D6926 #2, "same random value, different result"; first step only in window 1 = 0x0900188A.
- Battle 11 (controlled order): window 2 picked its move first, window 1 picked ~6 s later. The Z-Move again happened
  only in **window 1 - the game that was still choosing when the other player's choice arrived**. Window 2 (chose
  first, remote arrived after) played normal Tackles. compare_traces output: RNG lock held; first difference
  080155C9/081D6926 #2, same random value, different result.
- Opponent hold: `f1=0802E311` on every line where both sides choose (exec=3); release to 0x08035A99 only on the
  exec=2 lines that consume the remote reply - in both games.
Conclusion: the run-8 fix clears the opponent's Z flag on one code path only. When the remote decision is already in
hand while the local player is still choosing, the reply is written early and the opponent controller still sets
the Z-Move (or the flag is cleared before CFRU sets it). Clear the flag on every path, at the moment battler 1's
reply is consumed (exec bit 2 -> 0), not when the remote decision arrives.
Section 2 (talk-to-player menu) not tested yet.
