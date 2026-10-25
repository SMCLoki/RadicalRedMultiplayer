# Checklist for the local (Windows, mGBA) Claude session

The cloud session wrote and tested everything that can run without the emulator. It **cannot** run mGBA or the
ROM, so nothing below has been seen working in the real game yet. `tools/mgba_mock.py` models the mGBA 0.10
API (checked against mGBA 0.10 source: `src/script/engines/lua.c`, `src/core/scripting.c`), but a model is
not the emulator. Work top to bottom; log results in `docs/progress.md`; commit after each step.

## 0. Get the code and run the offline tests (5 min)
```
git fetch origin && git checkout claude/lucid-tesla-es8owg
python server\test_server.py        # 34 tests, stdlib only
python server\soak.py --minutes 1   # 8 bots, random disconnects, conservation check -> PASS
python tests\test_client.py         # Lua client end-to-end; skipped unless `pip install lupa` (ask the user first)
```
`run_tests.bat` runs all three.

## 1. Phase 1 in the real emulator (two clients, one PC)

> 2026-10-24 local run: two mGBA windows joined LOBBY and listed each other with position. See progress.md.
1. `start_server.bat` (keeps a console window open; leave it running).
2. Open mGBA with `work\rr.gba`, load a save/state in the overworld, Tools → Scripting → File → Load script →
   `client\rr_mp.lua`. Open a second mGBA with `work\rr2.gba` (`Start-Process`, never `open_application`) and
   load the same script. The second window claims profile 2 automatically (lock files `work\mp_lock_N.txt`).
3. Expect in each scripting window: buffer **"RR Online"** listing the other player, and
   `work\mp_status_1.txt` / `mp_status_2.txt` showing both. Server console shows two `joined room LOBBY`.
4. Verify and record:
   - [x] `socket.connect` works from `rr_mp.lua` (connect status in the panel)
   - [x] the "RR Online" text buffer appears and refreshes (`console:createBuffer`, `setSize`)
   - [ ] `emu:checksum()` returns 4 bytes → `game` id looks like `AGB-BPRE:xxxxxxxx` in the server log
   - [x] typing `mp.help()`, `mp.who()`, `mp.say("hi")` in the console works (global `mp` reachable from the REPL)
   - [ ] no stutter: frame rate stays 60 with both connected (Tools → show FPS)
   - [x] stop the server: panel shows offline/retrying; restart it: both reconnect within ~10 s
   - [ ] pause one emulator > 10 s: the other sees it leave (timeout) and rejoin after unpausing
   - [ ] reload the script in the same window: no duplicate client (old instance is shut down)

## 2. Phase 2 presence
**Player visuals are implemented: follow `docs/visuals.md` → "Real-emulator verification".**
Tool: load `client/ramsearch.lua` in the same Scripting window (commands: `rs.facing()`, `rs.start(size)`,
`rs.filter(...)`, `rs.list()`, `rs.snap/diff`, `rs.watch`, `rs.callbacks(true)`; output in `work/ramsearch*.txt`).
- [x] Walk around: the other panel's `map g.n xX yY` follows, `<< HERE` when on the same map.
- [x] Verify facing: `objectEvents[0]+0x18` low nibble while facing down/up/left/right (expect 1/2/3/4).
      Update `sampleState()` and `docs/ram_map.md`.
- [ ] Research drawing remote players with a spare object-event slot: find `graphicsId`, `spriteId`, the
      `gSprites` array, and whether activating a free `gObjectEvents` slot spawns a sprite (vanilla: it needs
      `TrySpawnObjectEvent`, so probably not — consider re-using an existing sprite/NPC and moving it, or
      editing a map's object template list before the map loads). If two attempts fail, keep the text panel
      (already implemented) and document why.

## 3. Phase 3 trading
Party structs are plain on RR (verified Phase 0). The trade path writes whole 100-byte structs.
1. Give each save ≥2 Pokémon. In window 1: `mp.party()`, `mp.trade("Player2", 1)`. Window 2: `mp.accept(2)`.
   Both: `mp.confirm()`.
2. Verify:
   - [x] the received Pokémon shows correctly in the party screen and summary (nickname, OT, moves, stats)
   - [ ] it can battle and gain exp normally; the game does not "repair"/reject it (no Bad Egg)
   - [ ] save in-game, reset, reload: still there
   - [x] **kill mid-trade**: after `mp.confirm()` on one side only, kill that mGBA (Task Manager) → other side
         gets "trade cancelled (disconnect)", both parties unchanged
   - [x] **kill after commit, before saving**: complete a trade, kill one mGBA without saving, restart it and
         load the script → panel says the trade was re-applied from the journal; save
   - [ ] reload an older save after a trade: within 5 s the given Pokémon is replaced again (anti-dupe)
3. Known limits to decide on: trade evolutions don't trigger (would need the engine's evolution scene);
   received Pokémon obey based on OT/badges as usual; PC-box trading not implemented (format unverified).

## 4. Phase 4 battles
Follow `docs/phase4_battle.md`. Network side is ready; implement `M.engine`.

## 5. Phase 5/6 polish
- [ ] Time a non-programmer walkthrough of README.md (goal < 10 min).
- [ ] 8 clients: two PCs × 4 mGBA windows (profiles 1-4) or friends joining through the host's router (UPnP / port forward); play for 2 h with random
      disconnects (`server/soak.py` already covers the server side for 8 bots).
- [ ] Optional: species-name table for the panel (RR species ids differ from vanilla; read names from ROM).
