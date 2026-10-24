# Handoff — read this first

You are taking over the Radical Red online multiplayer project.

1. Read GOAL.md and paste/follow its prompt. It contains every verified fact from Phase 0.
2. Then read docs/ram_map.md (verified addresses) and MULTIPLAYER_PLAN.md.
3. Status: Phase 0 DONE. Phase 1 (relay server + client/rr_mp.lua) NOT started.
   docs/protocol.md is a draft spec for Phase 1 (newline JSON over TCP) - review before using.

Local-only files (gitignored, never push): work/rr.gba (ROM copy), work/rr.ss1-3 (mGBA state slots),
work/states/*.ss (named copies: lab_after_starter, overworld_lab, rival_battle_menu).
mGBA 0.10.5 is installed at C:\Program Files\mGBA\mGBA.exe. Open work/rr.gba, then
Tools > Scripting > Load script: client/probe.lua and client/pilot.lua.

Paths inside the Lua scripts point to C:/Users/you/Desktop/Radical Red/work/ - update them
to this folder's work/ if you run from here.

Not yet on GitHub. Ask the user before creating a repo or pushing.
