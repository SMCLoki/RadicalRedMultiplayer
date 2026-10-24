-- RAM research helper for mGBA 0.10 (Phase 2/4 address hunting). Load next to rr_mp.lua or on its own.
-- Type commands into the scripting console; results also go to work/ramsearch.txt.
--
-- Classic narrowing search (EWRAM 0x02000000-0x0203FFFF + IWRAM 0x03000000-0x03007FFF):
--   rs.start(size)            -- size 1, 2 or 4 (aligned); every address becomes a candidate, snapshot taken
--   rs.filter("changed")      -- keep candidates whose value changed since the last snapshot
--   rs.filter("same")         -- ... stayed the same
--   rs.filter("eq", 25)       -- ... now equal 25        ("ne", "gt", "lt" also work; "inc"/"dec" vs last)
--   rs.list(20)               -- show first 20 candidates with old -> new values
-- Snapshots and diffs:
--   rs.snap("a") ... rs.snap("b"); rs.diff("a", "b", 50)    -- first 50 changed addresses
-- Watching (logged to work/ramsearch_watch.txt with frame number, battle flags and gMain.callback2):
--   rs.watch(0x02023D7C, 4, "chosenAction")   rs.unwatch()   rs.callbacks(true|false)
-- Quick looks:
--   rs.facing()   rs.hex(0x02036E38, 0x24)   rs.r32(addr)
--
-- Like rr_mp.lua, this file must not end with a return statement (mGBA 0.10 crashes on script return values).

local function workDir()
  if RRMP_WORK then return (RRMP_WORK:gsub("\\", "/"):gsub("/?$", "/")) end
  local src = debug.getinfo(1, "S").source:gsub("^@", ""):gsub("\\", "/")
  return (src:match("^(.*)/[^/]*$") or ".") .. "/../work/"
end
local WORK = workDir()
local OUT = WORK .. "ramsearch.txt"
local WATCH_OUT = WORK .. "ramsearch_watch.txt"

local REGIONS = { { base = 0x02000000, size = 0x40000 }, { base = 0x03000000, size = 0x8000 } }
local GMAIN, BATTLE_FLAGS, OBJ_EVENTS = 0x030030F0, 0x02022B4C, 0x02036E38
local FMT = { [1] = "<I1", [2] = "<I2", [4] = "<I4" }

if _RRMP_RS and _RRMP_RS.cb then pcall(callbacks.remove, callbacks, _RRMP_RS.cb) end
local rs = { snaps = {}, cand = nil, size = 1, last = nil, watches = {}, logCallbacks = false }
_RRMP_RS = rs
_G.rs = rs

local function out(lines)
  local text = type(lines) == "table" and table.concat(lines, "\n") or tostring(lines)
  if console and console.log then console:log(text) end
  local f = io.open(OUT, "ab")
  if f then f:write(text, "\n"); f:close() end
  return text
end

local function snapshot()
  local s = {}
  for i, r in ipairs(REGIONS) do s[i] = emu:readRange(r.base, r.size) end
  return s
end

local function valueAt(snap, addr, size)
  for i, r in ipairs(REGIONS) do
    if addr >= r.base and addr + size <= r.base + r.size then
      return (string.unpack(FMT[size], snap[i], addr - r.base + 1))
    end
  end
  return nil
end

function rs.start(size)
  size = tonumber(size) or 1
  if not FMT[size] then return out("size must be 1, 2 or 4") end
  rs.size, rs.last, rs.cand = size, snapshot(), {}
  for _, r in ipairs(REGIONS) do
    for a = r.base, r.base + r.size - size, size do rs.cand[#rs.cand + 1] = a end
  end
  return out(string.format("ramsearch: %d candidates (size %d)", #rs.cand, size))
end

local TESTS = {
  changed = function(o, n) return o ~= n end,
  same = function(o, n) return o == n end,
  inc = function(o, n) return n > o end,
  dec = function(o, n) return n < o end,
  eq = function(o, n, v) return n == v end,
  ne = function(o, n, v) return n ~= v end,
  gt = function(o, n, v) return n > v end,
  lt = function(o, n, v) return n < v end,
}

function rs.filter(kind, value)
  if not rs.cand then return out("run rs.start() first") end
  local test = TESTS[kind]
  if not test then return out("filter: changed, same, inc, dec, eq, ne, gt, lt") end
  local now, keep = snapshot(), {}
  for _, a in ipairs(rs.cand) do
    if test(valueAt(rs.last, a, rs.size), valueAt(now, a, rs.size), value) then keep[#keep + 1] = a end
  end
  rs.prev, rs.last, rs.cand = rs.last, now, keep
  return out(string.format("ramsearch: %s%s -> %d candidates", kind, value and (" " .. value) or "", #keep))
end

function rs.list(n)
  if not rs.cand then return out("run rs.start() first") end
  local L = {}
  for i = 1, math.min(tonumber(n) or 20, #rs.cand) do
    local a = rs.cand[i]
    local old = rs.prev and valueAt(rs.prev, a, rs.size)
    L[#L + 1] = string.format("  %08X  %s -> %d", a, old and tostring(old) or "?", valueAt(rs.last, a, rs.size))
  end
  L[#L + 1] = string.format("(%d candidates total)", #rs.cand)
  return out(L)
end

function rs.snap(name)
  rs.snaps[tostring(name)] = snapshot()
  return out("snapshot " .. tostring(name) .. " taken at frame " .. tostring(emu:currentFrame()))
end

function rs.diff(a, b, n)
  local sa, sb = rs.snaps[tostring(a)], rs.snaps[tostring(b)]
  if not sa or not sb then return out("unknown snapshot") end
  n = tonumber(n) or 50
  local L, count = { string.format("diff %s -> %s (bytes):", a, b) }, 0
  for i, r in ipairs(REGIONS) do
    local x, y = sa[i], sb[i]
    for off = 1, r.size do
      if x:byte(off) ~= y:byte(off) then
        count = count + 1
        if count <= n then
          L[#L + 1] = string.format("  %08X  %02X -> %02X", r.base + off - 1, x:byte(off), y:byte(off))
        end
      end
    end
  end
  L[#L + 1] = string.format("(%d bytes differ)", count)
  return out(L)
end

function rs.hex(addr, len)
  local raw = emu:readRange(addr, len or 16)
  return out(string.format("%08X: %s", addr, (raw:gsub(".", function(c) return string.format("%02X ", c:byte()) end))))
end

function rs.r32(addr) return out(string.format("%08X = %08X", addr, emu:read32(addr))) end

function rs.facing()
  local b = emu:read8(OBJ_EVENTS + 0x18)
  return out(string.format("objectEvents[0]+0x18 = %02X (low nibble %d, high nibble %d)", b, b & 0x0F, b >> 4))
end

local function wlog(s)
  local f = io.open(WATCH_OUT, "ab")
  if f then f:write(s, "\n"); f:close() end
end

function rs.watch(addr, size, label)
  size = tonumber(size) or 4
  if not FMT[size] then return out("size must be 1, 2 or 4") end
  local read = size == 1 and emu.read8 or size == 2 and emu.read16 or emu.read32
  rs.watches[#rs.watches + 1] = { addr = addr, read = read, label = label or string.format("%08X", addr),
                                  last = read(emu, addr) }
  return out(string.format("watching %08X (%d bytes) as %s -> %s", addr, size, label or "?", WATCH_OUT))
end

function rs.unwatch() rs.watches = {}; return out("watches cleared") end

function rs.callbacks(on)
  rs.logCallbacks = on ~= false
  rs.lastCb = nil
  return out("gMain callback logging " .. (rs.logCallbacks and "on" or "off") .. " -> " .. WATCH_OUT)
end

rs.cb = callbacks:add("frame", function()
  if #rs.watches == 0 and not rs.logCallbacks then return end
  local frame = emu:currentFrame()
  local flags = emu:read32(BATTLE_FLAGS)
  local cb2 = emu:read32(GMAIN + 4)
  if rs.logCallbacks then
    local cb1, saved = emu:read32(GMAIN), emu:read32(GMAIN + 8)
    local key = string.format("%08X %08X %08X", cb1, cb2, saved)
    if key ~= rs.lastCb then
      rs.lastCb = key
      wlog(string.format("f%d cb1=%08X cb2=%08X saved=%08X battleFlags=%08X", frame, cb1, cb2, saved, flags))
    end
  end
  for _, w in ipairs(rs.watches) do
    local v = w.read(emu, w.addr)
    if v ~= w.last then
      wlog(string.format("f%d %s %X -> %X (cb2=%08X battleFlags=%08X)", frame, w.label, w.last, v, cb2, flags))
      w.last = v
    end
  end
end)

out("ramsearch.lua loaded: rs.start(size), rs.filter(kind[, value]), rs.list(n), rs.snap/diff, rs.watch, rs.callbacks")
