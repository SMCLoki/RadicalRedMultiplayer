-- Test-harness autopilot: runs button/state commands from work/pilot.txt inside the emulator.
-- File format: first line "id <number>" (re-run when it changes), then one command per line:
--   HOLD <KEY> <frames>   WAIT <frames>   MASH <KEY> <frames>   SAVE <slot>   LOAD <slot>
-- KEY: A B START SELECT UP DOWN LEFT RIGHT L R.  Writes work/pilot_done.txt "<id>" when finished.
local function workDir()
  if RRMP_WORK then return (RRMP_WORK:gsub("\\", "/"):gsub("/?$", "/")) end
  local src = debug.getinfo(1, "S").source:gsub("^@", ""):gsub("\\", "/")
  return (src:match("^(.*)/[^/]*$") or ".") .. "/../work/"
end
local DIR = workDir()
local BIT = { A = 0, B = 1, SELECT = 2, START = 3, RIGHT = 4, LEFT = 5, UP = 6, DOWN = 7, R = 8, L = 9 }

local lastId, queue, cur, frame, doneId = nil, {}, nil, 0, nil

local function load()
  local f = io.open(DIR .. "pilot.txt", "r")
  if not f then return end
  local text = f:read("*a"); f:close()
  local id = text:match("^id%s+(%S+)")
  if not id or id == lastId then return end
  lastId, queue, cur = id, {}, nil
  for line in text:gmatch("[^\r\n]+") do
    local op, a, b = line:match("^(%u+)%s*(%S*)%s*(%S*)")
    if op and op ~= "ID" then queue[#queue + 1] = { op = op, a = a, b = b } end
  end
  console:log("pilot: loaded id " .. id .. " (" .. #queue .. " cmds)")
end

local function finish()
  local f = io.open(DIR .. "pilot_done.txt", "w"); if f then f:write(lastId); f:close() end
end

callbacks:add("frame", function()
  frame = frame + 1
  if frame % 20 == 0 and #queue == 0 and not cur then load() end
  if not cur then
    cur = table.remove(queue, 1)
    if cur then
      cur.t = 0
      if cur.op == "SAVE" then emu:saveStateSlot(tonumber(cur.a), 31); cur = nil
      elseif cur.op == "LOAD" then emu:loadStateSlot(tonumber(cur.a), 29); cur = nil end
    elseif lastId and #queue == 0 then
      if doneId ~= lastId then emu:setKeys(0); finish(); doneId = lastId end
    end
  end
  if cur then
    cur.t = cur.t + 1
    if cur.op == "HOLD" then emu:setKeys(1 << BIT[cur.a]); if cur.t >= tonumber(cur.b) then emu:setKeys(0); cur = nil end
    elseif cur.op == "MASH" then
      if (cur.t // 3) % 2 == 0 then emu:setKeys(1 << BIT[cur.a]) else emu:setKeys(0) end
      if cur.t >= tonumber(cur.b) then emu:setKeys(0); cur = nil end
    elseif cur.op == "WAIT" then if cur.t >= tonumber(cur.a) then cur = nil end
    else cur = nil end
  end
end)
console:log("pilot.lua loaded; watching " .. DIR .. "pilot.txt")
