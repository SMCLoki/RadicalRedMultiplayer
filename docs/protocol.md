# Radical Red Online protocol (v1)

Implemented by `server/rr_server.py` (server) and `client/rr_mp.lua` (mGBA client).
Tests: `server/test_server.py` (protocol), `tests/test_client.py` (Lua client end-to-end), `server/soak.py`.

Transport: plain TCP, one JSON object per line (UTF-8, `\n` terminated, max 8 KiB per line).
mGBA 0.10 Lua only has raw TCP sockets (no WebSocket/TLS), so the wire format is kept trivial.
Every message has `t` (type). Rooms hold **up to 8 players** (`--max-room`, hard cap 8).

## Errors

`{"t":"error","code":"...","msg":"...","fatal":true|false,"re":"<type that caused it>"}`

Fatal errors are followed by the server closing the socket. Errors before `welcome` are always fatal.

| code | fatal | meaning |
|---|---|---|
| `bad_version` | yes | `v` in hello is not the server's protocol version |
| `bad_hello` | yes | first message not hello, or bad room/name/uid/game |
| `room_full` | yes | room already has max players |
| `game_mismatch` | yes | room was created with a different ROM id |
| `replaced` | yes | same `uid` connected again; the newer connection wins |
| `too_long` | yes | line > 8 KiB (relayed payloads: > 1 KiB, non-fatal) |
| `rate_limited` | yes | more than 60 msg/s sustained (burst 120) |
| `timeout` | yes | no hello within 10 s, or nothing received for 10 s |
| `bad_msg`, `bad_type`, `no_peer`, `busy`, `bad_mon`, `bad_team`, `bad_state`, `bad_turn`, `no_trade`, `no_battle` | no | request refused; client should roll back its local step |

The Lua client does not auto-reconnect after `bad_version`, `bad_hello`, `room_full`, `game_mismatch`, `replaced`.

## Session

| Dir | Message | Notes |
|---|---|---|
| C→S | `{"t":"hello","v":1,"name":"Red","room":"ABCD","game":"AGB-BPRE:1a2b3c4d","uid":"0123456789abcdef"}` | first message, within 10 s |
| S→C | `{"t":"welcome","v":1,"id":3,"uid":"…","room":"ABCD","max":8,"peers":[{"id":1,"name":"Blue","state":{…}}]}` | `peers[].state` = their last presence |
| S→C | `{"t":"join","id":4,"name":"Green"}` | |
| S→C | `{"t":"leave","id":4,"reason":"quit"}` | reasons: `quit`, `timeout`, `error`, `replaced` |
| C→S | `{"t":"bye"}` | polite disconnect |

- `room`: 1-16 chars `[A-Za-z0-9_-]`. `name`: 1-12 printable chars (server trims). `game`: 1-64 printable non-space
  chars; the client sends `<game code>:<ROM CRC32 hex>`. The first client in a room sets its `game`.
- `uid`: 8-32 alphanumerics, generated once per client profile and stored in `work/mp_uid_<profile>.txt`.
  It is the player's identity for trade redelivery and battle resume. `id` is per connection.

## Liveness

`{"t":"ping","n":17}` → `{"t":"pong","n":17}`. The client pings every 2 s and treats 6 s without any server
traffic as a dead link; it reconnects with backoff 1, 2, 4, 8, 10, 10… s. The server drops a client after 10 s
of silence. Note: pausing the emulator stops the script, so a paused client times out (by design).

## Relayed messages

| Message | Notes |
|---|---|
| `{"t":"state","map":[3,0],"x":12,"y":8,"f":1,"b":0}` | presence. Server adds `from`, stores it for `welcome`, relays to the room. Client sends on change (sampled every 6 frames), at least once per second, max 15/s. `f` = facing (unverified, see ram_map.md), `b` = 1 in battle |
| `{"t":"chat","text":"hi"}` | server sanitises (printable, ≤120 chars) and sends `{"t":"chat","from":1,"name":"Red","text":"hi"}` to everyone incl. sender |
| `{"t":"direct","to":4,…}` | forwarded only to `to`, with `from` added |

## Trading (server-coordinated two-phase commit)

A Pokémon on the wire is its 100-byte party struct as 200 hex chars (Radical Red party structs are unencrypted).
Identity of a Pokémon = fingerprint `fp` = `%08x%08x` of (PID, OT ID).

```
A                          server                          B
trade_offer{to,slot,mon} →        → trade_offered{txn,from,name,mon,summary}
         ← trade_pending{txn,to}
                                  ← trade_answer{txn,slot,mon}
trade_preview{txn,mon} ←          → trade_preview{txn,mon}
trade_confirm{txn} →              → trade_confirmed{txn,by}
                                  ← trade_confirm{txn}
              [ledger append + fsync: COMMIT]
trade_commit{txn,slot,give,mon} ← → trade_commit{txn,slot,give,mon}
trade_done{txn,status} →          ← trade_done{txn,status}
```

- `trade_cancel{txn}` (either side) or a disconnect/120 s timeout **before commit** → `trade_abort{txn,reason}`
  to both; nothing changes anywhere.
- Server validation (`parse_mon`): 200 hex chars, PID/OTID not both 0, checksum field 0, species 1-2047,
  level 1-100, 0 < maxHP, HP ≤ maxHP, at least one move, moves ≤ 2047. One open trade per player; no trading
  while in a battle; no new trade while an earlier commit is unacknowledged.
- `trade_commit` is written to `server/data/trades.jsonl` (fsynced) **before** being sent, and re-sent on every
  `welcome` for that `uid` until the client answers `trade_done` (status `applied`, `missing` or `bad_mon`).
  This survives client crashes, network loss and server restarts.

### Trade safety on the client

1. On `trade_commit` (deferred while `gBattleTypeFlags != 0`): if the received `fp` is already in the party →
   just ack. Else find the given `fp` (at `slot`, or anywhere in the party). If missing → ack `missing`.
2. Append `{txn,slot,give,mon}` to `work/mp_trades_<profile>.jsonl` **first**, then overwrite the slot in RAM
   (the whole swap happens between two frames, so the game never sees a half-written struct), then ack.
3. **Reconcile**: on every `welcome` and every 5 s, for each journal entry: if `give` is in the party and the
   received `fp` is not, re-apply. This covers "game killed before the player saved" and "player reloaded an
   older save": the given Pokémon cannot come back. Entries are applied in order, so chains of trades work.

The in-game save is outside our control: after a trade the panel tells the player to save. Until they do,
the journal is what keeps both sides consistent.

## Battles (server-coordinated lockstep)

```
battle_req{to,rules} → server → battle_invite{bid,from,name,rules}     (challenger = side 0)
        ← battle_pending{bid,to}
battle_accept{bid} | battle_decline{bid}
server → both: battle_teamreq{bid}
both → battle_team{bid,party:[mon hex ×1-6]}
server → each: battle_start{bid,seed,side,turn:1,rules,party,opp:{id,name,party}}
each turn N:  both → battle_action{bid,turn:N,action:{…},hash:"…"}
              server → both: battle_turn{bid,turn:N,actions:[a0,a1]}
end:          both → battle_end{bid,winner:0|1|null}
server → both: battle_result{bid,winner,reason[,turn,hashes|reported]}
```

- `seed` is a server-chosen u32 both sides must load into the game RNG at battle start.
- `hash` (≤64 chars) is each client's hash of battle state at the start of turn N. If both are non-null and
  differ → `battle_result` reason `desync`. Disagreeing `battle_end` winners also → `desync`.
- `action` is opaque to the server (≤256 bytes JSON); the format is decided by the client battle engine.
- `rules` (≤256 bytes): `level_cap` is enforced by the server on `battle_team`; other keys are passed through.
- Reasons: `normal`, `forfeit` (`battle_forfeit{bid}`), `turn_timeout` (120 s; the side that did not act loses,
  both idle → no winner), `disconnect` (gone longer than 30 s grace), `desync`, `declined`, `cancelled`, `timeout`.
- Disconnect mid-battle: the opponent gets `battle_wait{bid,reason:"disconnected",grace}`. If the same `uid`
  returns within the grace period it gets `battle_resume` (= `battle_start` fields + `have_action`) followed
  by the last `battle_turn`; the opponent gets `battle_wait{reason:"resumed"}`. Re-sending the same action for
  the current turn is idempotent; an action for the previous turn just replays its `battle_turn`.
- Several battles can run at once in one room (e.g. 4 pairs in a full room). Results are logged to
  `server/data/battles.jsonl`.

## Versioning

`v` in `hello` must equal the server's protocol version. Breaking changes bump `v`; additive fields do not.
