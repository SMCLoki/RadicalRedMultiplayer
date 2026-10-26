# Incident: game unresponsive after trainer / rival battles (all players)

**Status:** fixed and verified in real play on 2026-10-25 (commit on `main`, release v0.4.0 files replaced).
**Severity:** high. Every player was affected, and the story could not progress without loading an earlier state.
**Component:** `client/rr_visuals.lua` (the map patch that shows other players as characters).

## Symptom
- After trainer battles and "boss" rival encounters, the battle ended and the text finished. Then the game stopped
  responding: the player could not move or open the menu.
- The emulator itself kept running (normal fps, the client script kept writing its status file). The game's own
  event script was stuck waiting.
- It happened for every player, with or without other players on the same map. In the confirmed case, no other
  player was online.

## Root cause
To draw other players, the client patches the current map in RAM:
- `gMapHeader.events` points at a copy of `MapEvents` with a larger `objectEventCount`, stored in SaveBlock1
  template slot 63;
- extra object templates are added;
- `gMapHeader.mapScripts` gets an ON_FRAME table for auto-spawn.

The patch was guarded by a "settle gate": patch a map only after the player has been free to move (not
`F_FROZEN`) for 60 frames. That gate looked only at the player object's frozen flag.

When a battle ends, the game returns to the field and resumes the trainer's script (post-battle text, rival walking
away, `removeobject`, ...). For a stretch of that resumed script the player object is not flagged frozen, even
though `sScriptContext1` is still running. The gate therefore patched the map in the middle of the event script.
The changed MapEvents and map-script pointers then left the script waiting forever.

## Evidence
- In the user's own frozen game (no other players online, so no characters involved), the command
  `mp.visuals(false)` was sent through the launcher command file. That calls `V.shutdown()`, which runs
  `unpatch()`: the `gMapHeader.events` / `mapScripts` pointers go back to the ROM originals.
  **The stuck post-battle scene continued immediately.** That isolates the cause to our patch being active during
  the game's script.
- The script-engine state was verified in-game:
  - `sScriptContext1` is at `0x03000EB0`: `stackDepth` +0, `mode` +1 (0 = idle, 1 = bytecode, 2 = native wait),
    `nativePtr` +4, `scriptPtr` +8, call stack +0x0C;
  - idle reads `mode=0`; talking to an NPC reads `mode=2` with `scriptPtr` in ROM (`0x081A4E52`).

## Fix (`client/rr_visuals.lua`, `V.tick`)
1. **The game goes first.** Each frame, the client checks whether a *game* script is running: `mode ~= 0`, and
   neither `scriptPtr` nor any call-stack entry lies in our own RAM script area (SaveBlock1 template slots 49-63,
   which hold the talk-to-player menu and the auto-spawn script). While it runs:
   - `unpatch()`: the map is exactly the game's own;
   - `hideAllPuppets()`: other players' characters are made invisible and moved far off the map, so they can't
     block an NPC's scripted path;
   - the settle timer resets, and the function returns before touching anything else.
2. **Re-patch only when idle.** After the script ends and the player has been free for 60 frames, the map is
   patched again and the characters come back (`drive()` clears the invisible flag and moves them to their real
   positions).
3. **Watchdog / diagnostics.** If a game script makes no progress for 8 s (same `scriptPtr` / `nativePtr` /
   `mode`), the client writes to the session log (`work/mp_log_<profile>.txt`): the script address, the next 12
   bytes, the native wait function, the call stack, the map and the number of characters. Future reports then
   point straight at the cause.
4. Script detection and the watchdog run *before* the settle gate, so they work even when the map is not patched
   yet. Previously the post-battle path returned early and never reached them.

Our own scripts (talk-to-player trade/battle menu, auto-spawn) are recognised by address and keep the map patched,
so talking to another player still works.

## Related fixes in the same change set
- **Autosave guard:** `autosave()` now checks the game state directly: `gMain.callback2 == CB2_Overworld`
  (`0x080565B5`), no battle flags, and the player object active and not frozen. The old check went through the
  battle module and could be skipped, so autosaves taken at the title screen replaced good ones.
- **Tracing:** opt-in per-frame trace markers in `rr_mp.lua` `tick()` (create `work/mp_trace_on`), so a stall in the
  client's own frame callback can be located.

## Verification
- In real play, the user's game (after reload with the fix) completed trainer / rival battles and their
  post-battle scenes normally.
- Not yet verified by the other players. **Every player must update**, because each player's own client patched
  their own map: run the new `RadicalRedOnline-Setup-0.4.0.exe` from the release, or copy `client/rr_visuals.lua`
  and `client/rr_mp.lua` into the install's `client` folder.

## Notes for future work
- Any feature that writes to game RAM the game's own scripts read (map headers, templates, vars) must stay inside
  this "game goes first" rule: no writes while `sScriptContext1.mode ~= 0` for a non-own script.
- Earlier incidents on the same theme:
  - commit `9b3765f`: settle gate, which fixed the new-game options / Mom's event;
  - commit `490e245`: skip maps with frame scripts.
  This incident is the case those two did not cover: a script still running while the player is not flagged
  frozen.
