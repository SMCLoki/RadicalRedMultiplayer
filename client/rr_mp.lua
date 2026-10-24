-- Radical Red Online - mGBA 0.10+ client (rooms of up to 8 players)
--
-- Load:  mGBA > Tools > Scripting > File > Load script > client/rr_mp.lua
-- Then type commands into the scripting console, e.g.  mp.help()
--
-- Settings live in work/mp_config.txt (created on first run). Each mGBA instance on the same PC
-- claims its own "profile" (1-8) so two windows don't share an identity, trade journal or status file.
-- Protocol: docs/protocol.md. RAM addresses: docs/ram_map.md (verified on Radical Red).

local VERSION = "0.3.0"
local PROTO = 1
local FPS = 60

if _RRMP_INSTANCE and _RRMP_INSTANCE.shutdown then pcall(_RRMP_INSTANCE.shutdown) end
local M = {}
_RRMP_INSTANCE = M

------------------------------------------------------------------------------------------------
-- Paths, files, config

local function scriptDir()
  local src = (debug and debug.getinfo and debug.getinfo(1, "S").source) or ""
  src = src:gsub("^@", ""):gsub("\\", "/")
  return src:match("^(.*)/[^/]*$") or "."
end

local function readFile(path)
  local f = io.open(path, "rb")
  if not f then return nil end
  local s = f:read("a")
  f:close()
  return s
end

local function writeFile(path, s, mode)
  local f = io.open(path, mode or "wb")
  if not f then return false end
  f:write(s)
  f:close()
  return true
end

local WORK = RRMP_WORK or (scriptDir() .. "/../work/")
WORK = WORK:gsub("\\", "/")
if not WORK:match("/$") then WORK = WORK .. "/" end
if not writeFile(WORK .. "mp_probe.tmp", "ok") then
  -- work/ is missing (fresh checkout): fall back to the script folder so we can still run.
  WORK = scriptDir() .. "/"
else
  os.remove(WORK .. "mp_probe.tmp")
end

local function parseConfig(text, into)
  for line in (text or ""):gmatch("[^\r\n]+") do
    local k, v = line:match("^%s*([%w_]+)%s*=%s*(.-)%s*$")
    if k and not line:match("^%s*#") then into[k] = v end
  end
  return into
end

local CONFIG_HELP = [[# Radical Red Online settings. Edit, then type mp.reload() in the scripting console.
# host: server address (127.0.0.1 = server on this PC). port: server port.
# room: room code, 1-16 letters/digits/_/- . Everyone in the same room sees each other (max 8).
# name: your display name (max 12). Per-window overrides go in mp_config_<profile>.txt
]]

local function pickProfile()
  if RRMP_PROFILE then return tonumber(RRMP_PROFILE) end
  local now = os.time()
  for n = 1, 8 do
    local t = tonumber(readFile(WORK .. "mp_lock_" .. n .. ".txt") or "")
    if not t or now - t > 15 then return n end
  end
  return 1
end

local PROFILE = pickProfile()
local function touchLock() writeFile(WORK .. "mp_lock_" .. PROFILE .. ".txt", tostring(os.time())) end
touchLock()

local cfg
local function loadConfig()
  cfg = { host = "127.0.0.1", port = "8765", room = "LOBBY", name = "Player" .. PROFILE, autoconnect = "1" }
  local shared = readFile(WORK .. "mp_config.txt")
  if not shared then
    writeFile(WORK .. "mp_config.txt", CONFIG_HELP .. "host=127.0.0.1\nport=8765\nroom=LOBBY\n")
  end
  parseConfig(shared, cfg)
  parseConfig(readFile(WORK .. "mp_config_" .. PROFILE .. ".txt"), cfg)
  if RRMP_CONFIG then for k, v in pairs(RRMP_CONFIG) do cfg[k] = tostring(v) end end
end

local function saveProfileSetting(key, value)
  local path = WORK .. "mp_config_" .. PROFILE .. ".txt"
  local t = parseConfig(readFile(path), {})
  t[key] = value
  local out = { "# per-window overrides for profile " .. PROFILE }
  for k, v in pairs(t) do out[#out + 1] = k .. "=" .. v end
  writeFile(path, table.concat(out, "\n") .. "\n")
end

loadConfig()

------------------------------------------------------------------------------------------------
-- Minimal JSON (objects, arrays, strings, numbers, booleans, null)

local json = { null = setmetatable({}, { __tostring = function() return "null" end }) }
local ARRAY_MT = { __jsontype = "array" }
function json.array(t) return setmetatable(t or {}, ARRAY_MT) end

local ESC = { ['"'] = '\\"', ["\\"] = "\\\\", ["\b"] = "\\b", ["\f"] = "\\f", ["\n"] = "\\n",
              ["\r"] = "\\r", ["\t"] = "\\t" }

local function encode(v, out)
  local tv = type(v)
  if v == nil or v == json.null then out[#out + 1] = "null"
  elseif tv == "boolean" then out[#out + 1] = v and "true" or "false"
  elseif tv == "number" then
    if v ~= v or v == math.huge or v == -math.huge then out[#out + 1] = "null"
    elseif math.type(v) == "integer" then out[#out + 1] = string.format("%d", v)
    else out[#out + 1] = string.format("%.14g", v) end
  elseif tv == "string" then
    out[#out + 1] = '"' .. v:gsub('[%c"\\]', function(c)
      return ESC[c] or string.format("\\u%04x", c:byte())
    end) .. '"'
  elseif tv == "table" then
    local n, count = #v, 0
    for _ in pairs(v) do count = count + 1 end
    local isArray = getmetatable(v) == ARRAY_MT or (n > 0 and count == n)
    if isArray then
      out[#out + 1] = "["
      for i = 1, n do
        if i > 1 then out[#out + 1] = "," end
        encode(v[i], out)
      end
      out[#out + 1] = "]"
    else
      out[#out + 1] = "{"
      local first = true
      for k, val in pairs(v) do
        if not first then out[#out + 1] = "," end
        first = false
        encode(tostring(k), out)
        out[#out + 1] = ":"
        encode(val, out)
      end
      out[#out + 1] = "}"
    end
  else
    error("cannot encode " .. tv)
  end
end

function json.encode(v)
  local out = {}
  encode(v, out)
  return table.concat(out)
end

local function utf8char(cp)
  if utf8 and utf8.char then return utf8.char(cp) end
  return cp < 128 and string.char(cp) or "?"
end

function json.decode(s)
  local pos = 1
  local function ws() pos = s:find("[^ \t\r\n]", pos) or #s + 1 end
  local function err(m) error(string.format("json: %s at %d", m, pos), 0) end
  local value
  local function str()
    pos = pos + 1
    local buf = {}
    while true do
      local c = s:sub(pos, pos)
      if c == "" then err("unterminated string") end
      if c == '"' then pos = pos + 1; break end
      if c == "\\" then
        local e = s:sub(pos + 1, pos + 1)
        local map = { b = "\b", f = "\f", n = "\n", r = "\r", t = "\t", ['"'] = '"', ["\\"] = "\\", ["/"] = "/" }
        if map[e] then buf[#buf + 1] = map[e]; pos = pos + 2
        elseif e == "u" then
          local cp = tonumber(s:sub(pos + 2, pos + 5), 16) or err("bad \\u escape")
          pos = pos + 6
          if cp >= 0xD800 and cp <= 0xDBFF and s:sub(pos, pos + 1) == "\\u" then
            local lo = tonumber(s:sub(pos + 2, pos + 5), 16)
            if lo and lo >= 0xDC00 and lo <= 0xDFFF then
              cp = 0x10000 + (cp - 0xD800) * 0x400 + (lo - 0xDC00)
              pos = pos + 6
            end
          end
          buf[#buf + 1] = utf8char(cp)
        else err("bad escape") end
      else
        local j = s:find('["\\]', pos) or #s + 1
        buf[#buf + 1] = s:sub(pos, j - 1)
        pos = j
      end
    end
    return table.concat(buf)
  end
  function value()
    ws()
    local c = s:sub(pos, pos)
    if c == "{" then
      pos = pos + 1
      local t = {}
      ws()
      if s:sub(pos, pos) == "}" then pos = pos + 1; return t end
      while true do
        ws()
        if s:sub(pos, pos) ~= '"' then err("expected key") end
        local k = str()
        ws()
        if s:sub(pos, pos) ~= ":" then err("expected ':'") end
        pos = pos + 1
        t[k] = value()
        ws()
        local d = s:sub(pos, pos)
        pos = pos + 1
        if d == "}" then return t end
        if d ~= "," then err("expected ',' or '}'") end
      end
    elseif c == "[" then
      pos = pos + 1
      local t = json.array({})
      ws()
      if s:sub(pos, pos) == "]" then pos = pos + 1; return t end
      while true do
        t[#t + 1] = value()
        ws()
        local d = s:sub(pos, pos)
        pos = pos + 1
        if d == "]" then return t end
        if d ~= "," then err("expected ',' or ']'") end
      end
    elseif c == '"' then return str()
    elseif s:sub(pos, pos + 3) == "true" then pos = pos + 4; return true
    elseif s:sub(pos, pos + 4) == "false" then pos = pos + 5; return false
    elseif s:sub(pos, pos + 3) == "null" then pos = pos + 4; return json.null
    else
      local num = s:match("^-?%d+%.?%d*", pos)
      if not num then err("unexpected character") end
      pos = pos + #num
      local exp = s:match("^[eE][-+]?%d+", pos)
      if exp then num = num .. exp; pos = pos + #exp end
      local n = tonumber(num) or err("bad number")
      return math.tointeger(n) or n
    end
  end
  local ok, res = pcall(function()
    local v = value()
    ws()
    if pos <= #s then err("trailing data") end
    return v
  end)
  if ok then return res end
  return nil, res
end

M.json = json

------------------------------------------------------------------------------------------------
-- RAM access (addresses verified on Radical Red, docs/ram_map.md)

local RAM = {
  party = 0x02024284,
  partyCount = 0x02024029,
  enemyParty = 0x0202402C,
  battleFlags = 0x02022B4C,
  sb1Ptr = 0x03005008,
  objEvents = 0x02036E38,
}
local MON_SIZE = 100
M.RAM = RAM

local function tohex(s)
  return (s:gsub(".", function(c) return string.format("%02x", c:byte()) end))
end

local function fromhex(h)
  return (h:gsub("..", function(x) return string.char(tonumber(x, 16)) end))
end

local function readMonHex(base, slot)
  return tohex(emu:readRange(base + slot * MON_SIZE, MON_SIZE))
end

local function writeMonHex(base, slot, hex)
  local raw = fromhex(hex)
  local addr = base + slot * MON_SIZE
  for i = 0, MON_SIZE - 4, 4 do
    emu:write32(addr + i, (string.unpack("<I4", raw, i + 1)))
  end
end

-- Decode the fields we care about from a 100-byte plain (unencrypted) party struct, or nil if empty/bad.
local function monInfo(hex)
  if type(hex) ~= "string" or #hex ~= 200 or hex:find("[^%x]") then return nil end
  local raw = fromhex(hex)
  local pid, otid = string.unpack("<I4I4", raw, 1)
  if pid == 0 and otid == 0 then return nil end
  local checksum = string.unpack("<I2", raw, 0x1C + 1)
  local species = string.unpack("<I2", raw, 0x20 + 1)
  local m1, m2, m3, m4 = string.unpack("<I2I2I2I2", raw, 0x2C + 1)
  local level = raw:byte(0x54 + 1)
  local hp, maxhp = string.unpack("<I2I2", raw, 0x56 + 1)
  local ok = checksum == 0 and species >= 1 and species <= 2047 and level >= 1 and level <= 100
    and maxhp > 0 and hp <= maxhp and (m1 + m2 + m3 + m4) > 0
  return { fp = string.format("%08x%08x", pid, otid), pid = pid, otid = otid, species = species,
           level = level, hp = hp, maxhp = maxhp, moves = { m1, m2, m3, m4 }, ok = ok }
end
M.monInfo = monInfo

local function partyCount()
  local n = emu:read8(RAM.partyCount)
  if n > 6 then n = 6 end
  return n
end

local function partySlots()
  local slots = {}
  for i = 0, partyCount() - 1 do
    local hex = readMonHex(RAM.party, i)
    slots[i] = { hex = hex, info = monInfo(hex) }
  end
  return slots
end

local function findFp(fp)
  for i, s in pairs(partySlots()) do
    if s.info and s.info.fp == fp then return i end
  end
  return nil
end

local function inBattle() return emu:read32(RAM.battleFlags) ~= 0 end

local function monLabel(info)
  if not info then return "(empty)" end
  return string.format("#%d Lv%d", info.species, info.level)
end

local function sampleState()
  local sb1 = emu:read32(RAM.sb1Ptr)
  if sb1 < 0x02000000 or sb1 >= 0x02040000 then return nil end
  local x, y = emu:read16(sb1), emu:read16(sb1 + 2)
  if x >= 0x8000 then x = x - 0x10000 end
  if y >= 0x8000 then y = y - 0x10000 end
  -- facing: low nibble of objectEvents[0]+0x18 (vanilla layout; NOT yet verified on RR, see ram_map.md)
  local facing = emu:read8(RAM.objEvents + 0x18) & 0x0F
  return { map = json.array({ emu:read8(sb1 + 4), emu:read8(sb1 + 5) }), x = x, y = y, f = facing,
           b = inBattle() and 1 or 0 }
end

local function gameId()
  local code = tostring(emu:getGameCode() or "GBA"):gsub("[^%w%-]", "")
  local crc
  if emu.checksum and C and C.CHECKSUM and C.CHECKSUM.CRC32 then
    local ok, raw = pcall(emu.checksum, emu, C.CHECKSUM.CRC32)
    if ok and type(raw) == "string" and #raw > 0 then crc = tohex(raw) end
  end
  if not crc then
    -- FNV-1a over the ROM header + title as a fallback identifier
    local h = 0x811C9DC5
    local data = emu:readRange(0x08000000, 0xC0) .. tostring(emu:getGameTitle())
    for i = 1, #data do h = ((h ~ data:byte(i)) * 0x01000193) & 0xFFFFFFFF end
    crc = string.format("h%08x", h)
  end
  return (code ~= "" and code or "GBA") .. ":" .. crc
end

------------------------------------------------------------------------------------------------
-- State

local S = {
  frame = 0,
  status = "offline", -- offline | connecting | online | stopped
  statusMsg = "",
  myId = nil,
  peers = {},       -- id -> {name=, state=}
  chat = {},        -- recent lines
  log = {},         -- recent events
  trade = nil,
  battle = nil,
  pendingCommits = {},
  dirty = true,
}
M.S = S

local function event(msg)
  S.log[#S.log + 1] = msg
  if #S.log > 6 then table.remove(S.log, 1) end
  S.dirty = true
  if console and console.log then console:log("[mp] " .. msg) end
end

local function uid()
  local path = WORK .. "mp_uid_" .. PROFILE .. ".txt"
  local u = (readFile(path) or ""):match("^%w+")
  if not u or #u < 8 then
    u = string.format("%08x%08x", math.random(0, 0x7FFFFFFF), math.random(0, 0x7FFFFFFF))
    writeFile(path, u)
  end
  return u
end

------------------------------------------------------------------------------------------------
-- Networking (mGBA socket API: raw TCP; connect blocks, everything else is event driven)

local net = { sock = nil, inbuf = "", outbuf = "", lastRx = 0, nextPing = 0, backoff = 1,
              retryAt = 0, pingN = 0, pingAt = {}, rtt = nil, auto = cfg.autoconnect ~= "0" }
M.net = net
local handlers = {}
local NO_RETRY = { bad_version = true, game_mismatch = true, room_full = true, replaced = true, bad_hello = true }

local function send(msg)
  if not net.sock then return false end
  local ok, line = pcall(json.encode, msg)
  if not ok then event("encode failed: " .. tostring(line)); return false end
  net.outbuf = net.outbuf .. line .. "\n"
  return true
end
M.send = send

local function drop(reason, retry)
  if net.sock then pcall(function() net.sock:close() end) end
  net.sock, net.inbuf, net.outbuf = nil, "", ""
  S.myId, S.peers = nil, {}
  if S.trade then S.trade = nil end
  if retry == false or not net.auto then
    S.status = "stopped"
  else
    S.status = "offline"
    net.retryAt = S.frame + net.backoff * FPS
    net.backoff = math.min(net.backoff * 2, 10)
  end
  S.statusMsg = reason or ""
  S.dirty = true
  event("disconnected: " .. tostring(reason))
end

local function flush()
  if not net.sock or net.outbuf == "" then return end
  local ok, sent, err = pcall(net.sock.send, net.sock, net.outbuf)
  if not ok then drop("send failed: " .. tostring(sent)); return end
  if sent == nil then
    if err ~= "again" then drop("send: " .. tostring(err)) end
    return
  end
  if type(sent) == "number" and sent < #net.outbuf then
    net.outbuf = net.outbuf:sub(sent + 1)
  else
    net.outbuf = ""
  end
end

local function processLine(line)
  local msg, err = json.decode(line)
  if type(msg) ~= "table" or type(msg.t) ~= "string" then
    event("bad message from server: " .. tostring(err))
    return
  end
  local h = handlers[msg.t]
  if h then
    local ok, e = pcall(h, msg)
    if not ok then event("handler " .. msg.t .. " failed: " .. tostring(e)) end
  end
end

local function onReceived()
  local sock = net.sock
  if not sock then return end
  local ok, data, err = pcall(sock.receive, sock, 4096)
  if sock ~= net.sock then return end
  if not ok then drop("receive failed: " .. tostring(data)); return end
  if not data or data == "" then
    if err ~= "again" then drop(err or "disconnected") end
    return
  end
  net.lastRx = S.frame
  net.inbuf = net.inbuf .. data
  if #net.inbuf > 65536 then drop("server line too long"); return end
  while true do
    local i = net.inbuf:find("\n", 1, true)
    if not i then break end
    local line = net.inbuf:sub(1, i - 1)
    net.inbuf = net.inbuf:sub(i + 1)
    if line ~= "" then processLine(line) end
    if not net.sock then return end
  end
end

local function connect()
  if net.sock then return end
  S.status, S.statusMsg = "connecting", cfg.host .. ":" .. cfg.port
  S.dirty = true
  local ok, sock, err = pcall(socket.connect, cfg.host, tonumber(cfg.port) or 8765)
  if not ok or not sock then
    drop("connect failed: " .. tostring(ok and err or sock))
    return
  end
  net.sock, net.inbuf, net.outbuf = sock, "", ""
  net.lastRx, net.nextPing = S.frame, S.frame + 2 * FPS
  sock:add("received", onReceived)
  sock:add("error", function(e) if net.sock == sock then drop("socket error " .. tostring(e)) end end)
  send({ t = "hello", v = PROTO, name = cfg.name, room = cfg.room, game = gameId(), uid = uid(),
         client = "rr_mp.lua " .. VERSION })
  flush()
end

------------------------------------------------------------------------------------------------
-- Trading (server does the two-phase commit; we journal before touching RAM so a crash or a
-- save reload can be reconciled: see docs/protocol.md "Trade safety")

local JOURNAL = WORK .. "mp_trades_" .. PROFILE .. ".jsonl"

local function journalEntries()
  local list = {}
  for line in (readFile(JOURNAL) or ""):gmatch("[^\r\n]+") do
    local e = json.decode(line)
    if type(e) == "table" and e.txn and e.give and e.mon then list[#list + 1] = e end
  end
  return list
end

-- Make RAM agree with the journal: a committed trade whose given Pokémon is back in the party
-- (crash before saving, or an old save loaded) gets re-applied. Returns number of fixes.
local function reconcile()
  if inBattle() then return 0 end
  local fixed = 0
  for _, e in ipairs(journalEntries()) do
    local recv = monInfo(e.mon)
    local slot = findFp(e.give)
    if slot and recv and not findFp(recv.fp) then
      writeMonHex(RAM.party, slot, e.mon)
      fixed = fixed + 1
      event("re-applied trade " .. e.txn .. " (slot " .. (slot + 1) .. ")")
    end
  end
  return fixed
end
M.reconcile = reconcile

local function applyCommit(m)
  local recv = monInfo(m.mon)
  if not recv then
    send({ t = "trade_done", txn = m.txn, status = "bad_mon" })
    return
  end
  if findFp(recv.fp) then
    send({ t = "trade_done", txn = m.txn, status = "applied" })
    return
  end
  local slot = m.slot
  local cur = slot and monInfo(readMonHex(RAM.party, slot))
  if not cur or cur.fp ~= m.give then slot = findFp(m.give) end
  if not slot then
    event("trade " .. m.txn .. ": the Pokémon you offered is no longer in your party!")
    send({ t = "trade_done", txn = m.txn, status = "missing" })
    return
  end
  -- Journal first (durable intent), then the RAM swap (atomic: we are between two frames).
  writeFile(JOURNAL, json.encode({ txn = m.txn, slot = slot, give = m.give, mon = m.mon }) .. "\n", "ab")
  writeMonHex(RAM.party, slot, m.mon)
  send({ t = "trade_done", txn = m.txn, status = "applied" })
  event("TRADE DONE: received " .. monLabel(recv) .. " in slot " .. (slot + 1) .. ". Save your game!")
end

local function processCommits()
  if #S.pendingCommits == 0 or inBattle() or not net.sock then return end
  local list = S.pendingCommits
  S.pendingCommits = {}
  for _, m in ipairs(list) do applyCommit(m) end
end

------------------------------------------------------------------------------------------------
-- Battles: lockstep plumbing is complete; the game-engine hooks below are Phase 4 work that needs
-- in-emulator verification (docs/phase4_battle.md). M.engine can be replaced at runtime.

local BACKUP = WORK .. "mp_party_backup_" .. PROFILE .. ".txt"

M.engine = {
  -- start the in-game battle against b.opp.party with RNG seed b.seed; return true when running
  begin = function(b) return false, "battle engine hooks not implemented yet (docs/phase4_battle.md)" end,
  -- return this side's action for b.turn once the player chose it, else nil
  localAction = function(b) return table.remove(b.manual, 1) end,
  -- hash of battle state at the start of b.turn (both sides must agree), or nil to skip the check
  stateHash = function(b) return nil end,
  -- feed both sides' actions for a finished turn into the game
  applyTurn = function(b, turn, actions) end,
  -- winner side (0/1) once the battle is over, else nil
  result = function(b) return nil end,
  -- tear down after battle_result
  finish = function(b) end,
}

local function restoreParty()
  local text = readFile(BACKUP)
  if not text then return false end
  local lines = {}
  for l in text:gmatch("[^\r\n]+") do lines[#lines + 1] = l end
  local count = tonumber(lines[1] or "")
  if not count then return false end
  for i = 0, count - 1 do
    if lines[i + 2] and #lines[i + 2] == 200 then writeMonHex(RAM.party, i, lines[i + 2]) end
  end
  emu:write8(RAM.partyCount, count)
  os.remove(BACKUP)
  event("party restored from backup")
  return true
end
M.restoreParty = restoreParty

local function backupParty()
  local n = partyCount()
  local out = { tostring(n) }
  for i = 0, n - 1 do out[#out + 1] = readMonHex(RAM.party, i) end
  writeFile(BACKUP, table.concat(out, "\n") .. "\n")
end

local function battleTick()
  local b = S.battle
  if not b or b.stage ~= "active" then return end
  if b.sentTurn ~= b.turn then
    local a = M.engine.localAction(b)
    if a then
      send({ t = "battle_action", bid = b.bid, turn = b.turn, action = a, hash = M.engine.stateHash(b) })
      b.sentTurn = b.turn
      S.dirty = true
    end
  end
  if not b.ended then
    local w = M.engine.result(b)
    if w ~= nil then
      send({ t = "battle_end", bid = b.bid, winner = w })
      b.ended = true
    end
  end
end

------------------------------------------------------------------------------------------------
-- Server message handlers

local function peerName(id)
  if id == S.myId then return cfg.name end
  local p = S.peers[id]
  return p and p.name or ("#" .. tostring(id))
end

function handlers.welcome(m)
  S.status, S.statusMsg, S.myId = "online", "", m.id
  S.maxPlayers = m.max or 8
  net.backoff = 1
  S.peers = {}
  for _, p in ipairs(m.peers or {}) do S.peers[p.id] = { name = p.name, state = p.state } end
  event("joined room " .. tostring(m.room) .. " as #" .. tostring(m.id))
  local n = reconcile()
  if n > 0 then event(n .. " trade(s) re-applied from journal - save your game") end
  M.forcePresence = true
end

function handlers.join(m)
  S.peers[m.id] = { name = m.name }
  event(m.name .. " joined")
end

function handlers.leave(m)
  local p = S.peers[m.id]
  S.peers[m.id] = nil
  event((p and p.name or "#" .. m.id) .. " left (" .. tostring(m.reason) .. ")")
end

function handlers.state(m)
  local p = S.peers[m.from]
  if not p then p = { name = "#" .. m.from }; S.peers[m.from] = p end
  m.t, m.from = nil, nil
  p.state = m
  S.dirty = true
end

function handlers.chat(m)
  S.chat[#S.chat + 1] = string.format("%s: %s", m.name or peerName(m.from), m.text or "")
  if #S.chat > 8 then table.remove(S.chat, 1) end
  S.dirty = true
  if console and console.log then console:log("[chat] " .. S.chat[#S.chat]) end
end

function handlers.pong(m)
  local sent = net.pingAt[m.n]
  if sent then
    net.rtt = math.floor((S.frame - sent) * 1000 / FPS)
    net.pingAt[m.n] = nil
  end
end

function handlers.error(m)
  event("server error " .. tostring(m.code) .. ": " .. tostring(m.msg))
  if m.fatal then drop(m.code, not NO_RETRY[m.code]); return end
  -- roll back the local step the server just refused
  if m.re == "trade_offer" and S.trade and S.trade.stage == "sending" then S.trade = nil
  elseif m.re == "trade_answer" and S.trade and S.trade.stage == "answered" then S.trade.stage = "offered"
  elseif m.re == "trade_confirm" and S.trade and S.trade.stage == "confirmed" then S.trade.stage = "preview"
  elseif m.re == "battle_team" and S.battle then
    event("your team was refused; mp.decline() to back out")
  end
end

function handlers.direct(m) end

function handlers.trade_pending(m)
  if S.trade then S.trade.txn, S.trade.stage = m.txn, "pending" end
  event("trade offer sent to " .. peerName(m.to) .. "; waiting for them to pick a Pokémon")
end

function handlers.trade_offered(m)
  S.trade = { txn = m.txn, role = "b", peer = m.from, stage = "offered", theirs = monInfo(m.mon) }
  event(string.format("%s offers %s. Type mp.accept(slot) to offer one of yours, or mp.decline()",
    m.name or peerName(m.from), monLabel(S.trade.theirs)))
end

function handlers.trade_preview(m)
  local tr = S.trade
  if not tr or tr.txn ~= m.txn then return end
  tr.theirs, tr.stage = monInfo(m.mon), "preview"
  event(string.format("trade: your %s for their %s. mp.confirm() to trade, mp.cancel() to stop",
    monLabel(tr.mine), monLabel(tr.theirs)))
end

function handlers.trade_confirmed(m)
  if S.trade and S.trade.txn == m.txn then
    S.trade.otherConfirmed = true
    event(peerName(m.by) .. " confirmed the trade")
  end
end

function handlers.trade_abort(m)
  if S.trade and S.trade.txn == m.txn then S.trade = nil end
  event("trade cancelled (" .. tostring(m.reason) .. ")")
end

function handlers.trade_commit(m)
  if S.trade and S.trade.txn == m.txn then S.trade = nil end
  S.pendingCommits[#S.pendingCommits + 1] = m
  processCommits()
end

function handlers.battle_pending(m)
  S.battle = { bid = m.bid, stage = "invited", peer = m.to, manual = {} }
  event("battle request sent to " .. peerName(m.to))
end

function handlers.battle_invite(m)
  S.battle = { bid = m.bid, stage = "invite", peer = m.from, manual = {} }
  event((m.name or peerName(m.from)) .. " challenges you! mp.fight() to accept, mp.decline() to refuse")
end

function handlers.battle_teamreq(m)
  local b = S.battle
  if not b or b.bid ~= m.bid then return end
  local party, slots = {}, partySlots()
  for i = 0, partyCount() - 1 do
    local s = slots[i]
    if s and s.info and s.info.ok then party[#party + 1] = s.hex end
  end
  b.stage = "teams"
  send({ t = "battle_team", bid = b.bid, party = json.array(party) })
end

local function startBattle(m, resumed)
  local b = S.battle
  if not b or b.bid ~= m.bid then
    b = { bid = m.bid, manual = {} }
    S.battle = b
  end
  b.stage, b.side, b.seed, b.turn = "active", m.side, m.seed, m.turn
  b.opp, b.party, b.rules = m.opp, m.party, m.rules
  if resumed and m.have_action then b.sentTurn = m.turn end
  if not resumed then backupParty() end
  local ok, why = M.engine.begin(b)
  b.engineRunning = ok
  event(string.format("battle %s vs %s (seed %08x, you are side %d)%s", resumed and "resumed" or "started",
    tostring(m.opp and m.opp.name), m.seed or 0, m.side or -1, ok and "" or (" - " .. tostring(why))))
end

function handlers.battle_start(m) startBattle(m, false) end
function handlers.battle_resume(m) startBattle(m, true) end

function handlers.battle_turn(m)
  local b = S.battle
  if not b or b.bid ~= m.bid or m.turn < (b.turn or 1) then return end
  M.engine.applyTurn(b, m.turn, m.actions)
  b.turn = m.turn + 1
  S.dirty = true
end

function handlers.battle_wait(m)
  event("battle: opponent " .. tostring(m.reason))
end

function handlers.battle_result(m)
  local b = S.battle
  if not b or b.bid ~= m.bid then return end
  local outcome = m.winner == nil and "no winner" or (m.winner == b.side and "YOU WIN" or "you lose")
  event("battle over: " .. outcome .. " (" .. tostring(m.reason) .. ")")
  if b.stage == "active" and b.engineRunning then
    pcall(M.engine.finish, b)
    restoreParty()
  else
    os.remove(BACKUP)
  end
  S.battle = nil
end

------------------------------------------------------------------------------------------------
-- UI: scripting text buffer + work/mp_status_<profile>.txt

local panel
if console and console.createBuffer then
  local ok, buf = pcall(console.createBuffer, console, "RR Online")
  if ok then panel = buf end
end

local function render()
  local L = {}
  local function add(s) L[#L + 1] = s end
  add(string.format("Radical Red Online %s  [%s%s]  room %s  you: %s%s  %s", VERSION, S.status,
    S.statusMsg ~= "" and (" " .. S.statusMsg) or "", cfg.room, cfg.name,
    S.myId and (" (#" .. S.myId .. ")") or "", net.rtt and (net.rtt .. "ms") or ""))
  local me = sampleState()
  local ids = {}
  for id in pairs(S.peers) do ids[#ids + 1] = id end
  table.sort(ids)
  add(string.format("Players (%d/%d):", #ids + (S.myId and 1 or 0), S.maxPlayers or 8))
  for _, id in ipairs(ids) do
    local p = S.peers[id]
    local st, where = p.state, "?"
    if st and st.map then
      where = string.format("map %d.%d x%d y%d", st.map[1], st.map[2], st.x or 0, st.y or 0)
      if me and me.map[1] == st.map[1] and me.map[2] == st.map[2] then
        local d = math.abs((st.x or 0) - me.x) + math.abs((st.y or 0) - me.y)
        where = where .. string.format("  << HERE, %d tiles", d)
      end
      if st.b == 1 then where = where .. " (in battle)" end
    end
    add(string.format("  #%-2d %-12s %s", id, p.name or "?", where))
  end
  if S.trade then
    add(string.format("Trade with %s: %s  mine=%s theirs=%s", peerName(S.trade.peer), S.trade.stage,
      monLabel(S.trade.mine), monLabel(S.trade.theirs)))
  end
  if S.battle then
    add(string.format("Battle vs %s: %s turn %s", peerName(S.battle.peer), S.battle.stage, tostring(S.battle.turn)))
  end
  if #S.chat > 0 then
    add("Chat:")
    for _, c in ipairs(S.chat) do add("  " .. c) end
  end
  if #S.log > 0 then
    add("Events:")
    for _, c in ipairs(S.log) do add("  " .. c) end
  end
  add("Type mp.help() in the scripting console for commands.")
  local text = table.concat(L, "\n")
  if panel then
    pcall(function() panel:clear(); panel:print(text .. "\n") end)
  end
  writeFile(WORK .. "mp_status_" .. PROFILE .. ".txt", text .. "\n")
  S.dirty = false
end
M.render = render

------------------------------------------------------------------------------------------------
-- Frame loop

local lastState, lastSentFrame, sendSecond, sentThisSecond = nil, -9999, 0, 0
local errAt = -9999

local function presenceTick()
  if not net.sock or S.status ~= "online" then return end
  local st = sampleState()
  if not st then return end
  local enc = json.encode(st)
  local now = os.time()
  if now ~= sendSecond then sendSecond, sentThisSecond = now, 0 end
  local changed = enc ~= lastState
  if (changed or M.forcePresence or S.frame - lastSentFrame >= FPS) and sentThisSecond < 15 then
    st.t = "state"
    send(st)
    lastState, lastSentFrame, M.forcePresence = enc, S.frame, false
    sentThisSecond = sentThisSecond + 1
  end
end

local function tick()
  S.frame = S.frame + 1
  local f = S.frame
  if cfg.manual_poll == "1" and net.sock and net.sock.poll then pcall(net.sock.poll, net.sock) end
  if net.sock then
    if S.status == "online" or S.status == "connecting" then
      if f >= net.nextPing then
        net.pingN = net.pingN + 1
        net.pingAt[net.pingN] = f
        net.pingAt[net.pingN - 10] = nil
        send({ t = "ping", n = net.pingN })
        net.nextPing = f + 2 * FPS
      end
      if f - net.lastRx > 6 * FPS then drop("timeout (no data from server for 6s)") end
    end
    if f % 6 == 0 then presenceTick() end
    if f % 30 == 0 then processCommits() end
    if f % 300 == 0 and S.status == "online" and reconcile() > 0 then event("save your game!") end
    battleTick()
    flush()
  elseif S.status == "offline" and net.auto and f >= net.retryAt then
    connect()
  end
  if f % 300 == 0 then touchLock() end
  if S.dirty and f % 15 == 0 or f % 60 == 0 then render() end
end

local frameCb = callbacks:add("frame", function()
  local ok, e = pcall(tick)
  if not ok and S.frame - errAt > 5 * FPS then
    errAt = S.frame
    if console and console.error then console:error("[mp] " .. tostring(e)) end
  end
end)

local shutdownCb
function M.shutdown()
  if net.sock then pcall(function() send({ t = "bye" }); flush(); net.sock:close() end) end
  net.sock = nil
  pcall(callbacks.remove, callbacks, frameCb)
  if shutdownCb then pcall(callbacks.remove, callbacks, shutdownCb) end
  os.remove(WORK .. "mp_lock_" .. PROFILE .. ".txt")
end
local okCb, id = pcall(callbacks.add, callbacks, "shutdown", M.shutdown)
if okCb then shutdownCb = id end

------------------------------------------------------------------------------------------------
-- Console commands:  mp.help()

local function resolvePeer(who)
  if type(who) == "number" then return S.peers[who] and who or nil end
  if type(who) == "string" then
    local lw = who:lower()
    for id, p in pairs(S.peers) do
      if (p.name or ""):lower() == lw then return id end
    end
    for id, p in pairs(S.peers) do
      if (p.name or ""):lower():find(lw, 1, true) then return id end
    end
  end
  return nil
end

local function say(s) if console and console.log then console:log(s) end; return s end

local mp = {}
_G.mp = mp
M.mp = mp

function mp.help()
  return say([[Radical Red Online commands (type into the scripting console):
  mp.who()               list players in your room
  mp.say("hi")           chat
  mp.party()             show your party slots
  mp.trade(who, slot)    offer party slot 1-6 to a player (who = #id or name)
  mp.accept(slot)        answer an incoming trade with your slot 1-6
  mp.confirm()           confirm the trade shown in the panel
  mp.cancel()/decline()  cancel the trade / decline a trade or battle
  mp.battle(who)         challenge a player     mp.fight()  accept a challenge
  mp.forfeit()           give up the current battle
  mp.name("Red")  mp.room("ABCD")  mp.connect("host", port)  mp.disconnect()  mp.reload()]])
end

function mp.who()
  local out = { string.format("you: #%s %s (room %s, %s)", tostring(S.myId), cfg.name, cfg.room, S.status) }
  for id, p in pairs(S.peers) do out[#out + 1] = string.format("#%d %s", id, p.name or "?") end
  return say(table.concat(out, "\n"))
end

function mp.party()
  local out = {}
  for i, s in pairs(partySlots()) do
    out[#out + 1] = string.format("slot %d: %s%s", i + 1, monLabel(s.info),
      s.info and not s.info.ok and " (looks invalid)" or "")
  end
  return say(#out > 0 and table.concat(out, "\n") or "party is empty")
end

function mp.say(text)
  if not send({ t = "chat", text = tostring(text) }) then return say("not connected") end
  return true
end

local function pickSlot(slot)
  slot = tonumber(slot)
  if not slot or slot < 1 or slot > partyCount() then return nil, "slot must be 1-" .. partyCount() end
  local hex = readMonHex(RAM.party, slot - 1)
  local info = monInfo(hex)
  if not info or not info.ok then return nil, "slot " .. slot .. " has no valid Pokémon" end
  return slot - 1, hex, info
end

function mp.trade(who, slot)
  local id = resolvePeer(who)
  if not id then return say("no such player: " .. tostring(who)) end
  if S.trade then return say("finish or mp.cancel() your current trade first") end
  if inBattle() then return say("can't trade during a battle") end
  local s, hex, info = pickSlot(slot)
  if not s then return say(hex) end
  S.trade = { role = "a", peer = id, stage = "sending", mine = info, slot = s }
  send({ t = "trade_offer", to = id, slot = s, mon = hex })
  return true
end

function mp.accept(slot)
  local tr = S.trade
  if not tr or tr.stage ~= "offered" then return say("no incoming trade to accept") end
  if inBattle() then return say("can't trade during a battle") end
  local s, hex, info = pickSlot(slot)
  if not s then return say(hex) end
  tr.mine, tr.slot, tr.stage = info, s, "answered"
  send({ t = "trade_answer", txn = tr.txn, slot = s, mon = hex })
  return true
end

function mp.confirm()
  local tr = S.trade
  if not tr or tr.stage ~= "preview" then return say("nothing to confirm yet") end
  -- Re-check: the Pokémon must still be where it was offered
  local cur = monInfo(readMonHex(RAM.party, tr.slot))
  if not cur or cur.fp ~= tr.mine.fp then
    send({ t = "trade_cancel", txn = tr.txn })
    return say("your offered Pokémon moved; trade cancelled")
  end
  tr.stage = "confirmed"
  send({ t = "trade_confirm", txn = tr.txn })
  return true
end

function mp.cancel()
  if S.trade and S.trade.txn then
    send({ t = "trade_cancel", txn = S.trade.txn })
    return true
  end
  S.trade = nil
  return say("no trade open")
end

function mp.battle(who, rules)
  local id = resolvePeer(who)
  if not id then return say("no such player: " .. tostring(who)) end
  if S.battle then return say("already in a battle") end
  send({ t = "battle_req", to = id, rules = rules or {} })
  return true
end

function mp.fight()
  local b = S.battle
  if not b or b.stage ~= "invite" then return say("no challenge to accept") end
  send({ t = "battle_accept", bid = b.bid })
  return true
end

function mp.decline()
  if S.trade and S.trade.stage == "offered" then return mp.cancel() end
  local b = S.battle
  if b and (b.stage == "invite" or b.stage == "invited" or b.stage == "teams") then
    send({ t = "battle_decline", bid = b.bid })
    return true
  end
  return say("nothing to decline")
end

function mp.forfeit()
  local b = S.battle
  if not b then return say("not in a battle") end
  send({ t = "battle_forfeit", bid = b.bid })
  return true
end

-- queue a manual battle action (used until the engine hooks exist, and by tests)
function mp.act(action)
  local b = S.battle
  if not b or b.stage ~= "active" then return say("no active battle") end
  b.manual[#b.manual + 1] = action
  return true
end

function mp.restore_party() return restoreParty() or say("no party backup found") end

function mp.name(n)
  n = tostring(n or ""):sub(1, 12)
  if n == "" then return say("usage: mp.name(\"Red\")") end
  saveProfileSetting("name", n)
  return mp.reload()
end

function mp.room(r)
  r = tostring(r or "")
  if not r:match("^[%w_%-]+$") or #r > 16 then return say("room: 1-16 letters, digits, _ or -") end
  saveProfileSetting("room", r)
  return mp.reload()
end

function mp.connect(host, port)
  if host then saveProfileSetting("host", tostring(host)) end
  if port then saveProfileSetting("port", tostring(port)) end
  return mp.reload()
end

function mp.disconnect()
  net.auto = false
  if net.sock then send({ t = "bye" }); flush() end
  drop("disconnected by you", false)
  return true
end

function mp.reload()
  if net.sock then send({ t = "bye" }); flush() end
  drop("reconnecting", true)
  loadConfig()
  net.auto, net.backoff, net.retryAt = true, 1, S.frame
  return true
end

function mp.status() render(); return say(readFile(WORK .. "mp_status_" .. PROFILE .. ".txt") or "") end

-- startup
if readFile(BACKUP) then
  event("a party backup from an interrupted battle exists; type mp.restore_party() if your party looks wrong")
end
event(string.format("rr_mp.lua %s loaded (profile %d, work dir %s). Type mp.help()", VERSION, PROFILE, WORK))
if net.auto then connect() end
render()

return M
