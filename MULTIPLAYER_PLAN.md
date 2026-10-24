# Radical Red Multiplayer — Plan & Goal Prompt

## What we have (from the folder)
- Patched Radical Red ROM (32 MB, FireRed US base) — `1636 - Pokemon Fire Red (U)(Squirrels) (patched).gba`
- mGBA 0.10.5 installer (has Lua scripting + sockets)
- **No source code.** Radical Red ships as a binary patch (CFRU + DPE engine). So we cannot recompile; every approach is either (a) an emulator-side bridge, or (b) a binary ASM patch.

## Architecture decision (recommended)
**Emulator-side bridge, not ROM surgery.**

```
 mGBA #1 (Lua client) ──ws──┐
                            ├── Relay server (Node/Python, rooms, auth, logging)
 mGBA #2 (Lua client) ──ws──┘
```

- Lua script reads/writes emulated RAM each frame (party, position, battle state).
- Relay server only forwards validated messages between room members.
- Why not a ROM patch of the link-cable code: the GBA serial (SIO) protocol is timing-critical, RR has no symbols, and it would take months for a worse result. Keep ROM-patching as a Phase 7 stretch.

## Phases

### Phase 0 — Recon & test harness (do first, everything depends on it)
1. Record ROM SHA-1; confirm mGBA 0.10.5 Lua console works (`Tools > Scripting`) and `socket` API is available.
2. Build a RAM map for **this** ROM (do not trust vanilla FireRed addresses — RR relocates/expands things). Verify each by watching memory while playing:
   - party (6 × mon struct, vanilla 100 bytes — check RR changes to species IDs > 0x19B, move IDs, ability/nature fields)
   - player map group/num, X/Y, facing, movement state, sprite/outfit
   - battle flags, `gBattleTypeFlags`, enemy party, RNG state
   - save block pointers (SaveBlock1/2 are pointer-relocated in FR)
3. Make 3 save states: overworld, pre-trainer-battle, mid-battle. Automate loading them for repeatable tests.
4. Deliverable: `docs/ram_map.md` with address, size, how verified.

### Phase 1 — Networking foundation
- Relay server with room codes, display names, heartbeat/disconnect handling, versioned JSON (later binary) protocol.
- Lua client: connect, reconnect, non-blocking socket poll in `frame` callback, never block the emulator.
- Hello/ping demo: two emulators show each other's name in a Lua overlay.
- Deliverable: `server/`, `client/rr_mp.lua`, `docs/protocol.md`.

### Phase 2 — Overworld presence (see other players)
- Broadcast map, X/Y, facing, walking state at ~10 Hz; interpolate.
- Render remote players by borrowing a spare object-event slot (preferred) and driving its coords/sprite; fallback is an on-screen minimap/list overlay if the slot approach proves unstable.
- Only show players on the same map; despawn cleanly on map change/disconnect.
- Risk: object-event slot collisions with scripted NPCs → pick slot at runtime, release on map load.

### Phase 3 — Trading
- Offer/accept UI via Lua overlay + hotkeys (or an in-game NPC trigger).
- Server validates species/moves/level against an RR legality table (no hacked mons by default).
- Atomic swap: both sides confirm → both write received mon and clear sent mon in the same frame window; transaction ID + rollback on disconnect. Trade evolutions fire normally.
- Deliverable: no-dupe/no-loss under forced disconnect tests.

### Phase 4 — PvP battles (the hard part)
Approach: **deterministic lockstep.**
1. Challenge handshake; exchange party snapshots + a shared RNG seed.
2. Each client writes the opponent's party into its enemy-party RAM and launches a scripted trainer-style battle (flags set to no-reward/no-catch/no-exp).
3. Hook the point where the AI would choose an action; instead feed the opponent's action received over the network (fallback if Lua breakpoints are unavailable in 0.10.5: poll the battle state machine each frame and inject the choice into the battle-controller RAM).
4. Both sims must stay bit-identical → per-turn state hash exchange; on mismatch, host state wins and the match is flagged.
5. Handle: forfeit, disconnect (timeout → loss), turn timer, Mega/Z/Tera-style RR mechanics, held items, abilities that read RNG.
6. Restore each player's real party and position afterward (parties are temporary-copied; never touch the real save during the match).
- Deliverable: 20 consecutive 6v6 battles with zero desyncs.

### Phase 5 — Quality of life
- Chat (emoji-free text overlay), friend list, rematch.
- Showdown-style team import for battles, tier/clause rules (RR community uses Smogon-ish rules).
- Spectator mode (read-only client following the lockstep stream).
- Ladder/Elo on the server.

### Phase 6 — Hardening & packaging
- Anti-cheat: server-side legality checks, rate limits, state-hash validation, signed room tokens.
- One-click launcher (`start_client.bat`) that opens mGBA with the ROM + script; docs for hosting a server.
- Soak test: 4 clients, 2 hours, random disconnects.

### Phase 7 — Stretch
Co-op raids vs. boss mons, shared hub town, ROM patch to add an in-game NPC/menu entry instead of hotkeys.

## Key risks
| Risk | Mitigation |
|---|---|
| RR memory layout differs from vanilla | Phase 0 verification is mandatory; nothing downstream until the RAM map is done |
| Lua API lacks breakpoints/watchpoints in 0.10.5 | Frame-polling fallback; consider upgrading mGBA if a newer stable release is available |
| Battle desync | Seeded RNG, per-turn hash check, host-authoritative recovery |
| Item/mon duplication | Two-phase commit trades, transaction IDs, server legality checks |
| Save corruption | Never write to save mid-session; work on a copy; auto-backup before each session |
| Emulator timing/lag | Non-blocking I/O, turn-based sync tolerates latency; overworld uses interpolation |

---

# GOAL PROMPT (copy–paste into a new Claude Code session)

```
GOAL: Build a fully working online multiplayer system for Pokémon Radical Red
(the patched ROM in C:\Users\you\Desktop\Radical Red) running in mGBA 0.10.5.

CONSTRAINTS
- No ROM source exists. Do NOT try to recompile. Use an mGBA Lua client + a
  relay server (Node.js or Python, your choice; justify it). ROM binary patches
  only as a last resort and only to a COPY of the ROM.
- Never modify the original ROM or the user's save files. Work on copies and
  back up saves before every test session.
- Verify every memory address empirically on THIS ROM. Vanilla FireRed
  addresses are hypotheses, not facts. Document each in docs/ram_map.md with
  how it was verified.
- The Lua client must never block the emulator; all socket I/O is non-blocking.

DELIVERABLES (in order; do not start a phase until the previous passes its test)
1. Phase 0: RAM map + automated save-state test harness.
2. Phase 1: relay server (rooms, names, heartbeat) + Lua client; two emulators
   see each other connected.
3. Phase 2: overworld presence — remote players visible and moving on shared maps.
4. Phase 3: trading — atomic, no dupes/losses even with forced disconnects;
   legality-checked on the server.
5. Phase 4: PvP — lockstep deterministic battles with per-turn state hashing,
   forfeit/disconnect/timer handling, real party restored afterward.
6. Phase 5/6: chat, team import, ladder, anti-cheat, one-click launcher, README.

DEFINITION OF DONE
- Two (and then four) mGBA instances on different machines can join a room by
  code, see each other on the map, trade a Pokémon, and finish 20 consecutive
  6v6 battles with zero desyncs.
- Killing a client or the network mid-trade never duplicates or deletes a Pokémon.
- A 2-hour soak test with 4 clients and random disconnects completes without
  crashes or save corruption.
- README lets a non-programmer set up server + client in under 10 minutes.

WORKFLOW
- Keep a running checklist (use the task list). After each phase, run the
  tests, write a short results note in docs/, and report what is verified vs.
  assumed. If a phase's approach is infeasible (e.g. object-event rendering),
  state why, pick the documented fallback, and continue — don't stall.
- Ask me before: installing software, opening network ports, or anything that
  touches files outside this folder.
```
