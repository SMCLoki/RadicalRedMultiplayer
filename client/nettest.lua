-- Phase 0/1 check: can the mGBA Lua socket API reach a local TCP server? Result in work/nettest_out.txt
local function workDir()
  if RRMP_WORK then return (RRMP_WORK:gsub("\\", "/"):gsub("/?$", "/")) end
  local src = debug.getinfo(1, "S").source:gsub("^@", ""):gsub("\\", "/")
  return (src:match("^(.*)/[^/]*$") or ".") .. "/../work/"
end
local OUT = workDir() .. "nettest_out.txt"
local log = {}
local function L(s) log[#log + 1] = s; local f = io.open(OUT, "w"); if f then f:write(table.concat(log, "\n"), "\n"); f:close() end end
L("socket keys:")
for k, v in pairs(socket) do L("  " .. tostring(k) .. " = " .. type(v)) end
local ok, s = pcall(socket.connect, "127.0.0.1", 8765)
L("connect pcall ok=" .. tostring(ok) .. " sock=" .. tostring(s) .. " type=" .. type(s))
if ok and s then
  local ok2, e2 = pcall(function() return s:send("hello from mgba\n") end)
  L("send ok=" .. tostring(ok2) .. " " .. tostring(e2))
  s:add("received", function()
    local d, err = s:receive(1024)
    L("received: " .. tostring(d) .. " err=" .. tostring(err))
  end)
  s:add("error", function(e) L("socket error cb: " .. tostring(e)) end)
end
