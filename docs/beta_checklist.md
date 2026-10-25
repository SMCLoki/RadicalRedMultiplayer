# Beta checklist (one real-mGBA session, two windows)

> Result 2026-10-24 (local session, runs 10-11 in progress.md): sections 1-3 PASS on one PC. Section 4 (two PCs) not
> run yet. Section 1.4 (Z button) not testable with the test saves.

Goal: confirm the beta works end to end. Branch `claude/lucid-tesla-es8owg`, newest commit. Clear `work/btrace_*.txt`
between battles (`compare_traces.py` reads the first battle in each file). Record results in `docs/progress.md`.

## 0. Setup
- `start_server.bat`; two mGBA windows (`work/rr.gba`, `work/rr2.gba`), both standing on the same map.
- Load `client/rr_mp.lua` in both, and `client/battle_trace.lua` (window 2: set `PILOT_FILE` first).
- Give each save at least 2 Pokémon with different Speed (or use the RAM edits from runs 6-8).

## 1. PvP battle sync (fixes: run-8 Z-Move bug, d6a72b1)
1. Window 1 `mp.battle("Player2")`, window 2 `mp.fight()`.
2. Play until someone wins. Expected: no "The opposing X surrounded itself with its Z-Power!", HP equal in both
   windows after every turn, a faint replacement in sync, a natural winner, both back on the map with party,
   money and battle-style option restored.
3. `python tools/compare_traces.py work/btrace_pilot.txt work/btrace_pilot2.txt` -> expected "no damage/HP
   difference" and "RNG lock held". Paste the full output into progress.md either way.
4. Press the Z button in one window on a move with a Z-Crystal holder (if available): both games should show
   the Z-Move.
5. 3 more battles. (Speed ties are handled since run 11.)

## 2. Talk-to-player menu (9f0930f)
1. Walk next to the other player's character and face it; press A. Expected: "<name>: Let's link up! Trade or
   battle?" and the TRADE CENTER / COLOSSEUM / EXIT menu.
2. TRADE CENTER -> party screen opens -> pick a Pokémon. Other window: talk to you -> "<name> offers <SPECIES>!
   Trade one of yours for it?" -> YES -> party screen -> pick. Window 1: talk again -> "... offers X for your Y.
   Trade?" -> YES. Both parties swap; save in both.
3. COLOSSEUM -> other window talks to you -> "wants to battle! Accept?" -> YES -> battle starts in both.
4. Talk while waiting -> "Cancel your request?" -> YES cancels. EXIT does nothing.
5. If the menu shows other words or the party screen doesn't open, RR changed list 39 or special 159 (160 in older builds hung): note what
   appears; set `menu_list=` / `special_choose_party=` in `work/mp_config.txt` to try others.

## 3. Everyday play
- Walk/run around together on several maps, warp in and out, open menus, battle a wild Pokémon and return:
  characters keep following, nothing freezes.
- Stop and restart the server: both reconnect.
- Save, reset, reload after a trade: the traded Pokémon is still there.

## 4. Two PCs (the real target)
- Same network: host presses Host a game + Play in the launcher (allow the Windows firewall prompt on Private
  networks); the second PC presses *Find LAN games* (or types the address the host's launcher shows) and Play.
  Repeat a trade and one battle.
- Over the internet (no outside services): the host's router must let TCP 8765 in, by UPnP (launcher does it;
  *Retry router* after turning UPnP on) or a manual port forward to the host PC's LAN address. The friend joins
  with the public address the launcher shows (or the one on the router's status page). If the launcher reports
  carrier-grade NAT or a double router, follow its text.

When 1-3 pass, the beta is ready to hand to friends (README quick start).
