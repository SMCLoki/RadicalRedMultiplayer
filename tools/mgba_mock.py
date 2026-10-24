"""Fake mGBA 0.10 scripting environment for running client/rr_mp.lua outside the emulator.

Needs the `lupa` package (pip install lupa), which embeds Lua 5.4 (the version mGBA 0.10 uses).
Only the API surface rr_mp.lua touches is emulated: emu memory access, console + text buffers,
frame/shutdown callbacks and the socket API (socket.connect, :add("received"), :send, :receive,
:close). Socket semantics follow what Phase 0 observed on the real emulator: connect blocks,
"received" fires while data (or EOF) is pending, and after the peer closes :receive returns
nil, "disconnected" on every call. Loading mirrors mGBA's quirk of leaving script return values on
the Lua stack (fatal in the real emulator), so a script that returns anything is rejected. Everything here is a model of mGBA, not mGBA itself:
behaviour verified only in this mock must still be checked in the real emulator.
"""
from __future__ import annotations

import os
import select
import socket

import lupa.lua54 as lupa

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(REPO, "client", "rr_mp.lua")

PRELUDE = rb"""
local py = ...
emu = {}
function emu:read8(a) return py.read(a, 1) end
function emu:read16(a) return py.read(a, 2) end
function emu:read32(a) return py.read(a, 4) end
function emu:write8(a, v) py.write(a, 1, v) end
function emu:write16(a, v) py.write(a, 2, v) end
function emu:write32(a, v) py.write(a, 4, v) end
function emu:readRange(a, n) return py.read_range(a, n) end
function emu:getGameCode() return "AGB-BPRE" end
function emu:getGameTitle() return "POKEMON FIRE" end
function emu:currentFrame() return py.frame end
function emu:checksum(t) return py.checksum end  -- 0.10: type defaults to CRC32
C = { CHECKSUM = { CRC32 = 0 } }

console = {}
function console:log(s) py.log(s) end
function console:warn(s) py.log(s) end
function console:error(s) py.log(s) end
function console:createBuffer(name)
  local b = { text = "" }
  function b:setSize(c, r) end
  function b:clear() self.text = "" end
  function b:print(s) self.text = self.text .. s end
  __panel = b
  return b
end

callbacks = { list = {}, n = 0 }
function callbacks:add(ev, fn) self.n = self.n + 1; self.list[self.n] = { ev = ev, fn = fn }; return self.n end
function callbacks:remove(id) self.list[id] = nil end

__socks = {}
socket = {}
local Sock = {}
Sock.__index = Sock
function socket.connect(host, port)
  local h, err = py.connect(host, port)
  if not h then return nil, err end
  local s = setmetatable({ h = h, cbs = {} }, Sock)
  __socks[h] = s
  return s
end
function Sock:add(ev, fn) self.cbs[#self.cbs + 1] = { ev = ev, fn = fn }; return #self.cbs end
function Sock:send(d) return py.send(self.h, d) end
function Sock:receive(n) return py.recv(self.h, n) end
function Sock:close() py.close(self.h); __socks[self.h] = nil end

function __fire(ev)
  for id, c in pairs(callbacks.list) do if c.ev == ev then c.fn() end end
end
function __frame()
  for h, s in pairs(__socks) do
    if py.readable(h) then
      for _, c in ipairs(s.cbs) do if c.ev == "received" then c.fn(s) end end  -- mGBA passes the socket
    end
  end
  __fire("frame")
end
"""

REGIONS = {0x02: 0x40000, 0x03: 0x8000, 0x08: 0x1000}


class _Py:
    """Object handed to the Lua prelude; all values crossing the boundary are bytes/ints."""

    def __init__(self, gba: "FakeGBA"):
        self._g = gba
        self.frame = 0
        self.checksum = b"\x12\x34\x56\x78"

    def _loc(self, addr):
        region = addr >> 24
        size = REGIONS.get(region)
        if size is None:
            raise ValueError(f"unmapped address {addr:#x}")
        return self._g.mem[region], (addr & 0xFFFFFF) % size

    def read(self, addr, n):
        mem, off = self._loc(addr)
        return int.from_bytes(mem[off:off + n], "little")

    def write(self, addr, n, value):
        mem, off = self._loc(addr)
        mem[off:off + n] = (value & ((1 << (8 * n)) - 1)).to_bytes(n, "little")

    def read_range(self, addr, n):
        mem, off = self._loc(addr)
        return bytes(mem[off:off + n])

    def log(self, s):
        self._g.logs.append(s.decode("utf-8", "replace") if isinstance(s, bytes) else str(s))

    def connect(self, host, port):
        if self._g.network_down:
            return None, b"connection refused"
        try:
            s = socket.create_connection((host.decode(), int(port)), timeout=2)
        except OSError as e:
            return None, str(e).encode()
        s.setblocking(False)
        self._g._next += 1
        self._g.socks[self._g._next] = s
        return self._g._next

    def readable(self, h):
        s = self._g.socks.get(h)
        if s is None:
            return False
        try:
            r, _, _ = select.select([s], [], [], 0)
        except (OSError, ValueError):
            return True
        return bool(r)

    def send(self, h, data):
        s = self._g.socks.get(h)
        if s is None:
            return None, b"closed"
        try:
            return s.send(data)
        except BlockingIOError:
            return None, b"temporary failure"
        except OSError:
            return None, b"disconnected"

    def recv(self, h, n):
        s = self._g.socks.get(h)
        if s is None:
            return None, b"closed"
        try:
            data = s.recv(int(n))
        except BlockingIOError:
            return None, b"temporary failure"
        except OSError:
            return None, b"disconnected"
        if not data:
            return None, b"disconnected"
        return data

    def close(self, h):
        s = self._g.socks.pop(h, None)
        if s:
            s.close()


class FakeGBA:
    """One emulator instance running rr_mp.lua."""

    def __init__(self, work: str, profile: int | None = 1, config: dict | None = None, script: str = CLIENT):
        self.mem = {r: bytearray(size) for r, size in REGIONS.items()}
        self.logs: list[str] = []
        self.socks: dict[int, socket.socket] = {}
        self._next = 0
        self.network_down = False
        self.py = _Py(self)
        self.lua = lupa.LuaRuntime(encoding=None, unpack_returned_tuples=True)
        prelude = self.lua.eval(b"function(...) " + PRELUDE + b" end")
        prelude(self.py)
        g = self.lua.globals()
        g.RRMP_WORK = (work.replace("\\", "/") + "/").encode()
        if profile is not None:
            g.RRMP_PROFILE = profile
        if config:
            t = self.lua.table()
            for k, v in config.items():
                t[k.encode()] = str(v).encode()
            g.RRMP_CONFIG = t
        # mGBA 0.10 runs the file with lua_pcall(..., LUA_MULTRET) and never pops the results; anything a
        # script returns stays on the stack and aborts the emulator on the next callback. Fail loudly here.
        nret = self.lua.eval(b"function(p) local f = assert(loadfile(p)); return select('#', f()) end")(script.encode())
        if nret:
            raise RuntimeError(f"{script} returned {nret} value(s); real mGBA 0.10 crashes on this")
        self.M = g._RRMP_INSTANCE
        self.mp = g.mp

    # -- driving -----------------------------------------------------------

    def frame(self, n: int = 1):
        for _ in range(n):
            self.py.frame += 1
            self.lua.globals()[b"__frame"]()

    def kill(self):
        """Simulate the emulator being killed: sockets die, no shutdown callback runs."""
        for h in list(self.socks):
            self.py.close(h)
        self.lua.execute(b"__socks = {}; callbacks.list = {}")

    def shutdown(self):
        self.lua.globals()[b"__fire"](b"shutdown")

    # -- inspection helpers ------------------------------------------------

    def call(self, name: str, *args):
        fn = self.mp[name.encode()]
        conv = [a.encode() if isinstance(a, str) else a for a in args]
        return fn(*conv)

    @property
    def status(self) -> str:
        return self.M.S.status.decode()

    @property
    def my_id(self):
        return self.M.S.myId

    def peers(self) -> dict:
        out = {}
        for k, v in self.M.S.peers.items():
            out[k] = {"name": v.name.decode() if v.name else None, "state": v.state}
        return out

    def panel(self) -> str:
        p = self.lua.globals()[b"__panel"]
        return p.text.decode("utf-8", "replace") if p else ""

    def events(self) -> list[str]:
        return [e.decode("utf-8", "replace") for e in self.M.S.log.values()]

    # memory helpers for the verified Radical Red layout
    PARTY, COUNT, SB1PTR, SB1 = 0x02024284, 0x02024029, 0x03005008, 0x0202552C

    def set_party(self, mons_hex: list[str]):
        for i in range(6):
            raw = bytes.fromhex(mons_hex[i]) if i < len(mons_hex) else bytes(100)
            off = (self.PARTY & 0xFFFFFF) + i * 100
            self.mem[0x02][off:off + 100] = raw
        self.py.write(self.COUNT, 1, len(mons_hex))

    def party(self) -> list[str]:
        n = self.py.read(self.COUNT, 1)
        return [self.py.read_range(self.PARTY + i * 100, 100).hex() for i in range(n)]

    def set_position(self, group: int, num: int, x: int, y: int):
        self.py.write(self.SB1PTR, 4, self.SB1)
        self.py.write(self.SB1, 2, x)
        self.py.write(self.SB1 + 2, 2, y)
        self.py.write(self.SB1 + 4, 1, group)
        self.py.write(self.SB1 + 5, 1, num)

    def set_battle_flags(self, v: int):
        self.py.write(0x02022B4C, 4, v)
