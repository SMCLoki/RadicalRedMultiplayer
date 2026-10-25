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

local TRACE_PATH
function _RRMP_TRACE(msg)
  if not TRACE_PATH then return end
  local f = io.open(TRACE_PATH, "ab"); if f then f:write(os.clock(), " ", msg, string.char(10)); f:close() end
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
if io.open(WORK .. "mp_trace_on", "rb") then TRACE_PATH = WORK .. "mp_trace.txt" end
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

-- The launcher passes its settings in the environment: RRMP_HOST, RRMP_PORT, RRMP_ROOM, RRMP_NAME, RRMP_PROFILE
local function envSetting(key)
  local ok, v = pcall(os.getenv, "RRMP_" .. key:upper())
  if ok and v and v ~= "" then return v end
end

-- Started by the launcher: keep out of mGBA's console entirely (the launcher shows the status file). Updating the
-- console from the game thread hung mGBA 0.11 when the window lost focus or Settings opened (real play 2026-10-25).
local QUIET = envSetting("nopanel") == "1"

local function pickProfile()
  if RRMP_PROFILE then return tonumber(RRMP_PROFILE) end
  if tonumber(envSetting("profile") or "") then return tonumber(envSetting("profile")) end
  local now = os.time()
  for n = 1, 8 do
    local t = tonumber(readFile(WORK .. "mp_lock_" .. n .. ".txt") or "")
    if not t or now - t > 15 then return n end
  end
  return 1
end

local PROFILE = pickProfile()
M.profile = PROFILE   -- read by test tools (pilot.lua picks pilot<N>.txt for profile N > 1)
local function touchLock() writeFile(WORK .. "mp_lock_" .. PROFILE .. ".txt", tostring(os.time())) end
touchLock()

local cfg
local envApplied = false
local function loadConfig()
  cfg = { host = "127.0.0.1", port = "8765", room = "LOBBY", name = "Player" .. PROFILE, autoconnect = "1" }
  local shared = readFile(WORK .. "mp_config.txt")
  if not shared then
    writeFile(WORK .. "mp_config.txt", CONFIG_HELP .. "host=127.0.0.1\nport=8765\nroom=LOBBY\n")
  end
  parseConfig(shared, cfg)
  parseConfig(readFile(WORK .. "mp_config_" .. PROFILE .. ".txt"), cfg)
  if RRMP_CONFIG then for k, v in pairs(RRMP_CONFIG) do cfg[k] = tostring(v) end end
  -- launcher settings win on the first load only, so mp.name() / mp.room() still work in the session
  if not envApplied then
    for _, k in ipairs({ "host", "port", "room", "name" }) do
      local v = envSetting(k)
      if v then cfg[k] = v end
    end
    envApplied = true
  end
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
  playerAvatar = 0x02037078,
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

-- Step history for puppet rendering on other clients: every tile/facing change of the player object
-- {seq, x, y, facing, movementActionId}. Sampled every frame, the last 8 ride along with each presence update.
local steps, stepSeq, lastStepKey, footGfx = {}, 0, nil, 0

local function sampleSteps()
  local o = RAM.objEvents
  if emu:read32(o) & 0x10001 ~= 0x10001 then return end   -- player object active + isPlayer
  local cx, cy = emu:read16(o + 0x10), emu:read16(o + 0x12)
  if cx >= 0x8000 then cx = cx - 0x10000 end
  if cy >= 0x8000 then cy = cy - 0x10000 end
  local f = emu:read8(o + 0x18) & 0x0F
  local key = cx .. "," .. cy .. "," .. f
  if key == lastStepKey then return end
  lastStepKey = key
  stepSeq = stepSeq + 1
  steps[#steps + 1] = json.array({ stepSeq, cx - 7, cy - 7, f, emu:read8(o + 0x1C) })
  if #steps > 8 then table.remove(steps, 1) end
  if emu:read8(RAM.playerAvatar) & 1 ~= 0 then footGfx = emu:read8(o + 5) end  -- on-foot sprite
end

local function sampleState()
  local sb1 = emu:read32(RAM.sb1Ptr)
  if sb1 < 0x02000000 or sb1 >= 0x02040000 then return nil end
  local x, y = emu:read16(sb1), emu:read16(sb1 + 2)
  if x >= 0x8000 then x = x - 0x10000 end
  if y >= 0x8000 then y = y - 0x10000 end
  -- facing: low nibble of objectEvents[0]+0x18 (verified 1-4 = down/up/left/right on RR)
  local facing = emu:read8(RAM.objEvents + 0x18) & 0x0F
  return { map = json.array({ emu:read8(sb1 + 4), emu:read8(sb1 + 5) }), x = x, y = y, f = facing,
           b = inBattle() and 1 or 0, g = footGfx, e = emu:read8(RAM.objEvents + 0x0B) & 0x0F,
           st = json.array({ table.unpack(steps) }) }
end

local function gameId()
  -- mGBA 0.10 says "AGB-BPRE", 0.11 says "BPRE": keep only the 4-letter game code so mixed versions can play
  local code = tostring(emu:getGameCode() or "GBA"):gsub("^%u%u%u%-", ""):gsub("[^%w%-]", "")
  local crc
  if emu.checksum then
    -- 0.10: emu:checksum(type) defaults to CRC32 and returns 4 big-endian bytes
    local ok, raw = pcall(emu.checksum, emu)
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
  if console and console.log and not QUIET then console:log("[mp] " .. msg) end
end

-- Remote players drawn as overworld characters (client/rr_visuals.lua, docs/visuals.md)
local visuals
do
  local ok, err = pcall(dofile, scriptDir() .. "/rr_visuals.lua")
  if ok and _RRMP_VISUALS_NEW then
    visuals = _RRMP_VISUALS_NEW({ enabled = cfg.visuals ~= "0", gfx = cfg.puppet_gfx, event = event,
                                  cbOverworld = cfg.cb_overworld,
                                  buildScript = cfg.talk_menu ~= "0" and function(peer, base)
                                    return M.buildTalkScript and M.buildTalkScript(peer, base)
                                  end or nil })
  else
    event("visuals unavailable: " .. tostring(err))
  end
end
M.visuals = visuals
local visualsErrAt = -9999

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

-- Real time in seconds. Timers use it instead of frame counts: with fast-forward the game runs many frames per
-- second, which made the 6 s timeout fire in well under a second and file writes multiply (mGBA froze).
-- On Windows os.clock() is wall-clock time since the program started.
local function clock() return os.clock() end

local net = { sock = nil, inbuf = "", outbuf = "", lastRx = 0, nextPing = 0, backoff = 1,
              retryAt = 0, pingN = 0, pingAt = {}, rtt = nil, auto = cfg.autoconnect ~= "0" }
M.net = net
local handlers = {}
-- mGBA 0.10 reports EAGAIN as "temporary failure" (src/script/engines/lua.c)
local TRANSIENT = { again = true, ["temporary failure"] = true }
local NO_RETRY = { bad_version = true, game_mismatch = true, room_full = true, replaced = true, bad_hello = true }

local function send(msg)
  if not net.sock then return false end
  local ok, line = pcall(json.encode, msg)
  if not ok then event("encode failed: " .. tostring(line)); return false end
  net.outbuf = net.outbuf .. line .. "\n"
  return true
end
M.send = send

-- Sockets are closed from our own frame callback, never from inside a socket callback: mGBA 0.10's
-- socket:close() removes the socket's poll callback from the frame list while it is being run.
local pendingClose = {}
local function closePending()
  for i = #pendingClose, 1, -1 do
    local s = table.remove(pendingClose, i)
    pcall(function() s:close() end)
  end
end

local function drop(reason, retry)
  _RRMP_TRACE("drop " .. tostring(reason))
  if net.sock then pendingClose[#pendingClose + 1] = net.sock end
  net.sock, net.inbuf, net.outbuf = nil, "", ""
  S.myId, S.peers = nil, {}
  if visuals then visuals.peers = {} end
  if S.trade then S.trade = nil end
  if retry == false or not net.auto then
    S.status = "stopped"
  else
    S.status = "offline"
    net.retryAt = clock() + net.backoff
    net.backoff = math.min(net.backoff * 2, 10)
  end
  S.statusMsg = reason or ""
  S.dirty = true
  event("disconnected: " .. tostring(reason))
end

local function flush()
  if net.outbuf ~= "" then _RRMP_TRACE("flush " .. #net.outbuf) end
  if not net.sock or net.outbuf == "" then return end
  local ok, sent, err = pcall(net.sock.send, net.sock, net.outbuf)
  if not ok then drop("send failed: " .. tostring(sent)); return end
  if sent == nil then
    if not TRANSIENT[err] then drop("send: " .. tostring(err)) end
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
  local t = msg.t  -- handlers may modify msg (handlers.state clears msg.t)
  _RRMP_TRACE("line " .. t)
  local h = handlers[t]
  if h then
    local ok, e = pcall(h, msg)
    if not ok then event("handler " .. t .. " failed: " .. tostring(e)) end
    _RRMP_TRACE("handled " .. t)
  end
end

local function onReceived()
  _RRMP_TRACE("onReceived")
  local sock = net.sock
  if not sock then return end
  local ok, data, err = pcall(sock.receive, sock, 4096)
  if sock ~= net.sock then return end
  if not ok then drop("receive failed: " .. tostring(data)); return end
  if not data or data == "" then
    if not TRANSIENT[err] then drop(err or "disconnected") end
    return
  end
  net.lastRx = clock()
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
  local t0 = os.time()
  local ok, sock, err = pcall(socket.connect, cfg.host, tonumber(cfg.port) or 8765)
  if not ok or not sock then
    local why = tostring(ok and err or sock)
    if os.time() - t0 >= 3 then
      -- connect() blocks the emulator; an unreachable host can freeze it for ~20 s per try, so stop retrying
      drop("server unreachable (" .. why .. "). Check host in mp_config.txt, then type mp.reload()", false)
    else
      drop("connect failed: " .. why)
    end
    return
  end
  net.sock, net.inbuf, net.outbuf = sock, "", ""
  net.lastRx, net.nextPing = clock(), clock() + 2
  sock:add("received", onReceived)
  -- mGBA calls socket callbacks as cb(socket, ...)
  sock:add("error", function(_, e) _RRMP_TRACE("cb error " .. tostring(e)); if net.sock == sock then drop("socket error " .. tostring(e)) end end)
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
  local entries = journalEntries()
  -- An entry is superseded once a later trade handed its `give` Pokémon back to us: re-applying it
  -- would swap that Pokémon for one we have since traded on (seen in the real emulator).
  local receivedLater = {}
  for i = #entries, 1, -1 do
    local e = entries[i]
    e.superseded = receivedLater[e.give] or false
    local info = monInfo(e.mon)
    if info then receivedLater[info.fp] = true end
  end
  for _, e in ipairs(entries) do
    local recv = not e.superseded and monInfo(e.mon)
    local slot = recv and findFp(e.give)
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
  -- Already applied only if the received Pokémon is here AND the given one is gone: two saves copied from the same
  -- file hold identical Pokémon (same PID+OT), so "received fp present" alone skipped a real trade and duplicated
  -- the given Pokémon (real mGBA run 11).
  if findFp(recv.fp) and not findFp(m.give) then
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

-- The real engine (client/rr_battle.lua) replaces the manual one above unless pvp_engine=manual.
if cfg.pvp_engine ~= "manual" then
  local ok, err = pcall(dofile, scriptDir() .. "/rr_battle.lua")
  if ok and _RRMP_BATTLE_NEW then
    M.engine = _RRMP_BATTLE_NEW({ event = event, cfg = cfg, work = WORK, profile = PROFILE, null = json.null,
                                  frame = function() return S.frame end, restoreParty = restoreParty,
                                  forfeit = function() if _G.mp then return _G.mp.forfeit() end end })
  else
    event("battle engine unavailable: " .. tostring(err))
  end
end

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
  _RRMP_TRACE("welcome")
  S.status, S.statusMsg, S.myId = "online", "", m.id
  S.maxPlayers = m.max or 8
  net.backoff = 1
  S.peers = {}
  if visuals then visuals.peers = {} end
  for _, p in ipairs(m.peers or {}) do
    S.peers[p.id] = { name = p.name, state = p.state }
    if visuals and type(p.state) == "table" then visuals.onState(p.id, p.state) end
  end
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
  if visuals then visuals.onLeave(m.id) end
  event((p and p.name or "#" .. m.id) .. " left (" .. tostring(m.reason) .. ")")
end

function handlers.state(m)
  local id = m.from
  local p = S.peers[id]
  if not p then p = { name = "#" .. id }; S.peers[id] = p end
  m.t, m.from = nil, nil
  p.state = m
  if visuals then visuals.onState(id, m) end
  S.dirty = true
end

function handlers.chat(m)
  S.chat[#S.chat + 1] = string.format("%s: %s", m.name or peerName(m.from), m.text or "")
  if #S.chat > 8 then table.remove(S.chat, 1) end
  S.dirty = true
  if console and console.log and not QUIET then console:log("[chat] " .. S.chat[#S.chat]) end
end

function handlers.pong(m)
  local sent = net.pingAt[m.n]
  if sent then
    net.rtt = math.floor((clock() - sent) * 1000)
    net.pingAt[m.n] = nil
  end
end

function handlers.error(m)
  event("server error " .. tostring(m.code) .. ": " .. tostring(m.msg)
    .. (m.code == "game_mismatch" and (" (your ROM id " .. gameId() .. ")") or ""))
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
  local old = S.trade
  S.trade = { txn = m.txn, role = "b", peer = m.from, stage = "offered", theirs = monInfo(m.mon) }
  if old and old.peer == m.from and old.stage == "sending" then
    -- our offer crossed theirs (both sent before seeing the other's); the server turned ours into the answer
    S.trade.mine, S.trade.slot, S.trade.stage = old.mine, old.slot, "answered"
  end
  event(string.format("%s offers %s. Type mp.accept(slot) to offer one of yours, or mp.decline()",
    m.name or peerName(m.from), monLabel(S.trade.theirs)))
end

function handlers.trade_preview(m)
  local tr = S.trade
  if not tr or tr.txn ~= m.txn then return end
  tr.theirs, tr.stage = monInfo(m.mon), "preview"
  if tr.autoConfirm then
    -- answered through the in-game menu after seeing their Pokemon: picking ours was the confirmation
    _G.mp.confirm()
    return
  end
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
  b.resumed = resumed
  local oppPeer = m.opp and S.peers[m.opp.id]
  b.oppGfx = oppPeer and oppPeer.state and oppPeer.state.g
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
  local winner = m.winner ~= json.null and m.winner or nil
  local outcome = winner == nil and "no winner" or (winner == b.side and "YOU WIN" or "you lose")
  event("battle over: " .. outcome .. " (" .. tostring(m.reason) .. ")")
  if b.stage == "active" and b.engineRunning then
    -- the engine may defer the restore until the game is back on the map
    local ok, deferred = pcall(M.engine.finish, b, winner)
    if not (ok and deferred == true) then restoreParty() end
  else
    os.remove(BACKUP)
  end
  S.battle = nil
end

------------------------------------------------------------------------------------------------
-- UI: scripting text buffer + work/mp_status_<profile>.txt

local panel
if console and console.createBuffer and not QUIET and not io.open(WORK .. "mp_nopanel", "rb") then
  local ok, buf = pcall(console.createBuffer, console, "RR Online")
  if ok and buf then
    panel = buf
    pcall(buf.setSize, buf, 100, 40)
  end
end

local function render()
  _RRMP_TRACE("render")
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
    add(string.format("Battle vs %s: %s turn %s%s", peerName(S.battle.peer), S.battle.stage, tostring(S.battle.turn),
      M.engine.status and M.engine.status() and ("  [" .. M.engine.status() .. "]") or ""))
  end
  if visuals then add(visuals.status()) end
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
  _RRMP_TRACE("render text built")
  if panel then
    _RRMP_TRACE("panel clear")
    pcall(panel.clear, panel)
    _RRMP_TRACE("panel print")
    pcall(panel.print, panel, text .. "\n")
    _RRMP_TRACE("panel done")
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

-- Commands from the launcher: it writes lines to work/mp_cmd_<profile>.txt; we run and delete them.
-- "say <text>" chats (any text, no quoting needed); "mp.<command>(...)" runs a console command.
local CMD_PATH = WORK .. "mp_cmd_" .. PROFILE .. ".txt"
local luaLoad = load
local function pollCommands()
  local text = readFile(CMD_PATH)
  if not text then return end
  os.remove(CMD_PATH)
  for line in text:gmatch("[^\r\n]+") do
    local chat = line:match("^say (.+)$")
    if chat then
      pcall(_G.mp.say, chat)
    elseif line:match("^%s*mp%.[%w_]+%b()%s*$") then
      local fn, err = luaLoad(line, "launcher", "t", _ENV)
      local ok = true
      if fn then ok, err = pcall(fn) end
      if not fn or not ok then event("launcher command failed: " .. line .. " (" .. tostring(err) .. ")") end
    else
      event("launcher command ignored: " .. line)
    end
  end
end

-- run something at most once per `secs` of real time (not per frame: fast-forward must not multiply file I/O)
local lastRun = {}
local function every(name, secs)
  local t = clock()
  if t - (lastRun[name] or -1e9) >= secs then lastRun[name] = t; return true end
  return false
end

-- Autosave: a save state every AUTOSAVE_SECONDS while walking around (never in a battle, trade, menu or
-- script), rotating through 3 files in work/: autosave_<profile>_1..3.ss. Load one with mGBA's
-- File > Load State File. It does not write the in-game .sav; use the game's Save for that.
local AUTOSAVE_SECONDS = tonumber(cfg.autosave_seconds or "") or 10
local autosaveN = 0
local function autosave()
  if AUTOSAVE_SECONDS <= 0 or not emu.saveStateFile then return end
  if M.engine.cur or (M.engine.inOverworld and not M.engine.inOverworld()) then return end
  if S.trade and S.trade.stage then return end
  autosaveN = autosaveN % 3 + 1
  -- flags 2|8 = save data + RTC, no screenshot: taking the screenshot from the frame callback hung mGBA 0.11
  -- next to the ROM when the launcher says where it is (RRMP_AUTOSAVE = "<rom folder>\<rom name>_autosave"),
  -- otherwise in work/
  local base = envSetting("autosave") or cfg.autosave_path or (WORK .. "autosave_" .. PROFILE)
  pcall(emu.saveStateFile, emu, base .. "_" .. autosaveN .. ".ss", 10)
end

local function tick()
  S.frame = S.frame + 1
  local t = clock()
  if every("commands", 0.5) then pcall(pollCommands) end
  if #pendingClose > 0 then closePending() end
  if S.frame % 30 == 0 then _RRMP_TRACE("tick " .. S.frame) end
  local f = S.frame
  sampleSteps()
  if M.engine.tick then
    local ok, e = pcall(M.engine.tick, f)
    if not ok and f - visualsErrAt > 5 * FPS then
      visualsErrAt = f
      event("battle engine error: " .. tostring(e))
    end
  end
  if visuals and S.status == "online" then
    local ok, e = pcall(visuals.tick, f)
    if not ok and f - visualsErrAt > 5 * FPS then
      visualsErrAt = f
      event("visuals error: " .. tostring(e))
    end
    if M.pollTalkResult then
      local ok2, e2 = pcall(M.pollTalkResult)
      if not ok2 then event("talk menu error: " .. tostring(e2)) end
    end
  end
  if cfg.manual_poll == "1" and net.sock and net.sock.poll then pcall(net.sock.poll, net.sock) end
  if net.sock then
    if S.status == "online" or S.status == "connecting" then
      if t >= net.nextPing then
        net.pingN = net.pingN + 1
        net.pingAt[net.pingN] = t
        net.pingAt[net.pingN - 10] = nil
        send({ t = "ping", n = net.pingN })
        net.nextPing = t + 2
      end
      if t - net.lastRx > 6 then drop("timeout (no data from server for 6s)") end
    end
    if f % 6 == 0 then presenceTick() end
    if every("commits", 0.5) then processCommits() end
    if every("reconcile", 5) and S.status == "online" and reconcile() > 0 then event("save your game!") end
    battleTick()
    flush()
  elseif S.status == "offline" and net.auto and t >= net.retryAt then
    connect()
  end
  if every("lock", 5) then touchLock() end
  if (S.dirty and every("render", 0.25)) or every("render_idle", 1) then render() end
  if every("autosave", AUTOSAVE_SECONDS) then autosave() end
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
  if visuals then visuals.shutdown() end
  if net.sock then pcall(function() send({ t = "bye" }); flush(); net.sock:close() end) end
  net.sock = nil
  pcall(callbacks.remove, callbacks, frameCb)
  if shutdownCb then pcall(callbacks.remove, callbacks, shutdownCb) end
  os.remove(WORK .. "mp_lock_" .. PROFILE .. ".txt")
end
local okCb, id = pcall(callbacks.add, callbacks, "shutdown", M.shutdown)
if okCb then shutdownCb = id end

------------------------------------------------------------------------------------------------
-- Talk-to-player menu. Pressing A on another player's character runs a small event script (built here, kept in
-- RAM by rr_visuals.lua for the puppet you are facing) that uses the game's own text box, TRADE CENTER /
-- COLOSSEUM menu, YES/NO box and party screen. The script leaves its answer in special variables:
-- 0x8007 = which question, 0x8005 = menu/YES-NO answer, 0x8004 = party slot, 0x8006 = "done" marker.
-- FireRed script opcodes: pokefirered data/script_cmd_table.inc; menu 39 = MULTICHOICE_TRADE_CENTER_COLOSSEUM;
-- special 159 (0x9F) = ChoosePartyMon (result in 0x8004): RR's own trade scripts use special 159 + waitstate + copyvar
-- x, 0x8004 (43 uses in the ROM); 160 hangs the script (real mGBA run 11). Both can be overridden (menu_list,
-- special_choose_party).

local SV = { v8004 = 0x020370C0, v8005 = 0x020370C2, v8006 = 0x020370C4, v8007 = 0x020370C6,
             lastTalked = 0x020370D2 }
local SCRIPT_DONE = 0x4D50
local MENU_LIST = tonumber(cfg.menu_list or "") or 39
local SPECIAL_CHOOSE_PARTY = tonumber(cfg.special_choose_party or "") or 159
local Q_MENU, Q_TRADE_OFFER, Q_BATTLE_INVITE, Q_TRADE_PREVIEW, Q_WAITING, Q_BUSY = 1, 2, 3, 4, 5, 6

local function gen3text(str)
  local map = { [" "] = 0x00, ["!"] = 0xAB, ["?"] = 0xAC, ["."] = 0xAD, ["-"] = 0xAE, [","] = 0xB8,
                [":"] = 0xF0, ["'"] = 0xB4, ["\n"] = 0xFE }
  local out, i = {}, 1
  while i <= #str do
    local c = str:sub(i, i)
    if c == "{" then
      local j = str:find("}", i, true)
      local tag = str:sub(i + 1, j - 1)
      out[#out + 1] = tag == "S1" and "\xFD\x02" or "\xFD\x03"   -- STR_VAR_1 / STR_VAR_2
      i = j + 1
    else
      local b = c:byte()
      local code
      if b >= 65 and b <= 90 then code = 0xBB + b - 65
      elseif b >= 97 and b <= 122 then code = 0xD5 + b - 97
      elseif b >= 48 and b <= 57 then code = 0xA1 + b - 48
      else code = map[c] or 0xAC end
      out[#out + 1] = string.char(code)
      i = i + 1
    end
  end
  return table.concat(out) .. "\xFF"
end
M.gen3text = gen3text

-- tiny two-pass assembler for FireRed event scripts
local function newScript()
  local sc = { parts = {}, size = 0, fix = {}, labels = {}, texts = {} }
  function sc:b(...)
    for _, v in ipairs({ ... }) do self.parts[#self.parts + 1] = string.char(v & 0xFF); self.size = self.size + 1 end
  end
  function sc:u16(v) self:b(v & 0xFF, (v >> 8) & 0xFF) end
  function sc:ptr(label)
    self.fix[#self.fix + 1] = { pos = #self.parts + 1, label = label }
    self:b(0, 0, 0, 0)
  end
  function sc:label(name) self.labels[name] = self.size end
  function sc:text(name, str) self.texts[#self.texts + 1] = { name = name, data = gen3text(str) } end
  function sc:build(base)
    local off = self.size
    for _, t in ipairs(self.texts) do self.labels[t.name] = off; off = off + #t.data end
    for _, f in ipairs(self.fix) do
      local a = base + self.labels[f.label]
      for k = 0, 3 do self.parts[f.pos + k] = string.char((a >> (8 * k)) & 0xFF) end
    end
    local out = { table.concat(self.parts) }
    for _, t in ipairs(self.texts) do out[#out + 1] = t.data end
    return table.concat(out)
  end
  -- commands
  function sc:setvar(var, v) self:b(0x16); self:u16(var); self:u16(v) end
  function sc:copyvar(dst, src) self:b(0x19); self:u16(dst); self:u16(src) end
  function sc:message(label) self:b(0x67); self:ptr(label); self:b(0x66) end   -- message + waitmessage
  function sc:speciesName(strVar, species) self:b(0x7D, strVar); self:u16(species) end
  function sc:yesno() self:b(0x6E, 20, 8); self:copyvar(0x8005, 0x800D) end
  function sc:gotoIfResultNot(v, label) self:b(0x21); self:u16(0x800D); self:u16(v); self:b(0x06, 5); self:ptr(label) end
  function sc:chooseParty() self:b(0x25); self:u16(SPECIAL_CHOOSE_PARTY); self:b(0x27) end
  return sc
end

local function questionFor(peer)
  local tr, bt = S.trade, S.battle
  if S.status ~= "online" then return Q_BUSY end
  if bt and bt.peer == peer and bt.stage == "invite" then return Q_BATTLE_INVITE end
  if tr and tr.peer == peer and tr.stage == "offered" then return Q_TRADE_OFFER end
  if tr and tr.peer == peer and tr.stage == "preview" and tr.role == "a" then return Q_TRADE_PREVIEW end
  if (tr and tr.peer == peer) or (bt and bt.peer == peer) then return Q_WAITING end
  if tr or bt or inBattle() then return Q_BUSY end
  return Q_MENU
end

function M.buildTalkScript(peer, base)
  local p = S.peers[peer]
  if not p then return nil end
  local name = tostring(p.name or "Player"):sub(1, 10)
  local q = questionFor(peer)
  local sc = newScript()
  sc:b(0x6A, 0x5A)                       -- lock, faceplayer
  sc:setvar(0x8004, 0xFF)
  sc:setvar(0x8005, 0)
  sc:setvar(0x8007, q)
  if q == Q_MENU then
    sc:text("t", name .. ": Let's link up!\nTrade or battle?")
    sc:message("t")
    sc:b(0x6F, 0, 0, MENU_LIST, 0)        -- multichoice: TRADE CENTER / COLOSSEUM / EXIT
    sc:copyvar(0x8005, 0x800D)
    sc:gotoIfResultNot(0, "done")
    sc:chooseParty()
  elseif q == Q_TRADE_OFFER then
    sc:speciesName(0, S.trade.theirs and S.trade.theirs.species or 0)
    sc:text("t", name .. " offers {S1}!\nTrade one of yours for it?")
    sc:message("t")
    sc:yesno()
    sc:gotoIfResultNot(1, "done")
    sc:chooseParty()
  elseif q == Q_BATTLE_INVITE then
    sc:text("t", name .. " wants to battle!\nAccept the challenge?")
    sc:message("t")
    sc:yesno()
  elseif q == Q_TRADE_PREVIEW then
    sc:speciesName(0, S.trade.theirs and S.trade.theirs.species or 0)
    sc:speciesName(1, S.trade.mine and S.trade.mine.species or 0)
    sc:text("t", name .. " offers {S1}\nfor your {S2}. Trade?")
    sc:message("t")
    sc:yesno()
  elseif q == Q_WAITING then
    sc:text("t", "Waiting for " .. name .. "...\nCancel your request?")
    sc:message("t")
    sc:yesno()
  else
    sc:text("t", name .. " can't right now.\nFinish your current link first.")
    sc:message("t")
    sc:b(0x6D)                            -- waitbuttonpress
  end
  sc:label("done")
  sc:setvar(0x8006, SCRIPT_DONE)
  sc:b(0x68, 0x6C, 0x02)                 -- closemessage, release, end
  return sc:build(base)
end

function M.pollTalkResult()
  if emu:read16(SV.v8006) ~= SCRIPT_DONE then return end
  emu:write16(SV.v8006, 0)
  local q, answer, slot = emu:read16(SV.v8007), emu:read16(SV.v8005), emu:read16(SV.v8004)
  local peer = visuals and (visuals.peerForLocalId(emu:read16(SV.lastTalked)) or visuals.scriptPeer)
  if not peer or not S.peers[peer] then return end
  local slotOk = slot < partyCount()
  local mpc = _G.mp
  if q == Q_MENU then
    if answer == 0 and slotOk then mpc.trade(peer, slot + 1)
    elseif answer == 1 then mpc.battle(peer) end
  elseif q == Q_TRADE_OFFER then
    if answer == 1 and slotOk then
      if mpc.accept(slot + 1) == true and S.trade then S.trade.autoConfirm = true end
    else
      mpc.decline()
    end
  elseif q == Q_BATTLE_INVITE then
    if answer == 1 then mpc.fight() else mpc.decline() end
  elseif q == Q_TRADE_PREVIEW then
    if answer == 1 then mpc.confirm() else mpc.cancel() end
  elseif q == Q_WAITING and answer == 1 then
    if S.trade and S.trade.peer == peer then mpc.cancel() else mpc.decline() end
  end
end

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
  mp.visuals(true/false) show other players as characters on your map   mp.vis()  details
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

-- remote players as overworld characters
function mp.visuals(on)
  if not visuals then return say("visuals module not loaded") end
  on = on ~= false
  visuals.setEnabled(on)
  saveProfileSetting("visuals", on and "1" or "0")
  return say("visuals " .. (on and "on" or "off"))
end

function mp.vis()
  if not visuals then return say("visuals module not loaded") end
  local out = { visuals.status() }
  for k = 0, 6 do
    local id = visuals.slotPeer[k]
    local p = id and visuals.peers[id]
    if p then
      out[#out + 1] = string.format("  slot %d (localId 0x%02X): %s target %s,%s queue %d", k, 0x70 + k,
        peerName(id), tostring(p.tx), tostring(p.ty), #p.queue)
    end
  end
  local a = visuals.addr
  out[#out + 1] = string.format("  gSprites %08X  gMapHeader %08X  events %08X", a.gSprites, a.gMapHeader,
    emu:read32(a.gMapHeader + 4))
  return say(table.concat(out, "\n"))
end

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
  net.auto, net.backoff, net.retryAt = true, 1, clock()
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

-- No `return M`: mGBA 0.10 runs the script without popping its return values, which crashes
-- the emulator on the next callback. Use the global `mp` / _RRMP_INSTANCE instead.

