-- Fine-grained PvP battle trace: one line per battle-script step (gBattleMainFunc / gBattlescriptCurrInstr
-- change) plus every lag frame, and a dump of both active Pokemon's battle data (gBattleMons) at each turn start,
-- to work/btrace_<tag>.txt. crit = gCritMultiplier (CFRU: 10 = normal hit, 15 = critical). Compare two windows with tools/compare_traces.py.
-- Load in each window after rr_mp.lua (set PILOT_FILE in window 2 first; it is used as the tag).
-- Must not end with a return statement (mGBA 0.10).
local function workDir()
  if RRMP_WORK then return (RRMP_WORK:gsub("\\", "/"):gsub("/?$", "/")) end
  local src = debug.getinfo(1, "S").source:gsub("^@", ""):gsub("\\", "/")
  return (src:match("^(.*)/[^/]*$") or ".") .. "/../work/"
end
local WORK = workDir()
local TAG = (PILOT_FILE or "pilot.txt"):gsub("%.txt$", "")
local OUT = WORK .. "btrace_" .. TAG .. ".txt"
local MONS, BMON = 0x02023BE4, 0x58
local lastKey, lastPt, lastMain, n = nil, nil, nil, 0
if _BTRACE_CB then pcall(callbacks.remove, callbacks, _BTRACE_CB) end
_BTRACE_CB = callbacks:add("frame", function()
  n = n + 1
  if emu:read32(0x02022B4C) == 0 then lastKey = nil; return end
  local pt = emu:read32(emu:read32(0x0300500C) + 0x10)
  local lag = pt == lastPt
  lastPt = pt
  local main, instr, exec = emu:read32(0x03004F84), emu:read32(0x02023D74), emu:read32(0x02023BC8)
  if main == 0x08014041 and lastMain ~= 0x08014041 then
    -- turn start: full battle data of both active Pokemon (compare_traces.py diffs them, mirrored)
    local hex = function(a) return (emu:readRange(a, BMON):gsub(".", function(ch) return string.format("%02X", ch:byte()) end)) end
    local fh = io.open(OUT, "ab")
    if fh then fh:write(string.format("%d MONS b0=%s b1=%s", n, hex(MONS), hex(MONS + BMON)), string.char(10)); fh:close() end
  end
  lastMain = main
  local key = main .. ":" .. instr
  if key == lastKey and not lag then return end
  lastKey = key
  local line = string.format("%d %s main=%08X instr=%08X exec=%08X rng=%08X dmg=%d crit=%d atk=%d tgt=%d hp0=%d hp1=%d f1=%08X r2=%08X",
    n, lag and "LAG" or "ok", main, instr, exec, emu:read32(0x03005000), emu:read32(0x02023D50),
    emu:read8(0x02023D71), emu:read8(0x02023D6B), emu:read8(0x02023D6C),
    emu:read16(MONS + 0x28), emu:read16(MONS + BMON + 0x28), emu:read32(0x03004FE4), emu:read32(0x020386D0))
  local fh = io.open(OUT, "ab"); if fh then fh:write(line, string.char(10)); fh:close() end
end)
console:log("battle_trace.lua: logging to " .. OUT)
