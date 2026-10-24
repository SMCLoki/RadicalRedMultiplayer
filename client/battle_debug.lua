-- Battle desync debugger: logs battle state on every gBattleMainFunc change to work/bdbg_<tag>.txt.
-- Load in each window (after setting PILOT_FILE in window 2, used as the tag). Compare the two files per round.
-- Must not end with a return statement (mGBA 0.10).
local src = debug.getinfo(1, "S").source:gsub("^@", ""):gsub("\\", "/")
local WORK = (src:match("^(.*)/[^/]*$") or ".") .. "/../work/"
local TAG = (PILOT_FILE or "pilot.txt"):gsub("%.txt$", "")
local OUT = WORK .. "bdbg_" .. TAG .. ".txt"
local MONS, BMON = 0x02023BE4, 0x58
local last, n = nil, 0
if _BDBG_CB then pcall(callbacks.remove, callbacks, _BDBG_CB) end
_BDBG_CB = callbacks:add("frame", function()
  n = n + 1
  if emu:read32(0x02022B4C) == 0 then last = nil; return end
  local f = emu:read32(0x03004F84)
  if f == last then return end
  last = f
  local line = string.format("%d func=%08X rng=%08X b0[sp=%d hp=%d spe=%d] b1[sp=%d hp=%d spe=%d] act=%08X mv=%08X cur=%04X bufB0=%08X bufB1=%08X",
    n, f, emu:read32(0x03005000),
    emu:read16(MONS), emu:read16(MONS + 0x28), emu:read16(MONS + 6),
    emu:read16(MONS + BMON), emu:read16(MONS + BMON + 0x28), emu:read16(MONS + BMON + 6),
    emu:read32(0x02023D7C), emu:read32(0x02023DC4), emu:read16(0x02023D4A), emu:read32(0x020233C4), emu:read32(0x020235C4))
  local fh = io.open(OUT, "ab"); if fh then fh:write(line, string.char(10)); fh:close() end
end)
console:log("battle_debug.lua: logging to " .. OUT)
