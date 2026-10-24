-- Phase 0 probe: verifies candidate RAM addresses on Radical Red and writes a report
-- Load in mGBA: Tools > Scripting > File > Load script. Report: work/probe_out.txt
local function workDir()
  if RRMP_WORK then return (RRMP_WORK:gsub("\\", "/"):gsub("/?$", "/")) end
  local src = debug.getinfo(1, "S").source:gsub("^@", ""):gsub("\\", "/")
  return (src:match("^(.*)/[^/]*$") or ".") .. "/../work/"
end
local OUT = workDir() .. "probe_out.txt"

-- Candidate vanilla FireRed 1.0 (BPRE) addresses. HYPOTHESES until verified below.
local CAND = {
  party       = 0x02024284,
  partyCount  = 0x02024029,
  enemyParty  = 0x0202402C,
  saveBlock1P = 0x03005008,
  saveBlock2P = 0x0300500C,
  mainStruct  = 0x030022C0,
  battleType  = 0x02022B4C,
  objEvents   = 0x02036E38,
  playerAvatar= 0x02037078,
  rng         = 0x03005000,
}

local ORDER = { "GAEM","GAME","GEAM","GEMA","GMAE","GMEA","AGEM","AGME","AEGM","AEMG","AMGE","AMEG",
                "EGAM","EGMA","EAGM","EAMG","EMGA","EMAG","MGAE","MGEA","MAGE","MAEG","MEGA","MEAG" }

local function decodeMon(addr)
  local raw = emu:readRange(addr, 100)
  if #raw < 100 then return nil end
  local pid, otid = string.unpack("<I4I4", raw, 1)
  if pid == 0 and otid == 0 then return nil end
  local storedSum = string.unpack("<I2", raw, 0x1C + 1)
  local level = raw:byte(0x54 + 1)
  local hp, maxhp = string.unpack("<I2I2", raw, 0x56 + 1)
  -- Observed on Radical Red: no encryption/shuffle, substructs fixed G,A,E,M at 0x20,0x2C,0x38,0x44, checksum field 0
  local species, item, exp = string.unpack("<I2I2I4", raw, 0x20 + 1)
  local m1, m2, m3, m4, pp1, pp2, pp3, pp4 = string.unpack("<I2I2I2I2BBBB", raw, 0x2C + 1)
  local plain = { ok = (storedSum == 0), mode = "PLAIN", species = species, item = item, exp = exp,
                  moves = { m1, m2, m3, m4 }, level = level, hp = hp, maxhp = maxhp, pid = pid, otid = otid }
  if plain.ok then return plain end
  -- Vanilla Gen3 fallback: XOR with pid^otid, substructure order by pid % 24, 16-bit sum checksum
  local key = pid ~ otid
  local data = {}
  for i = 0, 11 do
    local w = string.unpack("<I4", raw, 0x20 + i * 4 + 1) ~ key
    data[#data + 1] = string.pack("<I4", w)
  end
  data = table.concat(data)
  local sum = 0
  for i = 0, 23 do sum = (sum + string.unpack("<I2", data, i * 2 + 1)) & 0xFFFF end
  local order = ORDER[(pid % 24) + 1]
  local sub = {}
  for i = 1, 4 do sub[order:sub(i, i)] = data:sub((i - 1) * 12 + 1, i * 12) end
  local sp, it = string.unpack("<I2I2", sub.G)
  local a1, a2, a3, a4 = string.unpack("<I2I2I2I2", sub.A)
  return { ok = (sum == storedSum), mode = "ENCRYPTED", species = sp, item = it, moves = { a1, a2, a3, a4 },
           level = level, hp = hp, maxhp = maxhp, pid = pid, otid = otid }
end

local function monStr(m)
  if not m then return "(empty)" end
  return string.format("%s species=%d lvl=%d hp=%d/%d item=%d moves=%d,%d,%d,%d pid=%08X",
    (m.mode or "?") .. (m.ok and "_OK" or "_BAD"), m.species, m.level, m.hp, m.maxhp, m.item,
    m.moves[1], m.moves[2], m.moves[3], m.moves[4], m.pid)
end

-- Fallback: scan EWRAM for 100-byte structs whose checksum validates
local function scanParty()
  local found = {}
  for addr = 0x02000000, 0x0203FF9C, 4 do
    local m = decodeMon(addr)
    if m and m.ok and m.level >= 1 and m.level <= 100 and m.species > 0 and m.species < 1500
       and m.maxhp > 0 and m.hp <= m.maxhp then
      found[#found + 1] = string.format("  0x%08X %s", addr, monStr(m))
      if #found >= 24 then break end
    end
  end
  return found
end

local scanned = false
local function report()
  local L = {}
  local function add(s) L[#L + 1] = s end
  add("probe frame " .. emu:currentFrame() .. "  game=" .. emu:getGameTitle() .. " code=" .. emu:getGameCode())
  add("socket API: " .. type(socket) .. "  console: " .. type(console))
  local cnt = emu:read8(CAND.partyCount)
  add(string.format("partyCount @%08X = %d", CAND.partyCount, cnt))
  local anyOk = false
  for i = 0, 5 do
    local m = decodeMon(CAND.party + i * 100)
    if m and m.ok then anyOk = true end
    add(string.format("  party[%d] @%08X %s", i, CAND.party + i * 100, monStr(m)))
  end
  local sb1 = emu:read32(CAND.saveBlock1P)
  if cnt > 0 then
    add("RAW party[0]: " .. (emu:readRange(CAND.party, 100):gsub(".", function(c) return string.format("%02X", c:byte()) end)))
  end
  local function hex(a, n) return (emu:readRange(a, n):gsub(".", function(c) return string.format("%02X", c:byte()) end)) end
  for i = 0, 3 do add(string.format("RAW objEvent[%d] @%08X: %s", i, CAND.objEvents + i * 0x24, hex(CAND.objEvents + i * 0x24, 0x24))) end
  add("RAW playerAvatar @" .. string.format("%08X", CAND.playerAvatar) .. ": " .. hex(CAND.playerAvatar, 0x20))
  add("RAW gMain@030030F0: " .. hex(0x030030F0, 0x20))
  add("RAW gMain@030022C0: " .. hex(0x030022C0, 0x20))
  add("RAW rngCandidates 03005000: " .. hex(0x03005000, 0x10))
  add(string.format("saveBlock1Ptr @%08X = %08X", CAND.saveBlock1P, sb1))
  if sb1 >= 0x02000000 and sb1 < 0x02040000 then
    add(string.format("  pos x=%d y=%d mapGroup=%d mapNum=%d", emu:read16(sb1), emu:read16(sb1 + 2),
      emu:read8(sb1 + 4), emu:read8(sb1 + 5)))
  end
  add(string.format("gMain.callback1=%08X callback2=%08X", emu:read32(CAND.mainStruct), emu:read32(CAND.mainStruct + 4)))
  add(string.format("gBattleTypeFlags @%08X = %08X", CAND.battleType, emu:read32(CAND.battleType)))
  add(string.format("gRngValue @%08X = %08X", CAND.rng, emu:read32(CAND.rng)))
  for i = 0, 5 do
    local m = decodeMon(CAND.enemyParty + i * 100)
    if m and m.ok then add(string.format("  enemy[%d] %s", i, monStr(m))) end
  end
  if not anyOk and not scanned and cnt == 0 then
    add("(no valid party at candidate address yet - start a new game / load a save, scan will retry)")
  elseif not anyOk then
    scanned = true
    add("candidate party address FAILED; EWRAM scan results:")
    for _, s in ipairs(scanParty()) do add(s) end
  end
  local f = io.open(OUT, "w"); if f then f:write(table.concat(L, "\n"), "\n"); f:close() end
  return L
end

local n = 0
callbacks:add("frame", function()
  n = n + 1
  if n % 120 == 0 then report() end
end)
console:log("probe.lua loaded; writing report every 2s to " .. OUT)
report()
