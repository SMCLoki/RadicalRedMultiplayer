#!/usr/bin/env python3
"""Relay server tests. Stdlib only:  python server/test_server.py  (or python -m unittest discover server)"""
import asyncio
import json
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rr_server  # noqa: E402

GAME = "BPRE:deadbeef"


def make_mon(pid, otid=0x1234, species=152, level=5, moves=(33, 45, 0, 0), hp=20, maxhp=20) -> str:
    raw = bytearray(100)
    struct.pack_into("<II", raw, 0, pid, otid)
    struct.pack_into("<HHI", raw, 0x20, species, 0, 135)
    struct.pack_into("<4H", raw, 0x2C, *moves)
    raw[0x54] = level
    struct.pack_into("<HH", raw, 0x56, hp, maxhp)
    return raw.hex()


class Client:
    def __init__(self, reader, writer):
        self.r, self.w = reader, writer
        self.id = None
        self.uid = None

    @classmethod
    async def connect(cls, port, name=None, room="ROOM", game=GAME, uid=None, hello=True):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        c = cls(r, w)
        if hello:
            msg = {"t": "hello", "v": 1, "name": name or "P", "room": room, "game": game}
            if uid:
                msg["uid"] = uid
            await c.send(msg)
        return c

    async def send(self, msg):
        self.w.write((json.dumps(msg) + "\n").encode())
        await self.w.drain()

    async def recv(self, timeout=2.0):
        line = await asyncio.wait_for(self.r.readline(), timeout)
        if not line:
            raise ConnectionError("closed")
        return json.loads(line)

    async def expect(self, t, timeout=2.0, **fields):
        """Read until a message of type t arrives (skipping others), return it."""
        while True:
            m = await self.recv(timeout)
            if m["t"] == t and all(m.get(k) == v for k, v in fields.items()):
                return m

    async def welcome(self):
        m = await self.expect("welcome")
        self.id, self.uid = m["id"], m["uid"]
        return m

    async def closed(self, timeout=2.0):
        try:
            while True:
                line = await asyncio.wait_for(self.r.readline(), timeout)
                if not line:
                    return True
        except (ConnectionError, asyncio.TimeoutError):
            return False

    async def close(self):
        self.w.close()
        try:
            await self.w.wait_closed()
        except Exception:
            pass


class ServerTest(unittest.IsolatedAsyncioTestCase):
    cfg_overrides = {}

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = rr_server.Config(port=0, data_dir=self.tmp.name, tick=0.05, **self.cfg_overrides)
        self.server = rr_server.Server(cfg)
        self.port = await self.server.start()
        self.clients = []

    async def asyncTearDown(self):
        for c in self.clients:
            await c.close()
        await self.server.stop()
        self.tmp.cleanup()

    async def join(self, name, room="ROOM", **kw):
        c = await Client.connect(self.port, name, room, **kw)
        self.clients.append(c)
        await c.welcome()
        return c

    async def restart_server(self):
        await self.server.stop()
        cfg = rr_server.Config(port=0, data_dir=self.tmp.name, tick=0.05, **self.cfg_overrides)
        self.server = rr_server.Server(cfg)
        self.port = await self.server.start()


class SessionTests(ServerTest):
    async def test_welcome_lists_peers_and_join(self):
        a = await self.join("Red")
        b = await Client.connect(self.port, "Blue")
        self.clients.append(b)
        w = await b.welcome()
        self.assertEqual(w["peers"], [{"id": a.id, "name": "Red"}])
        self.assertEqual(w["max"], 8)
        j = await a.expect("join")
        self.assertEqual((j["id"], j["name"]), (b.id, "Blue"))

    async def test_eight_players_then_room_full(self):
        cs = [await self.join(f"P{i}") for i in range(8)]
        self.assertEqual(len({c.id for c in cs}), 8)
        ninth = await Client.connect(self.port, "P9")
        self.clients.append(ninth)
        e = await ninth.expect("error")
        self.assertEqual(e["code"], "room_full")
        self.assertTrue(await ninth.closed())
        # another room is independent
        await self.join("Other", room="ROOM2")

    async def test_game_mismatch(self):
        await self.join("Red")
        b = await Client.connect(self.port, "Blue", game="BPRE:00000000")
        self.clients.append(b)
        self.assertEqual((await b.expect("error"))["code"], "game_mismatch")

    async def test_bad_version_and_bad_hello(self):
        c = await Client.connect(self.port, hello=False)
        self.clients.append(c)
        await c.send({"t": "hello", "v": 99, "name": "x", "room": "R", "game": GAME})
        self.assertEqual((await c.expect("error"))["code"], "bad_version")
        d = await Client.connect(self.port, hello=False)
        self.clients.append(d)
        await d.send({"t": "ping", "n": 1})
        self.assertEqual((await d.expect("error"))["code"], "bad_hello")
        e = await Client.connect(self.port, "x", room="bad room!")
        self.clients.append(e)
        self.assertEqual((await e.expect("error"))["code"], "bad_hello")

    async def test_name_sanitised(self):
        c = await Client.connect(self.port, "  ABCDEFGHIJKLMNOP\x07 ")
        self.clients.append(c)
        await c.welcome()
        d = await Client.connect(self.port, "Q")
        self.clients.append(d)
        w = await d.welcome()
        self.assertEqual(w["peers"][0]["name"], "ABCDEFGHIJKL")

    async def test_ping_pong(self):
        a = await self.join("Red")
        await a.send({"t": "ping", "n": 17})
        self.assertEqual((await a.expect("pong"))["n"], 17)

    async def test_leave_on_quit(self):
        a = await self.join("Red")
        b = await self.join("Blue")
        await b.send({"t": "bye"})
        m = await a.expect("leave")
        self.assertEqual((m["id"], m["reason"]), (b.id, "quit"))

    async def test_too_long_line(self):
        a = await self.join("Red")
        a.w.write(b"x" * (rr_server.MAX_LINE + 10) + b"\n")
        await a.w.drain()
        self.assertEqual((await a.expect("error"))["code"], "too_long")

    async def test_unknown_type_is_not_fatal(self):
        a = await self.join("Red")
        await a.send({"t": "wat"})
        e = await a.expect("error")
        self.assertEqual((e["code"], e["fatal"]), ("bad_type", False))
        await a.send({"t": "ping", "n": 2})
        await a.expect("pong")

    async def test_uid_reconnect_replaces_old_socket(self):
        a = await self.join("Red", uid="abcdef123456")
        b = await self.join("Blue")
        a2 = await self.join("Red", uid="abcdef123456")
        self.assertEqual(a2.uid, "abcdef123456")
        self.assertEqual((await a.expect("error"))["code"], "replaced")
        m = await b.expect("leave")
        self.assertEqual((m["id"], m["reason"]), (a.id, "replaced"))
        await b.expect("join", id=a2.id)


class TimeoutTests(ServerTest):
    cfg_overrides = {"idle_timeout": 0.4, "hello_timeout": 0.4}

    async def test_idle_timeout(self):
        a = await self.join("Red")
        b = await self.join("Blue")
        for _ in range(4):
            await asyncio.sleep(0.15)
            await b.send({"t": "ping", "n": 0})
        self.assertEqual((await a.expect("error"))["code"], "timeout")
        m = await b.expect("leave")
        self.assertEqual(m["reason"], "timeout")

    async def test_hello_timeout(self):
        c = await Client.connect(self.port, hello=False)
        self.clients.append(c)
        self.assertEqual((await c.expect("error"))["code"], "timeout")


class RateTests(ServerTest):
    cfg_overrides = {"rate": 5.0, "burst": 10.0}

    async def test_rate_limit(self):
        a = await self.join("Red")
        for i in range(30):
            a.w.write((json.dumps({"t": "ping", "n": i}) + "\n").encode())
        await a.w.drain()
        self.assertEqual((await a.expect("error"))["code"], "rate_limited")


class RelayTests(ServerTest):
    async def test_state_relay_and_snapshot(self):
        a = await self.join("Red")
        b = await self.join("Blue")
        await a.send({"t": "state", "map": [3, 0], "x": 5, "y": 6, "f": 1})
        m = await b.expect("state")
        self.assertEqual((m["from"], m["map"], m["x"]), (a.id, [3, 0], 5))
        c = await Client.connect(self.port, "Green")
        self.clients.append(c)
        w = await c.welcome()
        red = next(p for p in w["peers"] if p["id"] == a.id)
        self.assertEqual(red["state"]["x"], 5)

    async def test_chat(self):
        a = await self.join("Red")
        b = await self.join("Blue")
        await a.send({"t": "chat", "text": "hi " + "x" * 200})
        for c in (a, b):
            m = await c.expect("chat")
            self.assertEqual((m["from"], m["name"], len(m["text"])), (a.id, "Red", 120))

    async def test_direct(self):
        a = await self.join("Red")
        b = await self.join("Blue")
        c = await self.join("Green")
        await a.send({"t": "direct", "to": c.id, "k": "secret"})
        m = await c.expect("direct")
        self.assertEqual((m["from"], m["k"]), (a.id, "secret"))
        await b.send({"t": "ping", "n": 5})
        self.assertEqual((await b.expect("pong"))["n"], 5)  # b never saw the direct message
        await a.send({"t": "direct", "to": 999})
        self.assertEqual((await a.expect("error"))["code"], "no_peer")

    async def test_rooms_isolated(self):
        a = await self.join("Red", room="A")
        b = await self.join("Blue", room="B")
        await a.send({"t": "chat", "text": "hello A"})
        await a.expect("chat")
        await b.send({"t": "ping", "n": 1})
        self.assertEqual((await b.expect("pong"))["n"], 1)


class TradeTests(ServerTest):
    async def offer(self, a, b, mon_a, mon_b, slot_a=0, slot_b=1):
        await a.send({"t": "trade_offer", "to": b.id, "slot": slot_a, "mon": mon_a})
        txn = (await a.expect("trade_pending"))["txn"]
        o = await b.expect("trade_offered")
        self.assertEqual((o["txn"], o["mon"], o["from"]), (txn, mon_a, a.id))
        await b.send({"t": "trade_answer", "txn": txn, "slot": slot_b, "mon": mon_b})
        self.assertEqual((await a.expect("trade_preview"))["mon"], mon_b)
        self.assertEqual((await b.expect("trade_preview"))["mon"], mon_a)
        return txn

    async def test_happy_path(self):
        a, b = await self.join("Red"), await self.join("Blue")
        ma, mb = make_mon(1), make_mon(2, species=4)
        txn = await self.offer(a, b, ma, mb)
        await a.send({"t": "trade_confirm", "txn": txn})
        await b.expect("trade_confirmed")
        await b.send({"t": "trade_confirm", "txn": txn})
        ca = await a.expect("trade_commit")
        cb = await b.expect("trade_commit")
        self.assertEqual((ca["mon"], ca["slot"], ca["give"]), (mb, 0, "00000001" + "00001234"))
        self.assertEqual((cb["mon"], cb["slot"]), (ma, 1))
        await a.send({"t": "trade_done", "txn": txn})
        await b.send({"t": "trade_done", "txn": txn})
        await a.send({"t": "ping", "n": 1})
        await a.expect("pong")
        self.assertEqual(self.server.pending_commits, {})

    async def test_invalid_mon_rejected(self):
        a, b = await self.join("Red"), await self.join("Blue")
        bad = make_mon(1, level=0)
        await a.send({"t": "trade_offer", "to": b.id, "slot": 0, "mon": bad})
        self.assertEqual((await a.expect("error"))["code"], "bad_mon")
        enc = bytearray.fromhex(make_mon(1))
        enc[0x1C] = 7  # encrypted/vanilla checksum present
        await a.send({"t": "trade_offer", "to": b.id, "slot": 0, "mon": enc.hex()})
        self.assertEqual((await a.expect("error"))["code"], "bad_mon")
        await a.send({"t": "trade_offer", "to": b.id, "slot": 0, "mon": "zz" * 100})
        self.assertEqual((await a.expect("error"))["code"], "bad_mon")

    async def test_disconnect_before_commit_aborts(self):
        a, b = await self.join("Red"), await self.join("Blue")
        txn = await self.offer(a, b, make_mon(1), make_mon(2))
        await a.send({"t": "trade_confirm", "txn": txn})
        await b.expect("trade_confirmed")
        await a.close()
        m = await b.expect("trade_abort")
        self.assertEqual((m["txn"], m["reason"]), (txn, "disconnect"))
        await b.send({"t": "trade_confirm", "txn": txn})
        self.assertEqual((await b.expect("error"))["code"], "no_trade")
        self.assertEqual(self.server.pending_commits, {})

    async def test_commit_redelivered_after_reconnect_and_server_restart(self):
        a = await self.join("Red", uid="useraaaa1111")
        b = await self.join("Blue", uid="userbbbb2222")
        ma, mb = make_mon(1), make_mon(2)
        txn = await self.offer(a, b, ma, mb)
        await a.send({"t": "trade_confirm", "txn": txn})
        await b.send({"t": "trade_confirm", "txn": txn})
        await a.expect("trade_commit")
        await b.expect("trade_commit")
        await a.send({"t": "trade_done", "txn": txn})
        await asyncio.sleep(0.1)
        # Blue crashes before acknowledging; the server restarts as well.
        await b.close()
        await self.restart_server()
        b2 = await Client.connect(self.port, "Blue", uid="userbbbb2222")
        self.clients.append(b2)
        await b2.welcome()
        m = await b2.expect("trade_commit")
        self.assertEqual((m["txn"], m["mon"], m["slot"]), (txn, ma, 1))
        a2 = await self.join("Red", uid="useraaaa1111")
        # Red already acknowledged, so it must not get the commit again; it can trade b2 only after b2 acks.
        await a2.send({"t": "trade_offer", "to": b2.id, "slot": 0, "mon": mb})
        self.assertEqual((await a2.expect("error"))["code"], "busy")
        await b2.send({"t": "trade_done", "txn": txn})
        await asyncio.sleep(0.1)
        await a2.send({"t": "trade_offer", "to": b2.id, "slot": 0, "mon": mb})
        await a2.expect("trade_pending")

    async def test_cancel_and_busy(self):
        a, b, c = await self.join("Red"), await self.join("Blue"), await self.join("Green")
        await a.send({"t": "trade_offer", "to": b.id, "slot": 0, "mon": make_mon(1)})
        txn = (await a.expect("trade_pending"))["txn"]
        await c.send({"t": "trade_offer", "to": b.id, "slot": 0, "mon": make_mon(3)})
        self.assertEqual((await c.expect("error"))["code"], "busy")
        await b.send({"t": "trade_cancel", "txn": txn})
        self.assertEqual((await a.expect("trade_abort"))["reason"], "cancelled")
        await c.send({"t": "trade_offer", "to": b.id, "slot": 0, "mon": make_mon(3)})
        await c.expect("trade_pending")

    async def test_self_trade_and_confirm_before_answer(self):
        a, b = await self.join("Red"), await self.join("Blue")
        await a.send({"t": "trade_offer", "to": b.id, "slot": 0, "mon": make_mon(1)})
        txn = (await a.expect("trade_pending"))["txn"]
        await a.send({"t": "trade_confirm", "txn": txn})
        self.assertEqual((await a.expect("error"))["code"], "bad_state")
        await b.send({"t": "trade_answer", "txn": txn, "slot": 0, "mon": make_mon(1)})
        self.assertEqual((await b.expect("error"))["code"], "bad_mon")


class TradeTimeoutTests(ServerTest):
    cfg_overrides = {"trade_timeout": 0.3}

    async def test_trade_times_out(self):
        a, b = await self.join("Red"), await self.join("Blue")
        await a.send({"t": "trade_offer", "to": b.id, "slot": 0, "mon": make_mon(1)})
        await a.expect("trade_pending")
        self.assertEqual((await a.expect("trade_abort"))["reason"], "timeout")


def team(base, n=6):
    return [make_mon(base + i, species=1 + i) for i in range(n)]


class BattleTests(ServerTest):
    async def start_battle(self, a, b, rules=None):
        await a.send({"t": "battle_req", "to": b.id, "rules": rules or {}})
        bid = (await a.expect("battle_pending"))["bid"]
        inv = await b.expect("battle_invite")
        self.assertEqual((inv["bid"], inv["from"]), (bid, a.id))
        await b.send({"t": "battle_accept", "bid": bid})
        await a.expect("battle_teamreq")
        await b.expect("battle_teamreq")
        ta, tb = team(100), team(200)
        await a.send({"t": "battle_team", "bid": bid, "party": ta})
        await b.send({"t": "battle_team", "bid": bid, "party": tb})
        sa = await a.expect("battle_start")
        sb = await b.expect("battle_start")
        self.assertEqual(sa["seed"], sb["seed"])
        self.assertEqual((sa["side"], sb["side"]), (0, 1))
        self.assertEqual((sa["opp"]["party"], sb["opp"]["party"]), (tb, ta))
        return bid

    async def play_turn(self, a, b, bid, turn, h="h", hb=None):
        await a.send({"t": "battle_action", "bid": bid, "turn": turn, "action": {"move": 1}, "hash": h})
        await b.send({"t": "battle_action", "bid": bid, "turn": turn, "action": {"switch": 2},
                      "hash": hb if hb is not None else h})

    async def test_full_battle(self):
        a, b = await self.join("Red"), await self.join("Blue")
        bid = await self.start_battle(a, b)
        for turn in range(1, 4):
            await self.play_turn(a, b, bid, turn, h=f"s{turn}")
            for c in (a, b):
                m = await c.expect("battle_turn")
                self.assertEqual((m["turn"], m["actions"]), (turn, [{"move": 1}, {"switch": 2}]))
        await a.send({"t": "battle_end", "bid": bid, "winner": 1})
        await b.send({"t": "battle_end", "bid": bid, "winner": 1})
        for c in (a, b):
            r = await c.expect("battle_result")
            self.assertEqual((r["winner"], r["reason"]), (1, "normal"))
        logged = list(self.server.battle_log.read())
        self.assertEqual((logged[-1]["turns"], logged[-1]["winner"]), (3, 1))

    async def test_desync_detected(self):
        a, b = await self.join("Red"), await self.join("Blue")
        bid = await self.start_battle(a, b)
        await self.play_turn(a, b, bid, 1, h="aaa", hb="bbb")
        r = await a.expect("battle_result")
        self.assertEqual((r["reason"], r["turn"]), ("desync", 1))
        await b.expect("battle_result", reason="desync")

    async def test_disagreeing_result_is_desync(self):
        a, b = await self.join("Red"), await self.join("Blue")
        bid = await self.start_battle(a, b)
        await a.send({"t": "battle_end", "bid": bid, "winner": 0})
        await b.send({"t": "battle_end", "bid": bid, "winner": 1})
        self.assertEqual((await a.expect("battle_result"))["reason"], "desync")

    async def test_forfeit(self):
        a, b = await self.join("Red"), await self.join("Blue")
        bid = await self.start_battle(a, b)
        await b.send({"t": "battle_forfeit", "bid": bid})
        r = await a.expect("battle_result")
        self.assertEqual((r["winner"], r["reason"]), (0, "forfeit"))

    async def test_wrong_turn_and_bad_team(self):
        a, b = await self.join("Red"), await self.join("Blue")
        await a.send({"t": "battle_req", "to": b.id, "rules": {"level_cap": 50}})
        bid = (await a.expect("battle_pending"))["bid"]
        await b.send({"t": "battle_accept", "bid": bid})
        await a.send({"t": "battle_team", "bid": bid, "party": [make_mon(1, level=60)]})
        self.assertEqual((await a.expect("error"))["code"], "bad_team")
        await a.send({"t": "battle_team", "bid": bid, "party": [make_mon(1), make_mon(1)]})
        self.assertEqual((await a.expect("error"))["code"], "bad_team")
        await a.send({"t": "battle_team", "bid": bid, "party": [make_mon(1)]})
        await b.send({"t": "battle_team", "bid": bid, "party": [make_mon(2)]})
        await a.expect("battle_start")
        await a.send({"t": "battle_action", "bid": bid, "turn": 5, "action": {}})
        self.assertEqual((await a.expect("error"))["code"], "bad_turn")

    async def test_decline(self):
        a, b = await self.join("Red"), await self.join("Blue")
        await a.send({"t": "battle_req", "to": b.id})
        bid = (await a.expect("battle_pending"))["bid"]
        await b.send({"t": "battle_decline", "bid": bid})
        self.assertEqual((await a.expect("battle_result"))["reason"], "declined")

    async def test_parallel_battles_in_one_room(self):
        cs = [await self.join(f"P{i}") for i in range(8)]
        bids = []
        for i in range(0, 8, 2):
            bids.append(await self.start_battle(cs[i], cs[i + 1]))
        for i, bid in zip(range(0, 8, 2), bids):
            await self.play_turn(cs[i], cs[i + 1], bid, 1)
            await cs[i].expect("battle_turn", bid=bid)
            await cs[i + 1].expect("battle_turn", bid=bid)

    async def test_reconnect_resumes_battle(self):
        a = await self.join("Red", uid="redred111111")
        b = await self.join("Blue", uid="blueblue2222")
        bid = await self.start_battle(a, b)
        await self.play_turn(a, b, bid, 1)
        await a.expect("battle_turn")
        await b.expect("battle_turn")
        await a.send({"t": "battle_action", "bid": bid, "turn": 2, "action": {"move": 3}, "hash": "x"})
        await asyncio.sleep(0.05)
        await a.close()
        w = await b.expect("battle_wait")
        self.assertEqual(w["reason"], "disconnected")
        a2 = await Client.connect(self.port, "Red", uid="redred111111")
        self.clients.append(a2)
        await a2.welcome()
        r = await a2.expect("battle_resume")
        self.assertEqual((r["bid"], r["turn"], r["side"], r["have_action"]), (bid, 2, 0, True))
        self.assertEqual((await a2.expect("battle_turn"))["turn"], 1)
        await b.expect("battle_wait", reason="resumed")
        await b.send({"t": "battle_action", "bid": bid, "turn": 2, "action": {"move": 4}, "hash": "x"})
        m = await a2.expect("battle_turn", turn=2)
        self.assertEqual(m["actions"], [{"move": 3}, {"move": 4}])


class BattleTimeoutTests(ServerTest):
    cfg_overrides = {"turn_timeout": 0.3, "battle_grace": 0.3}

    async def test_turn_timeout_forfeits_idle_side(self):
        a, b = await self.join("Red"), await self.join("Blue")
        bid = await BattleTests.start_battle(self, a, b)
        await a.send({"t": "battle_action", "bid": bid, "turn": 1, "action": {"move": 1}})
        r = await a.expect("battle_result")
        self.assertEqual((r["winner"], r["reason"]), (0, "turn_timeout"))



class BattleGraceTests(ServerTest):
    # turn timer much longer than the grace, so only the disconnect can end the battle
    cfg_overrides = {"turn_timeout": 5.0, "battle_grace": 0.3}

    async def test_disconnect_past_grace_loses(self):
        a, b = await self.join("Red"), await self.join("Blue")
        await BattleTests.start_battle(self, a, b)
        await b.close()
        r = await a.expect("battle_result")
        self.assertEqual((r["winner"], r["reason"]), (0, "disconnect"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
