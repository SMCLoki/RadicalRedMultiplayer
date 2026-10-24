-- Test-harness autopilot: runs button/state commands from work/pilot.txt inside the emulator.
-- File format: first line "id <number>" (re-run when it changes), then one command per line:
--   HOLD <KEY> <frames>   WAIT <frames>   MASH <KEY> <frames>   SAVE <slot>   LOAD <slot>
--   LOADSTATE <file under work/>   DUMP <name> (EWRAM+IWRAM -> work/dumps/<name>_ew.bin/_iw.bin)
--   EXEC <one line of Lua>   (result appended to work/<pilot>_exec.txt)
--   CBLOG on|off (log gMain callbacks, battle flags and candidate battle vars on change -> work/cb_log.txt)
-- Inputs after LOADSTATE run on exact frames, so two runs that differ in one input can be diffed byte-for-byte.
-- KEY: A B START SELECT UP DOWN LEFT RIGHT L R, or several joined by + (HOLD B+LEFT 64 = run left).  Writes work/pilot_done.txt "<id>" when finished.
-- Set the global PILOT_FILE (e.g. "pilot2.txt") in the console before loading to drive a second window.
local function workDir()
  if RRMP_WORK then return (RRMP_WORK:gsub("\\", "/"):gsub("/?$", "/")) end
  local src = debug.getinfo(1, "S").source:gsub("^@", ""):gsub("\\", "/")
  return (src:match("^(.*)/[^/]*$") or ".") .. "/../work/"
end
local DIR = workDir()
local BIT = { A = 0, B = 1, SELECT = 2, START = 3, RIGHT = 4, LEFT = 5, UP = 6, DOWN = 7, R = 8, L = 9 }

local lastId, queue, cur, frame, doneId = nil, {}, nil, 0, nil

local function keyMask(spec)
  local mask = 0
  for k in (spec or ""):gmatch("[^+]+") do
    if BIT[k] then mask = mask | (1 << BIT[k]) else console:log("pilot: unknown key " .. k) end
  end
  return mask
end

local function load()
  local f = io.open(DIR .. (PILOT_FILE or "pilot.txt"), "r")
  if not f then return end
  local text = f:read("*a"); f:close()
  local id = text:match("^id%s+(%S+)")
  if not id or id == lastId then return end
  lastId, queue, cur = id, {}, nil
  for line in text:gmatch("[^\r\n]+") do
    if line:match("^EXEC ") then queue[#queue + 1] = { op = "EXEC", code = line:sub(6), keys = 0 } goto continue end
    local op, a, b = line:match("^(%u+)%s*(%S*)%s*(%S*)")
    if op and op ~= "ID" then queue[#queue + 1] = { op = op, a = a, b = b, keys = (op == "HOLD" or op == "MASH") and keyMask(a) or 0 } end
    ::continue::
  end
  console:log("pilot: loaded id " .. id .. " (" .. #queue .. " cmds)")
end

local function dump(name)
  for _, r in ipairs({ { "ew", 0x02000000, 0x40000 }, { "iw", 0x03000000, 0x8000 } }) do
    local f = io.open(DIR .. "dumps/" .. name .. "_" .. r[1] .. ".bin", "wb")
    if f then f:write(emu:readRange(r[2], r[3])); f:close() else console:log("pilot: cannot write dumps/ (create work/dumps)") end
  end
end

-- gMain.callback1/2 and savedCallback (verified gMain 0x030030F0), battle flags, and vanilla-FR guesses for
-- gBattleMainFunc (0x03004F84) and gBattleOutcome (0x02023E8A), both verified on RR.
local CBLOG = { { 0x030030F0, 4 }, { 0x030030F4, 4 }, { 0x030030F8, 4 }, { 0x02022B4C, 4 }, { 0x03004F84, 4 }, { 0x02023E8A, 1 },
  -- verified on RR (docs/ram_map.md): gChosenActionByBattler[0..3], gChosenMoveByBattler[0..1], gBattleBufferB[0] and [1]
  { 0x02023D7C, 4 }, { 0x02023DC4, 4 }, { 0x020233C4, 4 }, { 0x020235C4, 4 },
  { 0x02023D74, 4 }, { 0x02023D50, 4 }, { 0x03005000, 4 } }  -- gBattlescriptCurrInstr, gBattleMoveDamage, gRngValue
local cblog, cbLast, cbFrame = false, nil, 0
local function cbTick()
  cbFrame = cbFrame + 1
  local vals = {}
  for i, v in ipairs(CBLOG) do
    vals[i] = string.format(v[2] == 4 and "%08X" or "%02X", v[2] == 4 and emu:read32(v[1]) or emu:read8(v[1]))
  end
  local line = table.concat(vals, " ")
  if line ~= cbLast then
    cbLast = line
    local f = io.open(DIR .. "cb_log.txt", "ab"); if f then f:write(cbFrame, " ", line, string.char(10)); f:close() end
  end
end

local function finish()
  local f = io.open(DIR .. (PILOT_FILE or "pilot.txt"):gsub("%.txt$", "") .. "_done.txt", "w"); if f then f:write(lastId); f:close() end
end

-- Reloading the script replaces the previous instance instead of running two autopilots.
if _PILOT_CB then pcall(callbacks.remove, callbacks, _PILOT_CB) end
_PILOT_CB = callbacks:add("frame", function()
  frame = frame + 1
  if cblog then cbTick() end
  if frame % 20 == 0 and #queue == 0 and not cur then load() end
  if not cur then
    cur = table.remove(queue, 1)
    if cur then
      cur.t = 0
      if cur.op == "SAVE" then emu:saveStateSlot(tonumber(cur.a), 31); cur = nil
      elseif cur.op == "LOAD" then emu:loadStateSlot(tonumber(cur.a), 29); cur = nil
      elseif cur.op == "LOADSTATE" then emu:loadStateFile(DIR .. cur.a, 29); emu:setKeys(0); cbFrame = 0; cur = nil
      elseif cur.op == "DUMP" then dump(cur.a); cur = nil
      elseif cur.op == "EXEC" then
        -- run one line of Lua (e.g. mp.battle("Player2")) without typing into the scripting console
        local fn, err = load(cur.code)
        local ok, res = false, err
        if fn then ok, res = pcall(fn) end
        local f = io.open(DIR .. (PILOT_FILE or "pilot.txt"):gsub("%.txt$", "") .. "_exec.txt", "ab")
        if f then f:write(tostring(ok), " ", tostring(res), string.char(10)); f:close() end
        cur = nil
      elseif cur.op == "CBLOG" then cblog, cbLast, cbFrame = (cur.a == "on"), nil, 0; cur = nil end
    elseif lastId and #queue == 0 then
      if doneId ~= lastId then emu:setKeys(0); finish(); doneId = lastId end
    end
  end
  if cur then
    cur.t = cur.t + 1
    if cur.op == "HOLD" then
      -- keys stay down for exactly N frames; released on frame N+1 (HOLD A 1 = a one-frame tap the game sees)
      if cur.t <= tonumber(cur.b) then emu:setKeys(cur.keys) else emu:setKeys(0); cur = nil end
    elseif cur.op == "MASH" then
      if (cur.t // 3) % 2 == 0 then emu:setKeys(cur.keys) else emu:setKeys(0) end
      if cur.t >= tonumber(cur.b) then emu:setKeys(0); cur = nil end
    elseif cur.op == "WAIT" then if cur.t >= tonumber(cur.a) then cur = nil end
    else cur = nil end
  end
end)
console:log("pilot.lua loaded; watching " .. DIR .. (PILOT_FILE or "pilot.txt"))
