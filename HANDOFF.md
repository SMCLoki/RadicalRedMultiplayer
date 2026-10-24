# Handoff — read this first

You are taking over the Radical Red online multiplayer project on the user's Windows PC (mGBA + ROM available).
A cloud Claude session did the heavy lifting that doesn't need the emulator; your job is the in-emulator part.

1. Read `GOAL.md` (goal prompt, rules, STOP conditions). Rooms now hold **8 players** (user request).
2. Read `docs/progress.md` (what is VERIFIED / TESTED / WRITTEN / TODO) and `docs/local_checklist.md`
   (your step-by-step list). Then `docs/protocol.md`, `docs/ram_map.md`, `docs/phase4_battle.md`.
3. Code: branch `claude/lucid-tesla-es8owg`
   - `server/rr_server.py` relay server (stdlib), `server/test_server.py` (34 tests), `server/soak.py`
   - `client/rr_mp.lua` mGBA client (load via Tools > Scripting > File > Load script)
   - `tools/mgba_mock.py` + `tests/test_client.py`: runs rr_mp.lua in a fake mGBA (needs `pip install lupa`;
     ask the user before installing)
   - `start_server.bat`, `start_client.bat`, `run_tests.bat`

Status in one line: Phases 1-3 are written and pass automated tests (server real, client in a modelled mGBA);
none of it has run in the real emulator yet. Phase 4 has the network side done; the game-engine hooks are
open research. Start with `docs/local_checklist.md` §0-§1.

Local-only files (gitignored, never push): `work/rr.gba`, `work/rr2.gba` (ROM copies), `work/rr.ss1-3`,
`work/states/*.ss` (lab_after_starter, overworld_lab, rival_battle_menu), `work/mp_*` (client config,
identity, trade journal, status, locks), `server/data/` (trade ledger, battle log).

All Lua scripts now find `work/` relative to their own location (`client/../work/`); no hard-coded paths.
