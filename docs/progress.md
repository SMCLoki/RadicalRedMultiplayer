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
