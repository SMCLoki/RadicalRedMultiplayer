# Radical Red Online (WIP)

Online multiplayer for Pokémon Radical Red, made of an mGBA Lua client and a small relay server.
No game files are included: bring your own legally obtained ROM.

Status: Phase 0 (RAM map + test harness) done. See `docs/ram_map.md` and `MULTIPLAYER_PLAN.md`.

## Layout
- `client/` - mGBA Lua scripts (`probe.lua` RAM verifier, `pilot.lua` test autopilot, `nettest.lua` socket check)
- `server/` - relay server (Phase 1)
- `docs/` - RAM map, protocol, progress log
- `tools/` - helper scripts
- `GOAL.md` - autonomous build prompt; `MULTIPLAYER_PLAN.md` - plan

## Requirements
mGBA 0.10.5+, Python 3.12, a Radical Red ROM (sha1 964f951a0fdaf209e4ea1344883ef0d557bb3a80) placed at `work/rr.gba`.
