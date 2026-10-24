#!/usr/bin/env python3
"""TCP proxy that adds internet-like latency and jitter, to test the client/server over a "bad" link on one PC.

    python tools/lag_proxy.py --listen 8765 --target 8766 --delay 150 --jitter 50

Each direction delays every chunk by delay +- jitter ms (order preserved, like TCP). Stdlib only.
"""
import argparse
import asyncio
import random
import time


async def pipe(reader, writer, delay, jitter, stats, name):
    q = asyncio.Queue()

    async def sender():
        last = 0.0
        while True:
            due, data = await q.get()
            if data is None:
                break
            wait = due - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            writer.write(data)
            await writer.drain()
            stats[name] += len(data)

    task = asyncio.create_task(sender())
    last_due = 0.0
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            due = time.monotonic() + max(0.0, (delay + random.uniform(-jitter, jitter)) / 1000.0)
            last_due = max(last_due, due)          # keep order
            await q.put((last_due, data))
    finally:
        await q.put((0, None))
        await task
        writer.close()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", type=int, default=8765)
    ap.add_argument("--target", type=int, default=8766)
    ap.add_argument("--delay", type=float, default=150.0)
    ap.add_argument("--jitter", type=float, default=50.0)
    a = ap.parse_args()
    stats = {"up": 0, "down": 0}

    async def handle(cr, cw):
        sr, sw = await asyncio.open_connection("127.0.0.1", a.target)
        print(f"proxy: client connected, +{a.delay}+-{a.jitter} ms each way", flush=True)
        await asyncio.gather(pipe(cr, sw, a.delay, a.jitter, stats, "up"),
                             pipe(sr, cw, a.delay, a.jitter, stats, "down"), return_exceptions=True)
        print(f"proxy: connection closed (bytes up={stats['up']} down={stats['down']})", flush=True)

    srv = await asyncio.start_server(handle, "127.0.0.1", a.listen)
    print(f"proxy: 127.0.0.1:{a.listen} -> 127.0.0.1:{a.target}", flush=True)
    async with srv:
        await srv.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
