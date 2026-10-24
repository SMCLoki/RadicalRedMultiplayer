# Radical Red multiplayer protocol (v1)

Transport: plain TCP, one JSON object per line (UTF-8, `\n` terminated, max 8 KiB per line).
mGBA 0.10 Lua only has raw TCP sockets (no WebSocket/TLS), so the wire format is kept trivial.

Every message has `t` (type). Client -> server messages may carry `n` (sequence number) when a reply is expected.

## Session

| Direction | Message | Notes |
|---|---|---|
| C -> S | `{"t":"hello","v":1,"name":"Red","room":"ABCD","game":"BPRE:<crc32>"}` | first message; must arrive within 10 s |
| S -> C | `{"t":"welcome","v":1,"id":3,"room":"ABCD","peers":[{"id":1,"name":"Blue"}]}` | `id` is unique per server run |
| S -> C | `{"t":"error","code":"...","msg":"..."}` then close | codes: `bad_version`, `bad_hello`, `room_full`, `game_mismatch`, `too_long`, `rate_limited`, `timeout` |
| S -> C | `{"t":"join","id":4,"name":"Green"}` | another client entered the room |
| S -> C | `{"t":"leave","id":4,"reason":"timeout"}` | reasons: `quit`, `timeout`, `error` |

`room` is 1-16 chars `[A-Za-z0-9_-]`; `name` is 1-12 printable chars (server trims/sanitises).
The first client sets the room's `game` string; later clients with a different `game` get `game_mismatch`.

## Liveness

| Direction | Message |
|---|---|
| C -> S | `{"t":"ping","n":17}` every 2 s |
| S -> C | `{"t":"pong","n":17}` |

The server drops a client that sends nothing for 10 s (`timeout`). The client treats 6 s without any server
traffic as a dead link, closes the socket and reconnects with backoff 1, 2, 4, 8, 10, 10... s.

## Relayed messages

Clients send these to the server; the server adds `from` (sender id) and forwards to every other client in the room.

| Type | Payload | Phase |
|---|---|---|
| `state` | arbitrary object, e.g. `{"t":"state","map":[3,0],"x":12,"y":8,"f":1}` | 2 (presence) |
| `chat` | `{"t":"chat","text":"hi"}` (max 120 chars) | 5 |
| `direct` | `{"t":"direct","to":4,...}` forwarded only to `to` | 3/4 (trade, battle) |

Rate limit: 40 relayed messages per second per client; excess -> `rate_limited` and disconnect.

## Versioning

`v` in `hello` must equal the server's protocol version. Breaking changes bump `v`; additive fields do not.
