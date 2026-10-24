# /goal — Radical Red Online Multiplayer (autonomous run)

Paste everything in the code block below as the goal for a Claude Code session opened in this repo.

```
GOAL
Build working online multiplayer for Pokémon Radical Red (FireRed v1.0 base,
ROM sha1 964f951a0fdaf209e4ea1344883ef0d557bb3a80) running in mGBA 0.10.5,
using an mGBA Lua client + a relay server. Work autonomously phase by phase
until the Definition of Done is met or you hit a STOP condition.

READ FIRST (source of truth, keep them updated)
- MULTIPLAYER_PLAN.md  - phases, risks
- docs/ram_map.md      - VERIFIED addresses + how they were verified
- docs/protocol.md     - wire protocol (create in Phase 1 if missing)
- docs/progress.md     - running log: what is done / verified / assumed / next

KNOWN FACTS (verified in Phase 0 - don't re-derive)
- Party data on Radical Red is UNENCRYPTED: 100-byte struct, checksum field 0,
  substructs fixed G@0x20 A@0x2C E@0x38 M@0x44.
- gPlayerParty 0x02024284, count 0x02024029, gEnemyParty 0x0202402C,
  gBattleTypeFlags 0x02022B4C, gSaveBlock1Ptr 0x03005008 (pos x/y s16 @+0/+2,
  mapGroup @+4, mapNum @+5), gMain 0x030030F0, gObjectEvents 0x02036E38
  (0x24 each, [0]=player, currentCoords @+0x10 = pos+7), gRngValue 0x03005000.
- mGBA 0.10 Lua: socket.connect(host,port) works (raw TCP only, no WebSocket;
  connect itself blocks). "received" callback keeps firing with
  err="disconnected" after peer close -> close and drop the socket.
- No on-screen canvas in 0.10; use console:createBuffer() text panels.
- mgba-sdl -g GDB stub does not work on this build.

ENVIRONMENT / HARNESS
- Windows 11, Python 3.12, Node 24, mGBA at C:\Program Files\mGBA\mGBA.exe.
- Run the ROM COPY at work/rr.gba (gitignored). Never touch the original ROMs.
- Drive the game with client/pilot.lua (commands in work/pilot.txt:
  HOLD/MASH/WAIT/SAVE/LOAD). mGBA keys: A=X, B=Z, Start=Enter.
- Read state via client/probe.lua -> work/probe_out.txt.
- Save states: work/states/*.ss (lab_after_starter, overworld_lab,
  rival_battle_menu).
- Computer use: click the game window before key input; NEVER use
  open_application for mGBA (spawns an empty second instance). Prefer pilot.lua.
- For a 2nd client, launch another mGBA via Start-Process with a second ROM
  copy (work/rr2.gba) so saves don't collide.

PHASES (each ends with tests passing + docs/progress.md updated + git commit)
1. Networking: Python asyncio stdlib-only relay server (server/), newline-JSON
   over TCP, rooms, names, hello/welcome/join/leave, ping/pong, 10s timeout,
   rate limit, game-hash check. Lua client client/rr_mp.lua with tiny JSON lib,
   reconnect backoff, status in a scripting text buffer + work/mp_status_*.txt.
   Tests: server/test_server.py (pytest-free, asyncio). Demo: two mGBA
   instances list each other.
2. Presence: broadcast map/x/y/facing ~10Hz; render remote player by driving a
   spare object-event slot; fallback = text-panel list. Verify by screenshots.
3. Trading: two-phase commit with txn IDs, server validation, rollback on
   disconnect. Confirm PC-box mon format first. Test kill-mid-trade = no dupe/loss.
4. PvP: lockstep battles - shared seed, inject opponent party into gEnemyParty,
   feed remote action at AI-choice point (find via memory polling of battle
   state), per-turn state hash. Restore real party after. 20 battles, 0 desyncs.
5. QoL + hardening: chat, team import, rules, launcher .bat, README, soak test.

RULES
- Verify, don't assume: every new address goes in ram_map.md with evidence.
- Small commits per milestone with clear messages. Never commit ROMs, saves,
  save states (.gba .sav .ss* .zip) or anything in work/.
- Keep the emulator responsive: no busy loops in frame callbacks.
- If an approach fails twice, write why in progress.md, switch to the
  documented fallback, continue.

STOP AND ASK ME BEFORE
- git push / creating or changing a GitHub repo or anything public
- installing software or pip/npm packages
- opening a server port to anything beyond localhost / deploying to a cloud host
- deleting files outside work/, or any action outside this folder

DEFINITION OF DONE
- 2 (then 4) clients on different machines join a room, see each other move,
  trade a Pokémon, and finish 20 consecutive 6v6 battles with zero desyncs.
- Killing a client mid-trade never duplicates or deletes a Pokémon.
- 2-hour 4-client soak test with random disconnects: no crash, no save damage.
- README gets a non-programmer running server + client in under 10 minutes.
```
