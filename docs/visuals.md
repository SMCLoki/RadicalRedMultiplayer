# Player visuals: seeing each other on the map

Status: **written and tested against a model of the FireRed engine (`tools/fake_overworld.py`); not yet run
in the real emulator.** Code: `client/rr_visuals.lua` (loaded automatically by `client/rr_mp.lua`).
Tests: `tests/test_visuals.py` (needs `pip install lupa`).

## What the player sees
Other players on the same map appear as an overworld character that walks, runs and turns like they do,
with the game's own animations. They show up on your screen **after you take a step** (see Limits).
By default each remote player is drawn with *your own* character sprite (see `puppet_gfx` below).

## How it works
mGBA 0.10 Lua cannot call game functions or draw on screen, so the script gets the game to do the drawing.
All struct layouts and addresses come from pret/pokefirered (`include/global.fieldmap.h`, `include/sprite.h`,
`src/event_object_movement.c`, symbols branch). Vanilla FireRed's `gObjectEvents`, `gPlayerAvatar`, `gMain` and
`gSaveBlock1Ptr` match the addresses already verified on Radical Red, so the others are expected to match too;
the script checks them before writing anything.

| Address (vanilla FR US) | Use | Check before use |
|---|---|---|
| `gMapHeader` 0x02036DFC (`.events` at +4) | point at our MapEvents copy | localIds of `events->objectEvents[]` (ROM) equal SaveBlock1 templates |
| `gSprites` 0x0202063C (0x44 each) | puppet sprite position, `data[2]` | every active object's sprite is `inUse`, has a ROM callback and `data[0]` = its object index; else EWRAM scan |
| `gObjectEvents` 0x02036E38 (0x24 each) | find and drive puppets | player object active, `isPlayer`, localId 0xFF |
| SaveBlock1 + 0x8E0 (`objectEventTemplates[64]`, 0x18 each) | puppet templates in slots `count..count+6`; MapEvents copy in slot 63 | via the gMapHeader check |

1. **Spawning.** `TrySpawnObjectEvents` (called on every camera move) spawns any template in
   `gSaveBlock1Ptr->objectEventTemplates[0 .. gMapHeader.events->objectEventCount-1]` that is in view, has
   flag 0 or a clear flag, and isn't already spawned. The script writes 7 templates after the map's own ones
   (localId 0x70-0x76, movement type NONE, script NULL, flag 0, graphics = puppet sprite, x/y = the remote
   player's position; unused ones at x = y = -1000) and points `gMapHeader.events` at a RAM copy of the
   map's `MapEvents` with `objectEventCount + 7`. The copy sits in template slot 63, which maps never use.
   `gMapHeader` is reloaded from ROM on every map load; the script re-patches on the next overworld frame.
2. **Moving.** Exactly what `ObjectEventSetHeldMovement` does: `movementActionId` = action,
   `heldMovementActive` = 1, `heldMovementFinished` = 0, `frozen` = 0, sprite `data[2]` = 0. The engine runs
   the action and sets `heldMovementFinished`. Actions used: `FACE_*` 0x00-0x03, `WALK_NORMAL_*` 0x10-0x13,
   `WALK_FAST_*` 0x1D-0x20 (when the remote player ran, i.e. sent action 0x1D-0x20/0x3D-0x40, or the puppet
   is ≥3 steps behind).
3. **Network.** Each presence update now carries the sender's last 8 steps `st: [[seq, x, y, facing,
   movementActionId], ...]` (sampled every frame from `gObjectEvents[0]`), the on-foot sprite id `g` and
   elevation `e`. The receiver queues new steps by `seq` and replays them in order.
4. **Jumps.** More than one tile away (ledges, warps within a map, lag) → teleport: all three coord pairs
   `+= d`, sprite `x/y += 16*d` (sprite coords are linear in map coords: `SetSpritePosToMapCoords`). More than
   6 queued steps → teleport to the newest one.
5. **Removing.** Set `invisible`, move to (-1000, -1000). On the next camera move the game's
   `RemoveObjectEventsOutsideView` deletes the object and frees its sprite and tiles properly.
6. **Safety.** Nothing is written until the checks above pass (status in the panel and `mp.vis()`).
   Puppets are only driven in the overworld (no battle flags, `gMain.callback2` = the value seen while the
   player walks) and never while frozen (menus, text boxes). Maps that already use localIds 0x70-0x76 or have
   more than 56 objects are skipped. On script stop/reload the header is restored and puppets are hidden.

## Settings (`work/mp_config.txt` or per-window `mp_config_<n>.txt`)
- `visuals=0` turns it off (console: `mp.visuals(false)`, back on with `mp.visuals(true)`).
- `puppet_gfx=self` (default): draw others with your own on-foot sprite. Safe: player sprites share palette
  slot 0, so drawing a different player sprite (Red vs Leaf) can recolour your own character.
  `puppet_gfx=remote` uses the other player's sprite; `puppet_gfx=<number>` forces a graphics id
  (e.g. an NPC trainer sprite with its own palette).

## Limits (by design, from how the engine works)
- A puppet appears when **your** camera moves (you take a step), because that is when the game spawns NPCs.
- Puppets are solid: like any NPC you can't walk through another player. Talking to one does nothing.
- On-foot sprite only (no bike/surf sprite swap): changing a spawned NPC's graphics needs game code.
- Saving while a puppet is on screen stores it in the save like any NPC. Loading that save shows it at that
  spot until you leave the map (with the script running it is cleaned up immediately).
- Players on connected maps (walking across a route/town border without a warp) only show once you are on
  the same map.

## Real-emulator verification (for the local session)
1. Two windows on the same map, script loaded in both. Panel line `Visuals: on (1 on this map, ...)`; if it
   says `off:`/`paused:`, `mp.vis()` shows why and the addresses in use.
2. Take a step in each window: the other player appears where they stand, with your sprite.
3. Walk, run (B), turn in place, walk into a ledge, warp out and back: the puppet follows/animates/disappears.
4. Open the start menu and a text box while the other walks: puppet waits, then catches up.
5. Battle and return: puppet still there and moving. Script reload in one window: no duplicate puppet.
6. Try `puppet_gfx=remote` with a Red and a Leaf save and note whether colours clash.
7. Record results in `docs/progress.md`. If spawning never happens, check with `client/ramsearch.lua`
   whether RR moved `gMapHeader` or the SaveBlock1 template array (CFRU may change either).
