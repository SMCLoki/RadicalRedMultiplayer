-- Radical Red Online: draw remote players as real overworld characters ("puppets").
-- Loaded by rr_mp.lua (dofile); defines _RRMP_VISUALS_NEW. Must not end with a return statement.
--
-- How it works (FireRed engine, struct layouts from pret/pokefirered, see docs/visuals.md):
-- 1. The game spawns NPCs itself from gSaveBlock1Ptr->objectEventTemplates[0 .. events->objectEventCount-1]
--    whenever the camera moves (TrySpawnObjectEvents). We append up to 7 templates after the map's own ones
--    (localId 0x70+k, script NULL, flag 0) and point gMapHeader.events at a RAM copy of the map's MapEvents
--    whose objectEventCount includes them. The copy lives in template slot 63, which maps never use.
-- 2. A spawned puppet is driven like `applymovement` does it: write movementActionId, set
--    heldMovementActive, clear heldMovementFinished, reset sprite data[2]. The game animates the walk.
-- 3. Big jumps are teleports: coords += d, sprite x/y += 16*d (sprite coords are linear in map coords).
-- 4. To remove a puppet we make it invisible and move it far away; the game deletes off-screen objects on
--    the next camera move (RemoveObjectEventsOutsideView), which also frees its sprite and tiles.
-- Every address is validated before anything is written; on failure visuals stay off and say why.

function _RRMP_VISUALS_NEW(env)
  local V = { enabled = env.enabled ~= false, ok = false, reason = "waiting for the overworld", shown = 0 }
  local log = env.event or function() end

  local A = {
    gSprites = 0x0202063C, gMapHeader = 0x02036DFC, gObjectEvents = 0x02036E38,
    sb1Ptr = 0x03005008, playerAvatar = 0x02037078, gMain = 0x030030F0, battleFlags = 0x02022B4C,
  }
  V.addr = A
  local OBJ, SPR, TPL, NOBJ = 0x24, 0x44, 0x18, 16
  local SB1_TEMPLATES, FAKE_SLOT, MAX_PUPPETS, LOCALID_BASE = 0x8E0, 63, 7, 0x70
  -- template slots 54-62 hold the event script that runs when you talk to a puppet (216 bytes, rr_mp builds it)
  local SCRIPT_SLOT, SCRIPT_MAX = 54, 9 * 0x18
  local function scriptAddr(sb1) return sb1 + SB1_TEMPLATES + SCRIPT_SLOT * TPL end
  V.scriptAddr = scriptAddr
  local MAP_OFFSET, FAR = 7, -1000
  local F_ACTIVE, F_HELD, F_HELD_DONE, F_FROZEN, F_INVISIBLE, F_PLAYER = 0x1, 0x40, 0x80, 0x100, 0x2000, 0x10000
  -- MOVEMENT_ACTION_* from pokefirered include/constants/event_object_movement.h; index = facing (1 down 2 up 3 left 4 right)
  local ACT_FACE = { 0x00, 0x01, 0x02, 0x03 }
  local ACT_WALK = { 0x10, 0x11, 0x12, 0x13 }
  local ACT_FAST = { 0x1D, 0x1E, 0x1F, 0x20 }
  local RUNNING = {}
  for a = 0x1D, 0x20 do RUNNING[a] = true end
  for a = 0x3D, 0x40 do RUNNING[a] = true end

  local function s16(v) if v >= 0x8000 then return v - 0x10000 end return v end
  local function isRom(p) return p >= 0x08000000 and p < 0x0A000000 end
  local function isEwram(p) return p >= 0x02000000 and p < 0x02040000 end

  ------------------------------------------------------------------------------------------------
  -- validation

  local function playerObjOk()
    local o = A.gObjectEvents
    local flags = emu:read32(o)
    return (flags & F_ACTIVE) ~= 0 and (flags & F_PLAYER) ~= 0 and emu:read8(o + 8) == 0xFF
  end

  local function spritesOk(base)
    -- every active object event's sprite must be in use and point back at it (sprite->data[0] = objectEventId)
    local checked = 0
    for i = 0, NOBJ - 1 do
      local o = A.gObjectEvents + i * OBJ
      if emu:read32(o) & F_ACTIVE ~= 0 then
        local id = emu:read8(o + 4)
        if id >= 64 then return false end
        local s = base + id * SPR
        if emu:read16(s + 0x3E) & 1 == 0 or not isRom(emu:read32(s + 0x1C)) or emu:read16(s + 0x2E) ~= i then
          return false
        end
        checked = checked + 1
      end
    end
    return checked > 0
  end

  local function headerOk(hdr, sb1)
    local ev = emu:read32(hdr + 4)
    if V.fake and ev == V.fake then return true end
    if not isRom(emu:read32(hdr)) or not isRom(ev) then return false end
    local count = emu:read8(ev)
    if count > 64 then return false end
    local tp = emu:read32(ev + 4)
    if count > 0 and not isRom(tp) then return false end
    for i = 0, count - 1 do
      if emu:read8(tp + i * TPL) ~= emu:read8(sb1 + SB1_TEMPLATES + i * TPL) then return false end
    end
    return true
  end

  local function scanSprites()
    for base = 0x02000000, 0x0203C000, 4 do
      if spritesOk(base) then return base end
    end
  end

  local function validate(sb1)
    if not playerObjOk() then return false, "player object not found (gObjectEvents)" end
    if not spritesOk(A.gSprites) then
      local found = scanSprites()
      if not found then return false, "gSprites not found" end
      log(string.format("visuals: gSprites found at %08X (vanilla %08X)", found, A.gSprites))
      A.gSprites = found
    end
    if not headerOk(A.gMapHeader, sb1) then
      return false, string.format("gMapHeader %08X does not match the map templates", A.gMapHeader)
    end
    return true
  end

  ------------------------------------------------------------------------------------------------
  -- map patch: extra templates + MapEvents copy with a bigger objectEventCount

  local tplCache = {}

  local function writeTemplate(sb1, idx, k, p)
    local x, y, g, e = FAR, FAR, 0, 0
    if p then x, y, g, e = p.tx, p.ty, p.gfx, p.elev end
    local key = string.format("%d:%d,%d,%d,%d", sb1, x, y, g, e)
    local a = sb1 + SB1_TEMPLATES + idx * TPL
    local scr = (p and env.buildScript) and scriptAddr(sb1) or 0
    -- the cache alone can be stale after a soft reset reloads the save into the same addresses: check RAM too
    if tplCache[idx] == key and emu:read32(a + 0x10) == scr and emu:read16(a + 4) == (x & 0xFFFF) then return end
    tplCache[idx] = key
    emu:write32(a, (LOCALID_BASE + k) | (g << 8))         -- localId, graphicsId, kind 0 (normal), pad
    emu:write16(a + 4, x & 0xFFFF)
    emu:write16(a + 6, y & 0xFFFF)
    emu:write32(a + 8, e & 0x0F)                           -- elevation, movementType NONE, no range
    emu:write32(a + 0x0C, 0)                               -- trainerType, trainerRange
    -- script: the shared talk-to-player script (or NULL = talking does nothing)
    emu:write32(a + 0x10, scr)
    emu:write32(a + 0x14, 0)                               -- flagId 0: never hidden
  end

  -- returns true when the current map is patched (or can be)
  local function patch(sb1)
    local hdr = A.gMapHeader
    local fake = sb1 + SB1_TEMPLATES + FAKE_SLOT * TPL
    local ev = emu:read32(hdr + 4)
    if ev == fake then return true end
    if isEwram(ev) then
      -- our copy from before the save block moved
      if not V.origEvents then return false end
      ev = V.origEvents
    elseif not isRom(ev) then
      return false
    end
    local count = emu:read8(ev)
    if count + MAX_PUPPETS > SCRIPT_SLOT then
      V.mapReason = "this map has too many objects for puppets"
      return false
    end
    local tp = emu:read32(ev + 4)
    for i = 0, count - 1 do
      local id = emu:read8(tp + i * TPL)
      if id >= LOCALID_BASE and id < LOCALID_BASE + MAX_PUPPETS then
        V.mapReason = "this map already uses local ids 0x70-0x76"
        return false
      end
    end
    V.origEvents, V.baseCount, V.fake, V.mapReason = ev, count, fake, nil
    tplCache = {}
    for k = 0, MAX_PUPPETS - 1 do writeTemplate(sb1, count + k, k, nil) end
    local raw = emu:readRange(ev, 0x14)
    for i = 0, 0x10, 4 do emu:write32(fake + i, (string.unpack("<I4", raw, i + 1))) end
    emu:write8(fake, count + MAX_PUPPETS)
    emu:write32(hdr + 4, fake)
    return true
  end

  local function unpatch()
    if V.fake and V.origEvents and emu:read32(A.gMapHeader + 4) == V.fake then
      emu:write32(A.gMapHeader + 4, V.origEvents)
    end
  end

  ------------------------------------------------------------------------------------------------
  -- puppets

  V.peers = {}      -- peer id -> { queue, lastSeq, target, map, gfx, elev }
  V.slotPeer = {}   -- k -> peer id
  local peerSlot = {}

  function V.onState(id, st)
    local p = V.peers[id]
    if not p then
      p = { queue = {}, lastSeq = -1 }
      V.peers[id] = p
    end
    local mapKey = st.map and (st.map[1] .. "." .. st.map[2]) or nil
    if mapKey ~= p.map then p.queue = {} end
    p.map, p.gfx, p.elev = mapKey, st.g, st.e
    if st.x then p.target = { x = st.x, y = st.y, f = st.f } end
    if type(st.st) == "table" then
      for _, s in ipairs(st.st) do
        if type(s) == "table" and s[1] and s[1] > p.lastSeq then
          p.queue[#p.queue + 1] = { x = s[2], y = s[3], f = s[4], a = s[5] }
          p.lastSeq = s[1]
        end
      end
      while #p.queue > 16 do table.remove(p.queue, 1) end
    end
  end

  function V.onLeave(id) V.peers[id] = nil end

  local function objAddr(i) return A.gObjectEvents + i * OBJ end
  local function spriteAddr(o) return A.gSprites + emu:read8(o + 4) * SPR end

  local function coords(o) return s16(emu:read16(o + 0x10)) - MAP_OFFSET, s16(emu:read16(o + 0x12)) - MAP_OFFSET end

  local function teleport(o, nx, ny)
    local cx, cy = coords(o)
    local dx, dy = nx - cx, ny - cy
    if dx == 0 and dy == 0 then return end
    for off = 0x0C, 0x14, 4 do
      emu:write16(o + off, (s16(emu:read16(o + off)) + dx) & 0xFFFF)
      emu:write16(o + off + 2, (s16(emu:read16(o + off + 2)) + dy) & 0xFFFF)
    end
    local s = spriteAddr(o)
    emu:write16(s + 0x20, (s16(emu:read16(s + 0x20)) + 16 * dx) & 0xFFFF)
    emu:write16(s + 0x22, (s16(emu:read16(s + 0x22)) + 16 * dy) & 0xFFFF)
  end

  local function issue(o, action)
    emu:write8(o + 0x1C, action)
    local flags = emu:read32(o)
    emu:write32(o, (flags | F_HELD) & ~(F_HELD_DONE | F_FROZEN))
    emu:write16(spriteAddr(o) + 0x32, 0)   -- sprite->data[2]: restart the action's step function
  end

  local function hide(o)
    local flags = emu:read32(o)
    if flags & F_HELD ~= 0 and flags & F_HELD_DONE == 0 then return end  -- let a running step finish first
    emu:write32(o, flags | F_INVISIBLE)
    local cx, cy = coords(o)
    if cx > FAR / 2 then teleport(o, FAR, FAR) end
  end

  local function dirTo(dx, dy)
    if dy == 1 then return 1 elseif dy == -1 then return 2 elseif dx == -1 then return 3 else return 4 end
  end

  local function drive(o, p)
    local flags = emu:read32(o)
    if flags & F_FROZEN ~= 0 then return end                                  -- menu/textbox open
    if flags & F_HELD ~= 0 and flags & F_HELD_DONE == 0 then return end       -- step in progress
    if flags & F_INVISIBLE ~= 0 then emu:write32(o, flags & ~F_INVISIBLE) end
    local cx, cy = coords(o)
    local face = emu:read8(o + 0x18) & 0x0F
    local q = p.queue
    if p.elev then emu:write8(o + 0x0B, (emu:read8(o + 0x0B) & 0xF0) | (p.elev & 0x0F)) end
    while q[1] and q[1].x == cx and q[1].y == cy and (q[1].f == face or not q[1].f) do table.remove(q, 1) end
    if #q > 6 then  -- far behind: jump to the latest position
      local last = q[#q]
      p.queue, q = {}, {}
      teleport(o, last.x, last.y)
      if last.f and last.f ~= face then issue(o, ACT_FACE[last.f]) end
      return
    end
    local step = q[1] or p.target
    if not step then return end
    local dx, dy = step.x - cx, step.y - cy
    local d = math.abs(dx) + math.abs(dy)
    if d == 0 then
      if step.f and step.f >= 1 and step.f <= 4 and step.f ~= face then issue(o, ACT_FACE[step.f]) end
      if q[1] == step then table.remove(q, 1) end
    elseif d == 1 then
      local dir = dirTo(dx, dy)
      local fast = (step.a and RUNNING[step.a]) or #q >= 3
      issue(o, (fast and ACT_FAST or ACT_WALK)[dir])
    else
      teleport(o, step.x, step.y)
      if step.f and step.f >= 1 and step.f <= 4 then issue(o, ACT_FACE[step.f]) end
    end
  end

  ------------------------------------------------------------------------------------------------
  -- per-frame entry point

  -- CB2_Overworld, verified on RR (docs/ram_map.md "gMain callbacks"); config cb_overworld overrides
  local overworldCb2, lastValidate = tonumber(env.cbOverworld or "") or 0x080565B5, -9999

  function V.inOverworld()
    if emu:read32(A.battleFlags) ~= 0 then return false end
    local o = A.gObjectEvents
    if emu:read32(o) & (F_ACTIVE | F_PLAYER) ~= (F_ACTIVE | F_PLAYER) then return false end
    return emu:read32(A.gMain + 4) == overworldCb2
  end

  local function myLocation(sb1)
    return emu:read8(sb1 + 4), emu:read8(sb1 + 5)
  end

  local function puppetGfx(p)
    local mode = env.gfx or "self"
    if tonumber(mode) then return tonumber(mode) end
    if mode == "remote" and p.gfx then return p.gfx end
    return V.selfGfx or 0
  end

  function V.tick(frame)
    if not V.enabled then V.reason = "off (mp.visuals(true) to enable)"; return end
    local sb1 = emu:read32(A.sb1Ptr)
    if not isEwram(sb1) or not V.inOverworld() then
      -- Battle init and field init move the save blocks (MoveSaveBlocks_ResetHeap), which would leave
      -- gMapHeader.events pointing at stale memory: restore the ROM pointer as soon as we leave the overworld.
      unpatch()
      return
    end
    if emu:read8(A.playerAvatar) & 1 ~= 0 then V.selfGfx = emu:read8(A.gObjectEvents + 5) end  -- on foot
    if not V.ok then
      if frame - lastValidate < 300 then return end
      lastValidate = frame
      local ok, why = validate(sb1)
      V.ok, V.reason = ok, ok and "on" or ("off: " .. why)
      if not ok then return end
      log("visuals: addresses validated, remote players will appear as characters")
    end
    local group, num = myLocation(sb1)
    local here = group .. "." .. num

    -- assign puppet slots to peers on this map
    for id, p in pairs(V.peers) do
      local k = peerSlot[id]
      if p.map == here and p.target then
        if not k then
          for s = 0, MAX_PUPPETS - 1 do
            if not V.slotPeer[s] then k = s; break end
          end
          if k then V.slotPeer[k], peerSlot[id] = id, k end
        end
      elseif k then
        V.slotPeer[k], peerSlot[id] = nil, nil
      end
    end
    for k, id in pairs(V.slotPeer) do
      if not V.peers[id] then V.slotPeer[k], peerSlot[id] = nil, nil end
    end

    local patched = patch(sb1)
    if patched then
      for k = 0, MAX_PUPPETS - 1 do
        local p = V.slotPeer[k] and V.peers[V.slotPeer[k]]
        if p then
          local last = p.queue[#p.queue] or p.target
          p.tx, p.ty, p.gfx2 = last.x, last.y, puppetGfx(p)
          writeTemplate(sb1, V.baseCount + k, k, { tx = p.tx, ty = p.ty, gfx = p.gfx2, elev = p.elev or 3 })
        else
          writeTemplate(sb1, V.baseCount + k, k, nil)
        end
      end
    end

    -- find spawned puppets and drive or hide them
    local shown, at = 0, {}
    for i = 1, NOBJ - 1 do
      local o = objAddr(i)
      if emu:read32(o) & F_ACTIVE ~= 0 then
        local k = emu:read8(o + 8) - LOCALID_BASE
        if k >= 0 and k < MAX_PUPPETS then
          local onMap = emu:read8(o + 9) == num and emu:read8(o + 10) == group
          local p = patched and onMap and V.slotPeer[k] and V.peers[V.slotPeer[k]]
          if p and emu:read8(o + 5) == p.gfx2 then
            drive(o, p)
            shown = shown + 1
            at[(s16(emu:read16(o + 0x10))) .. "," .. (s16(emu:read16(o + 0x12)))] = V.slotPeer[k]
          else
            hide(o)   -- leftover, wrong map, or graphics changed: the game deletes it on the next camera move
          end
        end
      end
    end
    V.shown = shown
    V.reason = patched and "on" or ("paused: " .. (V.mapReason or "map not patched"))

    -- talk-to-player: keep the shared script prepared for the puppet the player is facing. Never rewrite it
    -- while a script runs (the player object is frozen by `lock` from the moment A is pressed).
    if patched and env.buildScript then
      local po = A.gObjectEvents
      if emu:read32(po) & F_FROZEN == 0 then
        local dx, dy = 0, 0
        local f = emu:read8(po + 0x18) & 0x0F
        if f == 1 then dy = 1 elseif f == 2 then dy = -1 elseif f == 3 then dx = -1 elseif f == 4 then dx = 1 end
        local tx, ty = s16(emu:read16(po + 0x10)) + dx, s16(emu:read16(po + 0x12)) + dy
        local peer = at[tx .. "," .. ty]
        if peer then
          local addr = scriptAddr(sb1)
          local bytes = env.buildScript(peer, addr)
          -- compare with RAM, not only the last write: a soft reset + loading the save overwrites this area with
          -- the saved bytes at the same address, and the cached copy would then never be rewritten (real mGBA
          -- run 12: talking to a player did nothing after emu:reset()).
          if bytes and #bytes <= SCRIPT_MAX and (bytes ~= V.scriptBytes or addr ~= V.scriptAt
              or emu:readRange(addr, #bytes) ~= bytes) then
            for i = 1, #bytes do emu:write8(addr + i - 1, bytes:byte(i)) end
            V.scriptBytes, V.scriptAt, V.scriptPeer = bytes, addr, peer
          end
        end
      end
    end
  end

  -- which peer a puppet localId (gSpecialVar_LastTalked) belongs to
  function V.peerForLocalId(localId)
    return V.slotPeer[(localId or 0) - LOCALID_BASE]
  end

  function V.status()
    local n = 0
    for _ in pairs(V.slotPeer) do n = n + 1 end
    return string.format("Visuals: %s (%d on this map, %d drawn)", V.reason, n, V.shown)
  end

  function V.shutdown()
    pcall(function()
      unpatch()
      for i = 1, NOBJ - 1 do
        local o = objAddr(i)
        local k = emu:read8(o + 8) - LOCALID_BASE
        if emu:read32(o) & F_ACTIVE ~= 0 and k >= 0 and k < MAX_PUPPETS then hide(o) end
      end
    end)
  end

  function V.setEnabled(on)
    V.enabled = on
    if not on then V.shutdown(); V.slotPeer, peerSlot = {}, {} end
  end

  return V
end
