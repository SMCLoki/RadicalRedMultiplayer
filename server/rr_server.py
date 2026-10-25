#!/usr/bin/env python3
"""Radical Red Online relay server.

Stdlib only (Python 3.10+). Newline-delimited JSON over plain TCP, see docs/protocol.md.
Rooms hold up to 8 players. Besides relaying presence/chat, the server coordinates
trades (two-phase commit with a durable ledger) and lockstep PvP battles.

    python server/rr_server.py                 # localhost:8765
    python server/rr_server.py --host 0.0.0.0  # LAN / internet (open the port yourself)

With --host other than loopback the server also answers LAN discovery broadcasts on UDP 8766
(see docs/protocol.md), so players on the same network can find it without typing an address.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import secrets
import socket
import struct
import time
from dataclasses import dataclass, field

PROTOCOL_VERSION = 1
MAX_LINE = 8192
LAN_PORT = 8766
LAN_MAGIC = b"RRMP-DISCOVER"
LAN_PROBE_SIZE = 256   # probes are padded; the reply is never larger, so the beacon can't amplify traffic
MAX_ROOM_HARD = 8
ROOM_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
UID_RE = re.compile(r"^[A-Za-z0-9]{8,32}$")
GAME_RE = re.compile(r"^[\x21-\x7e]{1,64}$")
MAX_SPECIES = 2047
MAX_MOVE = 2047
MON_HEX_LEN = 200  # 100-byte party struct

log = logging.getLogger("rr_server")


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8765
    max_room: int = 8
    hello_timeout: float = 10.0
    idle_timeout: float = 10.0
    rate: float = 60.0          # sustained messages/second per client
    burst: float = 120.0        # bucket size
    trade_timeout: float = 120.0
    turn_timeout: float = 120.0
    battle_grace: float = 30.0  # how long a disconnected battler may take to come back
    data_dir: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    tick: float = 0.25
    max_write_buffer: int = 256 * 1024
    max_conns: int = 64         # open sockets in total (a hosted server is reachable from the internet)
    max_per_ip: int = 12        # open sockets per remote address
    exempt_loopback: bool = True  # this PC's own game windows don't count towards max_per_ip
    lan_port: int | None = None   # UDP port for LAN discovery replies (None = off)


def _lan_ip() -> str | None:
    """This PC's address on the home network (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.0.1", 9))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


class LanBeacon(asyncio.DatagramProtocol):
    """Answers LAN discovery probes with the TCP port and player count (no room codes)."""

    def __init__(self, server: "Server"):
        self.server = server
        self.transport = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        if not data.startswith(LAN_MAGIC) or len(data) < LAN_PROBE_SIZE:
            return
        info = {"rrmp": PROTOCOL_VERSION, "port": self.server.cfg.port,
                "players": sum(1 for c in self.server.conns if c.ready),
                "max_room": self.server.cfg.max_room, "name": self.server.host_name}
        reply = json.dumps(info, separators=(",", ":")).encode()
        if len(reply) <= len(data):
            self.transport.sendto(reply, addr)


class ProtocolError(Exception):
    def __init__(self, code: str, msg: str = "", fatal: bool = False):
        super().__init__(msg or code)
        self.code, self.msg, self.fatal = code, msg or code, fatal


# ---------------------------------------------------------------------------
# Pokémon validation (Radical Red party structs are unencrypted, see docs/ram_map.md)

@dataclass
class Mon:
    hex: str
    pid: int
    otid: int
    species: int
    level: int

    @property
    def fp(self) -> str:
        return f"{self.pid:08x}{self.otid:08x}"

    def summary(self) -> dict:
        return {"fp": self.fp, "species": self.species, "level": self.level}


def parse_mon(value) -> Mon:
    if not isinstance(value, str) or len(value) != MON_HEX_LEN:
        raise ProtocolError("bad_mon", "mon must be 200 hex chars")
    try:
        raw = bytes.fromhex(value)
    except ValueError:
        raise ProtocolError("bad_mon", "mon is not hex") from None
    pid, otid = struct.unpack_from("<II", raw, 0)
    (checksum,) = struct.unpack_from("<H", raw, 0x1C)
    (species,) = struct.unpack_from("<H", raw, 0x20)
    moves = struct.unpack_from("<4H", raw, 0x2C)
    level = raw[0x54]
    hp, maxhp = struct.unpack_from("<HH", raw, 0x56)
    if pid == 0 and otid == 0:
        raise ProtocolError("bad_mon", "empty slot")
    if checksum != 0:
        raise ProtocolError("bad_mon", "unexpected checksum (not a Radical Red party struct)")
    if not 1 <= species <= MAX_SPECIES:
        raise ProtocolError("bad_mon", "species out of range")
    if not 1 <= level <= 100:
        raise ProtocolError("bad_mon", "level out of range")
    if maxhp == 0 or hp > maxhp:
        raise ProtocolError("bad_mon", "bad hp")
    if not any(moves) or any(m > MAX_MOVE for m in moves):
        raise ProtocolError("bad_mon", "bad moves")
    return Mon(value.lower(), pid, otid, species, level)


# ---------------------------------------------------------------------------

class Ledger:
    """Append-only JSON-lines file, fsynced, so committed trades survive a server crash."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def append(self, rec: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def read(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        log.warning("ledger: skipping corrupt line in %s", self.path)


@dataclass
class Trade:
    txn: str
    room: str
    a: "Conn"
    b: "Conn"
    a_uid: str
    b_uid: str
    a_slot: int
    a_mon: Mon
    b_slot: int | None = None
    b_mon: Mon | None = None
    confirmed: set = field(default_factory=set)
    deadline: float = 0.0


@dataclass
class Battle:
    bid: str
    room: str
    uids: list               # [challenger, challenged]; index = side
    names: list
    rules: dict
    state: str = "invited"   # invited -> teams -> active -> over
    teams: list = field(default_factory=lambda: [None, None])
    seed: int = 0
    turn: int = 0
    actions: dict = field(default_factory=dict)   # side -> (action, hash)
    last_turn_msg: dict | None = None
    ends: dict = field(default_factory=dict)      # side -> winner side reported
    deadline: float = 0.0
    away: dict = field(default_factory=dict)      # side -> time it went away


class Conn:
    _next_id = 1

    def __init__(self, server: "Server", reader, writer):
        self.server, self.reader, self.writer = server, reader, writer
        self.id = 0
        self.uid = ""
        self.name = ""
        self.room = ""
        self.state = None
        self.ready = False
        self.closed = False
        self.gone = False
        self.leave_reason = "quit"
        now = time.monotonic()
        self.connected_at = now
        self.last_rx = now
        self.tokens = server.cfg.burst
        self.refill_at = now
        peer = writer.get_extra_info("peername")
        self.ip = peer[0] if peer else "?"
        self.peer = f"{peer[0]}:{peer[1]}" if peer else "?"

    def send(self, msg: dict) -> None:
        if self.closed:
            return
        try:
            self.writer.write((json.dumps(msg, separators=(",", ":")) + "\n").encode())
            if self.writer.transport.get_write_buffer_size() > self.server.cfg.max_write_buffer:
                self.close("error")
        except (ConnectionError, RuntimeError):
            self.close("error")

    def close(self, reason: str | None = None) -> None:
        if reason and self.leave_reason == "quit":
            self.leave_reason = reason
        if not self.closed:
            self.closed = True
            try:
                self.writer.close()
            except Exception:
                pass

    def take_token(self) -> bool:
        now = time.monotonic()
        cfg = self.server.cfg
        self.tokens = min(cfg.burst, self.tokens + (now - self.refill_at) * cfg.rate)
        self.refill_at = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True

    def info(self) -> dict:
        d = {"id": self.id, "name": self.name}
        if self.state is not None:
            d["state"] = self.state
        return d


class Room:
    def __init__(self, name: str, game: str):
        self.name, self.game = name, game
        self.members: dict[int, Conn] = {}

    def broadcast(self, msg: dict, exclude: Conn | None = None) -> None:
        for c in list(self.members.values()):
            if c is not exclude:
                c.send(msg)


def clean_name(name) -> str:
    if not isinstance(name, str):
        return ""
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    return name[:12]


def clean_text(text, limit: int) -> str:
    if not isinstance(text, str):
        return ""
    return "".join(ch for ch in text if ch.isprintable())[:limit].strip()


class Server:
    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or Config()
        self.cfg.max_room = max(1, min(self.cfg.max_room, MAX_ROOM_HARD))
        self.rooms: dict[str, Room] = {}
        self.by_uid: dict[str, Conn] = {}
        self.trades: dict[str, Trade] = {}
        self.battles: dict[str, Battle] = {}
        self.pending_commits: dict[str, dict[str, dict]] = {}  # uid -> txn -> trade_commit msg
        self.trade_ledger = Ledger(os.path.join(self.cfg.data_dir, "trades.jsonl"))
        self.battle_log = Ledger(os.path.join(self.cfg.data_dir, "battles.jsonl"))
        self._server: asyncio.base_events.Server | None = None
        self._janitor: asyncio.Task | None = None
        self.conns: set[Conn] = set()
        self._beacon = None
        self.host_name = re.sub(r"[^A-Za-z0-9_ -]", "", socket.gethostname())[:32]
        self._load_ledger()

    # -- lifecycle ---------------------------------------------------------

    def _load_ledger(self) -> None:
        for rec in self.trade_ledger.read():
            if rec.get("e") == "commit":
                for uid, msg in rec["msgs"].items():
                    self.pending_commits.setdefault(uid, {})[rec["txn"]] = msg
            elif rec.get("e") == "ack":
                self.pending_commits.get(rec["uid"], {}).pop(rec["txn"], None)
        n = sum(len(v) for v in self.pending_commits.values())
        if n:
            log.info("ledger: %d trade commits awaiting client acknowledgement", n)

    async def start(self) -> int:
        self._server = await asyncio.start_server(
            self._handle, self.cfg.host, self.cfg.port, limit=MAX_LINE + 1)
        self.cfg.port = self._server.sockets[0].getsockname()[1]
        self._janitor = asyncio.create_task(self._janitor_loop())
        log.info("listening on %s:%d (protocol v%d, max %d per room)",
                 self.cfg.host, self.cfg.port, PROTOCOL_VERSION, self.cfg.max_room)
        if self.cfg.host in ("0.0.0.0", "", "::"):
            ip = _lan_ip()
            if ip:
                log.info("players on your network connect to %s port %d", ip, self.cfg.port)
        if self.cfg.lan_port is not None:
            try:
                self._beacon, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                    lambda: LanBeacon(self), local_addr=(self.cfg.host, self.cfg.lan_port))
                self.cfg.lan_port = self._beacon.get_extra_info("sockname")[1]
                log.info("answering LAN discovery on UDP %d", self.cfg.lan_port)
            except OSError as e:   # e.g. a second server on this PC; discovery is optional
                log.warning("LAN discovery off (UDP %s: %s)", self.cfg.lan_port, e)
                self._beacon = None
        return self.cfg.port

    async def stop(self) -> None:
        if self._janitor:
            self._janitor.cancel()
        if self._beacon:
            self._beacon.close()
            self._beacon = None
        if self._server:
            self._server.close()
        for c in list(self.conns):
            c.close("quit")
        if self._server:
            await self._server.wait_closed()

    async def serve_forever(self) -> None:
        await self.start()
        async with self._server:
            await self._server.serve_forever()

    # -- connection handling ----------------------------------------------

    async def _handle(self, reader, writer) -> None:
        conn = Conn(self, reader, writer)
        refusal = self._over_limit(conn)
        if refusal:
            log.warning("refused %s: %s", conn.peer, refusal)
            self._fatal(conn, "server_busy", refusal)
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return
        self.conns.add(conn)
        try:
            while not conn.closed:
                try:
                    line = await reader.readuntil(b"\n")
                except (asyncio.LimitOverrunError, ValueError):
                    self._fatal(conn, "too_long", f"line exceeds {MAX_LINE} bytes")
                    break
                except (asyncio.IncompleteReadError, ConnectionError):
                    break
                conn.last_rx = time.monotonic()
                if not conn.take_token():
                    self._fatal(conn, "rate_limited", "too many messages")
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    if not isinstance(msg, dict) or not isinstance(msg.get("t"), str):
                        raise ProtocolError("bad_msg", "expected a JSON object with 't'")
                    self._dispatch(conn, msg)
                except json.JSONDecodeError:
                    self._fatal(conn, "bad_msg", "invalid JSON")
                except ProtocolError as e:
                    if e.fatal or not conn.ready:
                        self._fatal(conn, e.code, e.msg)
                    else:
                        conn.send({"t": "error", "code": e.code, "msg": e.msg, "fatal": False,
                                   "re": msg.get("t") if isinstance(msg, dict) else None})
        except Exception:
            log.exception("connection %s crashed", conn.peer)
            conn.leave_reason = "error"
        finally:
            conn.close()
            self.conns.discard(conn)
            self._on_disconnect(conn)
            try:
                await writer.wait_closed()
            except Exception:
                pass

    def _over_limit(self, conn: Conn) -> str | None:
        if len(self.conns) >= self.cfg.max_conns:
            return f"server has {self.cfg.max_conns} connections already"
        if self.cfg.exempt_loopback and (conn.ip.startswith(("127.", "::ffff:127.")) or conn.ip == "::1"):
            return None
        if sum(1 for c in self.conns if c.ip == conn.ip) >= self.cfg.max_per_ip:
            return f"too many connections from {conn.ip}"
        return None

    def _fatal(self, conn: Conn, code: str, msg: str) -> None:
        conn.send({"t": "error", "code": code, "msg": msg, "fatal": True})
        conn.close("timeout" if code == "timeout" else "error")

    def _on_disconnect(self, conn: Conn) -> None:
        if not conn.ready or conn.gone:
            return
        conn.gone = True
        room = self.rooms.get(conn.room)
        if room and room.members.get(conn.id) is conn:
            del room.members[conn.id]
            room.broadcast({"t": "leave", "id": conn.id, "reason": conn.leave_reason})
            if not room.members:
                del self.rooms[conn.room]
        if self.by_uid.get(conn.uid) is conn:
            del self.by_uid[conn.uid]
        for tr in list(self.trades.values()):
            if tr.a is conn or tr.b is conn:
                self._abort_trade(tr, "disconnect")
        now = time.monotonic()
        for b in list(self.battles.values()):
            if b.state != "over" and conn.uid in b.uids:
                side = b.uids.index(conn.uid)
                if b.state in ("invited", "teams"):
                    self._end_battle(b, None, "cancelled")
                else:
                    b.away[side] = now
                    other = self._battle_conn(b, 1 - side)
                    if other:
                        other.send({"t": "battle_wait", "bid": b.bid, "reason": "disconnected",
                                    "grace": self.cfg.battle_grace})
        log.info("%s #%d %r left room %s (%s)", conn.peer, conn.id, conn.name, conn.room, conn.leave_reason)

    async def _janitor_loop(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.tick)
            try:
                self._janitor_tick()
            except Exception:
                log.exception("janitor failed")

    def _janitor_tick(self) -> None:
        now = time.monotonic()
        for c in list(self.conns):
            if not c.ready and now - c.connected_at > self.cfg.hello_timeout:
                self._fatal(c, "timeout", "no hello")
            elif c.ready and now - c.last_rx > self.cfg.idle_timeout:
                self._fatal(c, "timeout", "idle")
        for tr in list(self.trades.values()):
            if now > tr.deadline:
                self._abort_trade(tr, "timeout")
        for b in list(self.battles.values()):
            if b.state == "over":
                continue
            for side, since in list(b.away.items()):
                if now - since > self.cfg.battle_grace:
                    self._end_battle(b, 1 - side, "disconnect")
                    break
            if b.state == "over":
                continue
            if now > b.deadline:
                if b.state == "active":
                    missing = [s for s in (0, 1) if s not in b.actions]
                    winner = (1 - missing[0]) if len(missing) == 1 else None
                    self._end_battle(b, winner, "turn_timeout")
                else:
                    self._end_battle(b, None, "timeout")

    # -- dispatch ----------------------------------------------------------

    def _dispatch(self, conn: Conn, msg: dict) -> None:
        t = msg["t"]
        if not conn.ready:
            if t != "hello":
                raise ProtocolError("bad_hello", "first message must be hello", fatal=True)
            self._on_hello(conn, msg)
            return
        handler = getattr(self, "_on_" + t, None) if t in self.CLIENT_TYPES else None
        if handler is None:
            raise ProtocolError("bad_type", f"unknown message type {t!r}")
        handler(conn, msg)

    CLIENT_TYPES = {
        "ping", "state", "chat", "direct", "bye",
        "trade_offer", "trade_answer", "trade_confirm", "trade_cancel", "trade_done",
        "battle_req", "battle_accept", "battle_decline", "battle_team", "battle_action",
        "battle_end", "battle_forfeit",
    }

    def _on_hello(self, conn: Conn, msg: dict) -> None:
        if msg.get("v") != PROTOCOL_VERSION:
            raise ProtocolError("bad_version", f"server speaks protocol v{PROTOCOL_VERSION}", fatal=True)
        room_name = msg.get("room")
        game = msg.get("game")
        uid = msg.get("uid") or secrets.token_hex(8)
        name = clean_name(msg.get("name"))
        if not isinstance(room_name, str) or not ROOM_RE.match(room_name):
            raise ProtocolError("bad_hello", "room must be 1-16 chars A-Z a-z 0-9 _ -", fatal=True)
        if not isinstance(game, str) or not GAME_RE.match(game):
            raise ProtocolError("bad_hello", "missing game id", fatal=True)
        if not isinstance(uid, str) or not UID_RE.match(uid):
            raise ProtocolError("bad_hello", "uid must be 8-32 alphanumerics", fatal=True)
        if not name:
            raise ProtocolError("bad_hello", "name must be 1-12 printable chars", fatal=True)
        room = self.rooms.get(room_name)
        old = self.by_uid.get(uid)
        if old is not None:
            # Same player reconnecting before their old socket timed out: the new one wins.
            self._fatal(old, "replaced", "connected from elsewhere")
            old.leave_reason = "replaced"
            self.conns.discard(old)
            self._on_disconnect(old)
            room = self.rooms.get(room_name)
        if room is not None:
            if room.game != game:
                raise ProtocolError("game_mismatch", "room is playing a different ROM", fatal=True)
            if len(room.members) >= self.cfg.max_room:
                raise ProtocolError("room_full", f"room holds {self.cfg.max_room} players", fatal=True)
        else:
            room = self.rooms[room_name] = Room(room_name, game)
        conn.id = Conn._next_id
        Conn._next_id += 1
        conn.uid, conn.name, conn.room, conn.ready = uid, name, room_name, True
        peers = [c.info() for c in room.members.values()]
        room.members[conn.id] = conn
        self.by_uid[uid] = conn
        conn.send({"t": "welcome", "v": PROTOCOL_VERSION, "id": conn.id, "uid": uid, "room": room_name,
                   "max": self.cfg.max_room, "peers": peers})
        room.broadcast({"t": "join", "id": conn.id, "name": name}, exclude=conn)
        log.info("%s #%d %r joined room %s (%d/%d)", conn.peer, conn.id, name, room_name,
                 len(room.members), self.cfg.max_room)
        for m in self.pending_commits.get(uid, {}).values():
            conn.send(m)
        self._resume_battles(conn)

    def _on_ping(self, conn: Conn, msg: dict) -> None:
        conn.send({"t": "pong", "n": msg.get("n")})

    def _on_bye(self, conn: Conn, msg: dict) -> None:
        conn.close("quit")

    def _relay_payload(self, msg: dict) -> dict:
        raw = json.dumps(msg, separators=(",", ":"))
        if len(raw) > 1024:
            raise ProtocolError("too_long", "relayed payload exceeds 1024 bytes")
        return msg

    def _on_state(self, conn: Conn, msg: dict) -> None:
        msg = self._relay_payload(msg)
        state = {k: v for k, v in msg.items() if k not in ("t", "from")}
        conn.state = state
        self.rooms[conn.room].broadcast({"t": "state", "from": conn.id, **state}, exclude=conn)

    def _on_chat(self, conn: Conn, msg: dict) -> None:
        text = clean_text(msg.get("text"), 120)
        if not text:
            raise ProtocolError("bad_msg", "empty chat")
        self.rooms[conn.room].broadcast({"t": "chat", "from": conn.id, "name": conn.name, "text": text})

    def _peer(self, conn: Conn, target) -> Conn:
        room = self.rooms[conn.room]
        other = room.members.get(target) if isinstance(target, int) else None
        if other is None or other is conn:
            raise ProtocolError("no_peer", "no such player in your room")
        return other

    def _on_direct(self, conn: Conn, msg: dict) -> None:
        other = self._peer(conn, msg.get("to"))
        msg = self._relay_payload(msg)
        other.send({**msg, "from": conn.id})

    # -- trading -----------------------------------------------------------

    def _trade_of(self, conn: Conn) -> Trade | None:
        for tr in self.trades.values():
            if tr.a is conn or tr.b is conn:
                return tr
        return None

    def _in_battle(self, conn: Conn) -> bool:
        return any(b.state != "over" and conn.uid in b.uids for b in self.battles.values())

    def _on_trade_offer(self, conn: Conn, msg: dict) -> None:
        other = self._peer(conn, msg.get("to"))
        slot = msg.get("slot")
        if not isinstance(slot, int) or not 0 <= slot <= 5:
            raise ProtocolError("bad_msg", "slot must be 0-5")
        mon = parse_mon(msg.get("mon"))
        crossed = self._trade_of(other)
        if (crossed is not None and crossed.a is other and crossed.b is conn and crossed.b_mon is None
                and self._trade_of(conn) is crossed):
            # Both players offered to each other before either saw the other's offer (seen with ~300 ms of lag in
            # real mGBA run 12). Treat this offer as the answer to theirs; picking the Pokémon is this player's
            # consent, so they count as confirmed and only the first offerer still has to confirm.
            if mon.fp == crossed.a_mon.fp:
                raise ProtocolError("bad_mon", "cannot trade a Pokémon for itself")
            crossed.b_slot, crossed.b_mon = slot, mon
            crossed.deadline = time.monotonic() + self.cfg.trade_timeout
            crossed.a.send({"t": "trade_preview", "txn": crossed.txn, "mon": mon.hex, "summary": mon.summary()})
            conn.send({"t": "trade_offered", "txn": crossed.txn, "from": other.id, "name": other.name,
                       "mon": crossed.a_mon.hex, "summary": crossed.a_mon.summary()})
            conn.send({"t": "trade_preview", "txn": crossed.txn, "mon": crossed.a_mon.hex,
                       "summary": crossed.a_mon.summary()})
            crossed.confirmed.add(conn.uid)
            other.send({"t": "trade_confirmed", "txn": crossed.txn, "by": conn.id})
            return
        if self._trade_of(conn) or self._trade_of(other):
            raise ProtocolError("busy", "you or that player already have a trade open")
        if self._in_battle(conn) or self._in_battle(other):
            raise ProtocolError("busy", "you or that player are in a battle")
        if self.pending_commits.get(conn.uid) or self.pending_commits.get(other.uid):
            raise ProtocolError("busy", "a previous trade is still being applied")
        tr = Trade(txn=secrets.token_hex(8), room=conn.room, a=conn, b=other, a_uid=conn.uid,
                   b_uid=other.uid, a_slot=slot, a_mon=mon,
                   deadline=time.monotonic() + self.cfg.trade_timeout)
        self.trades[tr.txn] = tr
        conn.send({"t": "trade_pending", "txn": tr.txn, "to": other.id})
        other.send({"t": "trade_offered", "txn": tr.txn, "from": conn.id, "name": conn.name,
                    "mon": mon.hex, "summary": mon.summary()})

    def _get_trade(self, conn: Conn, msg: dict) -> Trade:
        tr = self.trades.get(msg.get("txn"))
        if tr is None or (tr.a is not conn and tr.b is not conn):
            raise ProtocolError("no_trade", "no such open trade")
        return tr

    def _on_trade_answer(self, conn: Conn, msg: dict) -> None:
        tr = self._get_trade(conn, msg)
        if tr.b is not conn or tr.b_mon is not None:
            raise ProtocolError("bad_state", "you cannot answer this trade")
        slot = msg.get("slot")
        if not isinstance(slot, int) or not 0 <= slot <= 5:
            raise ProtocolError("bad_msg", "slot must be 0-5")
        mon = parse_mon(msg.get("mon"))
        if mon.fp == tr.a_mon.fp:
            raise ProtocolError("bad_mon", "cannot trade a Pokémon for itself")
        tr.b_slot, tr.b_mon = slot, mon
        tr.deadline = time.monotonic() + self.cfg.trade_timeout
        tr.a.send({"t": "trade_preview", "txn": tr.txn, "mon": mon.hex, "summary": mon.summary()})
        tr.b.send({"t": "trade_preview", "txn": tr.txn, "mon": tr.a_mon.hex, "summary": tr.a_mon.summary()})

    def _on_trade_confirm(self, conn: Conn, msg: dict) -> None:
        tr = self._get_trade(conn, msg)
        if tr.b_mon is None:
            raise ProtocolError("bad_state", "both sides must pick a Pokémon first")
        tr.confirmed.add(conn.uid)
        other = tr.b if conn is tr.a else tr.a
        other.send({"t": "trade_confirmed", "txn": tr.txn, "by": conn.id})
        if len(tr.confirmed) == 2:
            self._commit_trade(tr)

    def _commit_trade(self, tr: Trade) -> None:
        # Phase 2: the decision is durable before anyone is told, so a crash on either side
        # (or of the server) can only delay a trade, never half-apply it.
        msgs = {
            tr.a_uid: {"t": "trade_commit", "txn": tr.txn, "slot": tr.a_slot, "give": tr.a_mon.fp,
                       "mon": tr.b_mon.hex, "summary": tr.b_mon.summary()},
            tr.b_uid: {"t": "trade_commit", "txn": tr.txn, "slot": tr.b_slot, "give": tr.b_mon.fp,
                       "mon": tr.a_mon.hex, "summary": tr.a_mon.summary()},
        }
        self.trade_ledger.append({"e": "commit", "txn": tr.txn, "ts": time.time(), "room": tr.room,
                                  "a": tr.a_uid, "b": tr.b_uid, "msgs": msgs})
        del self.trades[tr.txn]
        for uid, m in msgs.items():
            self.pending_commits.setdefault(uid, {})[tr.txn] = m
            c = self.by_uid.get(uid)
            if c:
                c.send(m)
        log.info("trade %s committed: %s <-> %s", tr.txn, tr.a_mon.summary(), tr.b_mon.summary())

    def _on_trade_cancel(self, conn: Conn, msg: dict) -> None:
        tr = self._get_trade(conn, msg)
        self._abort_trade(tr, "cancelled", by=conn)

    def _abort_trade(self, tr: Trade, reason: str, by: Conn | None = None) -> None:
        if self.trades.pop(tr.txn, None) is None:
            return
        for c in (tr.a, tr.b):
            if c is not by:
                c.send({"t": "trade_abort", "txn": tr.txn, "reason": reason})
            else:
                c.send({"t": "trade_abort", "txn": tr.txn, "reason": reason, "by_you": True})

    def _on_trade_done(self, conn: Conn, msg: dict) -> None:
        txn = msg.get("txn")
        pending = self.pending_commits.get(conn.uid, {})
        if txn not in pending:
            raise ProtocolError("no_trade", "nothing to acknowledge")
        status = msg.get("status", "applied")
        self.trade_ledger.append({"e": "ack", "txn": txn, "uid": conn.uid, "status": str(status)[:16],
                                  "ts": time.time()})
        del pending[txn]
        if not pending:
            self.pending_commits.pop(conn.uid, None)
        if status != "applied":
            log.warning("trade %s acknowledged by %s with status %r", txn, conn.uid, status)

    # -- battles -----------------------------------------------------------

    def _battle_conn(self, b: Battle, side: int) -> Conn | None:
        c = self.by_uid.get(b.uids[side])
        return c if c and c.room == b.room else None

    def _get_battle(self, conn: Conn, msg: dict) -> tuple[Battle, int]:
        b = self.battles.get(msg.get("bid"))
        if b is None or b.state == "over" or conn.uid not in b.uids:
            raise ProtocolError("no_battle", "no such battle")
        return b, b.uids.index(conn.uid)

    def _on_battle_req(self, conn: Conn, msg: dict) -> None:
        other = self._peer(conn, msg.get("to"))
        if self._in_battle(conn) or self._in_battle(other):
            raise ProtocolError("busy", "you or that player are already in a battle")
        if self._trade_of(conn) or self._trade_of(other):
            raise ProtocolError("busy", "you or that player have a trade open")
        rules = msg.get("rules") if isinstance(msg.get("rules"), dict) else {}
        if len(json.dumps(rules)) > 256:
            raise ProtocolError("too_long", "rules too long")
        b = Battle(bid=secrets.token_hex(8), room=conn.room, uids=[conn.uid, other.uid],
                   names=[conn.name, other.name], rules=rules,
                   deadline=time.monotonic() + self.cfg.trade_timeout)
        self.battles[b.bid] = b
        conn.send({"t": "battle_pending", "bid": b.bid, "to": other.id})
        other.send({"t": "battle_invite", "bid": b.bid, "from": conn.id, "name": conn.name, "rules": rules})

    def _on_battle_accept(self, conn: Conn, msg: dict) -> None:
        b, side = self._get_battle(conn, msg)
        if b.state != "invited" or side != 1:
            raise ProtocolError("bad_state", "nothing to accept")
        b.state = "teams"
        b.deadline = time.monotonic() + self.cfg.trade_timeout
        for s in (0, 1):
            c = self._battle_conn(b, s)
            if c:
                c.send({"t": "battle_teamreq", "bid": b.bid})

    def _on_battle_decline(self, conn: Conn, msg: dict) -> None:
        b, side = self._get_battle(conn, msg)
        if b.state not in ("invited", "teams"):
            raise ProtocolError("bad_state", "battle already started; use battle_forfeit")
        self._end_battle(b, None, "declined")

    def _on_battle_team(self, conn: Conn, msg: dict) -> None:
        b, side = self._get_battle(conn, msg)
        if b.state != "teams" or b.teams[side] is not None:
            raise ProtocolError("bad_state", "not expecting a team")
        party = msg.get("party")
        if not isinstance(party, list) or not 1 <= len(party) <= 6:
            raise ProtocolError("bad_team", "party must hold 1-6 Pokémon")
        mons = [parse_mon(m) for m in party]
        if len({m.fp for m in mons}) != len(mons):
            raise ProtocolError("bad_team", "duplicate Pokémon in party")
        max_level = b.rules.get("level_cap")
        if isinstance(max_level, int) and any(m.level > max_level for m in mons):
            raise ProtocolError("bad_team", f"level cap is {max_level}")
        b.teams[side] = [m.hex for m in mons]
        if b.teams[0] is not None and b.teams[1] is not None:
            b.state = "active"
            b.seed = secrets.randbits(32)
            b.turn = 1
            b.deadline = time.monotonic() + self.cfg.turn_timeout
            for s in (0, 1):
                c = self._battle_conn(b, s)
                if c:
                    c.send(self._battle_start_msg(b, s))

    def _battle_start_msg(self, b: Battle, side: int) -> dict:
        other = self._battle_conn(b, 1 - side)
        return {"t": "battle_start", "bid": b.bid, "seed": b.seed, "side": side, "turn": b.turn,
                "rules": b.rules, "party": b.teams[side],
                "opp": {"id": other.id if other else None, "name": b.names[1 - side], "party": b.teams[1 - side]}}

    def _on_battle_action(self, conn: Conn, msg: dict) -> None:
        b, side = self._get_battle(conn, msg)
        if b.state != "active":
            raise ProtocolError("bad_state", "battle not active")
        turn, action, h = msg.get("turn"), msg.get("action"), msg.get("hash")
        if turn != b.turn:
            if b.last_turn_msg and turn == b.turn - 1:
                conn.send(b.last_turn_msg)  # duplicate after a reconnect: replay the result
                return
            raise ProtocolError("bad_turn", f"expected turn {b.turn}")
        if not isinstance(action, dict) or len(json.dumps(action)) > 256:
            raise ProtocolError("bad_msg", "action must be a small object")
        if h is not None and (not isinstance(h, str) or len(h) > 64):
            raise ProtocolError("bad_msg", "hash must be a short string")
        if side in b.actions:
            if b.actions[side] == (action, h):
                return
            raise ProtocolError("bad_state", "action for this turn already sent")
        b.actions[side] = (action, h)
        if len(b.actions) < 2:
            return
        (a0, h0), (a1, h1) = b.actions[0], b.actions[1]
        if h0 is not None and h1 is not None and h0 != h1:
            self._end_battle(b, None, "desync", extra={"turn": b.turn, "hashes": [h0, h1]})
            return
        b.last_turn_msg = {"t": "battle_turn", "bid": b.bid, "turn": b.turn, "actions": [a0, a1]}
        b.actions = {}
        b.turn += 1
        b.deadline = time.monotonic() + self.cfg.turn_timeout
        for s in (0, 1):
            c = self._battle_conn(b, s)
            if c:
                c.send(b.last_turn_msg)

    def _on_battle_end(self, conn: Conn, msg: dict) -> None:
        b, side = self._get_battle(conn, msg)
        if b.state != "active":
            raise ProtocolError("bad_state", "battle not active")
        winner = msg.get("winner")
        if winner not in (0, 1, None):
            raise ProtocolError("bad_msg", "winner must be 0, 1 or null")
        b.ends[side] = winner
        if len(b.ends) == 2:
            if b.ends[0] == b.ends[1]:
                self._end_battle(b, b.ends[0], "normal")
            else:
                self._end_battle(b, None, "desync", extra={"reported": [b.ends[0], b.ends[1]]})

    def _on_battle_forfeit(self, conn: Conn, msg: dict) -> None:
        b, side = self._get_battle(conn, msg)
        if b.state in ("invited", "teams"):
            self._end_battle(b, None, "cancelled")
        else:
            self._end_battle(b, 1 - side, "forfeit")

    def _end_battle(self, b: Battle, winner, reason: str, extra: dict | None = None) -> None:
        if b.state == "over":
            return
        started = b.state == "active"
        b.state = "over"
        msg = {"t": "battle_result", "bid": b.bid, "winner": winner, "reason": reason, **(extra or {})}
        for s in (0, 1):
            c = self._battle_conn(b, s)
            if c:
                c.send(msg)
        if started:
            self.battle_log.append({"bid": b.bid, "ts": time.time(), "room": b.room, "players": b.uids,
                                    "names": b.names, "winner": winner, "reason": reason,
                                    "turns": b.turn - 1, **(extra or {})})
        self.battles.pop(b.bid, None)
        log.info("battle %s over: winner=%s reason=%s", b.bid, winner, reason)

    def _resume_battles(self, conn: Conn) -> None:
        for b in self.battles.values():
            if b.state != "active" or conn.uid not in b.uids or b.room != conn.room:
                continue
            side = b.uids.index(conn.uid)
            b.away.pop(side, None)
            msg = self._battle_start_msg(b, side)
            msg["t"] = "battle_resume"
            msg["have_action"] = side in b.actions
            conn.send(msg)
            if b.last_turn_msg:
                conn.send(b.last_turn_msg)
            other = self._battle_conn(b, 1 - side)
            if other:
                other.send({"t": "battle_wait", "bid": b.bid, "reason": "resumed"})


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Radical Red Online relay server")
    p.add_argument("--host", default=os.environ.get("RRMP_HOST", "127.0.0.1"),
                   help="address to listen on (127.0.0.1 = this PC only, 0.0.0.0 = everyone)")
    p.add_argument("--port", type=int, default=int(os.environ.get("RRMP_PORT", "8765")))
    p.add_argument("--max-room", type=int, default=8, help="players per room (1-8)")
    p.add_argument("--max-conns", type=int, default=Config.max_conns, help="open connections in total")
    p.add_argument("--max-per-ip", type=int, default=Config.max_per_ip,
                   help="open connections per remote address (this PC is exempt)")
    p.add_argument("--no-lan", action="store_true", help="don't answer LAN discovery broadcasts")
    p.add_argument("--lan-name", default="", help="name shown to LAN players looking for games (default: PC name)")
    p.add_argument("--data-dir", default=Config.data_dir, help="where the trade ledger and battle log live")
    p.add_argument("--verbose", "-v", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config(host=a.host, port=a.port, max_room=a.max_room, data_dir=a.data_dir,
                 max_conns=a.max_conns, max_per_ip=a.max_per_ip,
                 lan_port=None if a.no_lan or a.host.startswith("127.") or a.host == "localhost" else LAN_PORT)
    server = Server(cfg)
    if a.lan_name:
        server.host_name = re.sub(r"[^A-Za-z0-9_ -]", "", a.lan_name)[:32]
    try:
        asyncio.run(server.serve_forever())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
