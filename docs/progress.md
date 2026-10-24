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
- Recommendation for the local session: `pip install lupa` (with the user's OK) so `tests/test_client.py`
  actually runs on Windows instead of skipping.

