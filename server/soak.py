#!/usr/bin/env python3
"""Soak test: N simulated players hammer a room with presence, chat, trades and battles while
randomly disconnecting (including right after a trade commit, before applying it).

At the end every bot reconnects, all pending trade commits are applied, and the script checks
the conservation invariant: the multiset of Pokémon across all bots equals the starting one
(no duplicates, no losses). Exit code 0 = pass.

    python server/soak.py --clients 8 --minutes 2            # in-process server
    python server/soak.py --host 1.2.3.4 --port 8765 ...     # against a running server
"""
import argparse
import asyncio
import collections
import json
import os
import random
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rr_server  # noqa: E402
from test_server import make_mon  # noqa: E402

GAME = "BPRE:soaktest"


class Stats(collections.Counter):
    pass


class Bot:
    def __init__(self, idx, host, port, room, stats, rng):
        self.idx, self.host, self.port, self.room = idx, host, port, room
        self.uid = f"soakbot{idx:04d}x"
        self.name = f"Bot{idx}"
        self.stats, self.rng = stats, rng
        self.party = [make_mon((idx + 1) * 0x1000 + s, otid=0xBEEF, species=1 + (idx * 6 + s) % 900,
                               level=5 + s) for s in range(6)]
        self.journal = set()
        self.w = None
        self.id = None
        self.peers = {}
        self.trade = None
        self.battle = None
        self.online = False
        self.chaos = True

    def fps(self):
        return [rr_server.parse_mon(m).fp for m in self.party]

    async def send(self, msg):
        if self.w is None:
            return
        try:
            self.w.write((json.dumps(msg) + "\n").encode())
            await self.w.drain()
        except (ConnectionError, RuntimeError):
            pass

    async def session(self, stop_at):
        r, self.w = await asyncio.open_connection(self.host, self.port)
        self.trade = self.battle = None
        await self.send({"t": "hello", "v": 1, "name": self.name, "room": self.room, "game": GAME,
                         "uid": self.uid})
        reader = asyncio.create_task(self.read_loop(r))
        try:
            while not reader.done() and time.monotonic() < stop_at:
                await asyncio.sleep(self.rng.uniform(0.05, 0.3))
                if not self.online:
                    continue
                await self.act()
                if self.chaos and self.rng.random() < 0.01:
                    self.stats["chaos_disconnect"] += 1
                    break
        finally:
            reader.cancel()
            self.online = False
            self.w.close()
            self.w = None

    async def act(self):
        roll = self.rng.random()
        await self.send({"t": "ping", "n": 1})
        if not self.chaos:
            return
        if roll < 0.4:
            await self.send({"t": "state", "map": [3, 0], "x": self.rng.randint(0, 20), "y": self.rng.randint(0, 20), "f": 1})
        elif roll < 0.45:
            await self.send({"t": "chat", "text": f"hi from {self.name}"})
        elif roll < 0.6 and self.peers and not self.trade and not self.battle:
            to = self.rng.choice(list(self.peers))
            slot = self.rng.randrange(6)
            self.trade = {"slot": slot}
            await self.send({"t": "trade_offer", "to": to, "slot": slot, "mon": self.party[slot]})
        elif roll < 0.65 and self.peers and not self.trade and not self.battle:
            self.battle = {"pending": True}
            await self.send({"t": "battle_req", "to": self.rng.choice(list(self.peers))})
        elif self.battle and self.battle.get("active") and not self.battle.get("sent"):
            b = self.battle
            if b["turn"] > b["length"]:
                await self.send({"t": "battle_end", "bid": b["bid"], "winner": b["length"] % 2})
                b["sent"] = True
            else:
                await self.send({"t": "battle_action", "bid": b["bid"], "turn": b["turn"],
                                 "action": {"move": self.rng.randint(1, 4)}, "hash": f"{b['bid']}:{b['turn']}"})
                b["sent"] = True

    def apply_commit(self, m):
        recv = rr_server.parse_mon(m["mon"])
        fps = self.fps()
        if recv.fp in fps:
            return "applied"
        if m["give"] not in fps:
            self.stats["commit_missing"] += 1
            return "missing"
        self.party[fps.index(m["give"])] = m["mon"]
        self.journal.add(m["txn"])
        self.stats["trades_applied"] += 1
        return "applied"

    async def read_loop(self, r):
        while True:
            line = await r.readline()
            if not line:
                return
            m = json.loads(line)
            t = m["t"]
            if t == "welcome":
                self.id, self.online = m["id"], True
                self.peers = {p["id"]: p["name"] for p in m["peers"]}
                self.stats["sessions"] += 1
            elif t == "join":
                self.peers[m["id"]] = m["name"]
            elif t == "leave":
                self.peers.pop(m["id"], None)
            elif t == "error":
                self.stats["err_" + m["code"]] += 1
                if m.get("re") == "trade_offer":
                    self.trade = None
                if m.get("re") == "battle_req":
                    self.battle = None
            elif t == "trade_pending":
                if self.trade:
                    self.trade["txn"] = m["txn"]
            elif t == "trade_offered":
                if self.trade or self.battle or self.rng.random() < 0.2:
                    await self.send({"t": "trade_cancel", "txn": m["txn"]})
                else:
                    slot = self.rng.randrange(6)
                    self.trade = {"txn": m["txn"], "slot": slot}
                    await self.send({"t": "trade_answer", "txn": m["txn"], "slot": slot, "mon": self.party[slot]})
            elif t == "trade_preview":
                if self.trade and self.trade.get("txn") == m["txn"]:
                    # sometimes vanish between preview and confirm
                    if self.chaos and self.rng.random() < 0.05:
                        self.stats["crash_before_confirm"] += 1
                        return
                    await self.send({"t": "trade_confirm", "txn": m["txn"]})
            elif t == "trade_abort":
                if self.trade and self.trade.get("txn") == m["txn"]:
                    self.trade = None
                self.stats["trades_aborted_seen"] += 1
            elif t == "trade_commit":
                self.trade = None
                if self.chaos and self.rng.random() < 0.1:
                    # crash after the server committed but before the game applied it
                    self.stats["crash_after_commit"] += 1
                    return
                status = self.apply_commit(m)
                await self.send({"t": "trade_done", "txn": m["txn"], "status": status})
            elif t == "battle_invite":
                if self.trade or self.battle:
                    await self.send({"t": "battle_decline", "bid": m["bid"]})
                else:
                    self.battle = {"bid": m["bid"]}
                    await self.send({"t": "battle_accept", "bid": m["bid"]})
            elif t == "battle_pending":
                if self.battle:
                    self.battle["bid"] = m["bid"]
            elif t == "battle_teamreq":
                await self.send({"t": "battle_team", "bid": m["bid"], "party": self.party})
            elif t in ("battle_start", "battle_resume"):
                self.battle = {"bid": m["bid"], "active": True, "turn": m["turn"],
                               "length": 3 + (m["seed"] % 4), "sent": bool(m.get("have_action"))}
            elif t == "battle_turn":
                if self.battle and self.battle.get("bid") == m["bid"] and m["turn"] >= self.battle["turn"]:
                    self.battle["turn"] = m["turn"] + 1
                    self.battle["sent"] = False
            elif t == "battle_result":
                if self.battle and self.battle.get("bid") == m["bid"]:
                    self.battle = None
                self.stats["battle_" + m["reason"]] += 1


async def run(args):
    rng = random.Random(args.seed)
    stats = Stats()
    server = None
    host, port = args.host, args.port
    tmp = None
    if host is None:
        tmp = tempfile.TemporaryDirectory()
        server = rr_server.Server(rr_server.Config(port=0, data_dir=tmp.name))
        port = await server.start()
        host = "127.0.0.1"
    bots = [Bot(i, host, port, args.room, stats, random.Random(rng.random())) for i in range(args.clients)]
    initial = collections.Counter(fp for b in bots for fp in b.fps())
    stop_at = time.monotonic() + args.minutes * 60

    async def life(bot):
        while time.monotonic() < stop_at:
            try:
                await bot.session(stop_at)
            except (ConnectionError, OSError) as e:
                stats["connect_error_" + type(e).__name__] += 1
            await asyncio.sleep(bot.rng.uniform(0, 2.5))

    t0 = time.monotonic()
    report_task = asyncio.create_task(report(stats, server, t0, args))
    await asyncio.gather(*(life(b) for b in bots))
    report_task.cancel()

    # Settle: reconnect everyone calmly until no trade commit is pending.
    async def settle(bot):
        bot.chaos = False
        await bot.session(time.monotonic() + 6)

    await asyncio.gather(*(settle(b) for b in bots))

    final = collections.Counter(fp for b in bots for fp in b.fps())
    dupes = {fp: n for fp, n in final.items() if n > 1}
    lost = initial - final
    extra = final - initial
    pending = sum(len(v) for v in server.pending_commits.values()) if server else "n/a"
    print("\n=== soak result ===")
    print(f"clients={args.clients} minutes={args.minutes} elapsed={time.monotonic() - t0:.0f}s")
    for k in sorted(stats):
        print(f"  {k}: {stats[k]}")
    print(f"pending commits after settle: {pending}")
    print(f"pokemon: start={sum(initial.values())} end={sum(final.values())} dupes={len(dupes)} "
          f"lost={sum(lost.values())} extra={sum(extra.values())}")
    ok = not dupes and not lost and not extra and stats["battle_desync"] == 0 and pending in (0, "n/a")
    print("PASS" if ok else "FAIL")
    if server:
        await server.stop()
    if tmp:
        tmp.cleanup()
    return 0 if ok else 1


async def report(stats, server, t0, args):
    while True:
        await asyncio.sleep(30)
        rooms = len(server.rooms) if server else "?"
        print(f"[{time.monotonic() - t0:5.0f}s] trades_applied={stats['trades_applied']} "
              f"battles={stats['battle_normal']} disconnects={stats['chaos_disconnect']} rooms={rooms}", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--clients", type=int, default=8)
    p.add_argument("--minutes", type=float, default=1.0)
    p.add_argument("--host")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--room", default="SOAK")
    p.add_argument("--seed", type=int, default=1)
    sys.exit(asyncio.run(run(p.parse_args())))


if __name__ == "__main__":
    main()
