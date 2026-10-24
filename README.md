# Radical Red Online (work in progress)

Play Pokémon Radical Red together online: up to **8 players per room** see where everyone is, chat and
trade Pokémon. Other players on your map walk around as characters (take a step to see newcomers). PvP battles are being built (network part done, game part in progress).
It is a small server program plus a script that runs inside the mGBA emulator.
**No game files are included**: bring your own legally obtained Radical Red ROM.

Status: see `docs/progress.md`. Not yet tested inside the real emulator: treat as alpha.

## Quick start (Windows, about 10 minutes)

### What you need
1. **mGBA 0.10.5 or newer**: https://mgba.io/downloads.html (install to the default folder).
2. **Python 3.10 or newer**: https://www.python.org/downloads/ (only the person running the server needs it.
   During install, tick **"Add python.exe to PATH"**.)
3. Your **Radical Red ROM** (FireRed v1.0 base, sha1 `964f951a0fdaf209e4ea1344883ef0d557bb3a80`).
4. This folder: on GitHub click **Code → Download ZIP** and unzip it somewhere, e.g. your Desktop.

### Step 1: put your ROM in place
Make a folder called `work` inside this folder (if it doesn't already exist) and copy your ROM into it as
`work\rr.gba`. Use a **copy**: keep your original ROM and save files somewhere else as a backup.

### Step 2: start the server (one person does this)
Double-click **`start_server.bat`**. A black window opens and says `listening on 127.0.0.1:8765`.
Leave it open while you play.
- **Playing on one PC** (testing): nothing else to do.
- **Playing with friends over the internet**: the easiest way is a free VPN like
  [Tailscale](https://tailscale.com/download). Everyone installs it and joins the host's network. The host
  then runs `start_server.bat --host 0.0.0.0` and tells friends their Tailscale IP (looks like `100.x.y.z`).
  (Advanced: forward TCP port 8765 on your router instead.)

### Step 3: start the game and the script (every player)
1. Double-click **`start_client.bat`** (or open `work\rr.gba` in mGBA yourself). Load your save.
2. In mGBA open **Tools → Scripting…**, then in that window **File → Load script…** and pick
   **`client\rr_mp.lua`**. (Next time it is under *File → Load recent script*.)
3. A panel called **RR Online** appears in the scripting window, showing the other players.

### Step 4: settings (name, room, server address)
The first run creates `work\mp_config.txt`. Open it with Notepad:
```
host=127.0.0.1     <- the server's address (friends: the host's Tailscale IP)
port=8765
room=LOBBY         <- everyone who wants to play together uses the same room code
```
Or type into the scripting window's input box at the bottom: `mp.name("Ash")`, `mp.room("MYROOM")`,
`mp.connect("100.1.2.3")`. These are saved for next time.

## Trading and battling: just talk to them
Walk up to another player's character and press **A**. The game's own menus appear:
- **TRADE CENTER**: pick one of your Pokémon on the party screen; they get your offer. They talk to you, see what
  you offer and pick theirs; you talk to them once more to confirm. **Save your game afterwards.**
- **COLOSSEUM**: challenge them. They talk to you and accept, and the battle starts for both of you.
- Talk to them again while waiting to cancel your request.
The console commands below do the same things and are handy when the other player is on a different map.

## Commands (type into the box at the bottom of the scripting window)
| Command | What it does |
|---|---|
| `mp.help()` | list commands |
| `mp.who()` | who is in your room |
| `mp.say("hello")` | chat |
| `mp.party()` | show your party with slot numbers |
| `mp.trade("Misty", 2)` | offer your party slot 2 to Misty |
| `mp.accept(1)` | answer a trade offer with your slot 1 |
| `mp.confirm()` | confirm the trade shown in the panel (both players must confirm) |
| `mp.cancel()` / `mp.decline()` | cancel a trade / decline a trade or battle |
| `mp.battle("Brock")`, `mp.fight()`, `mp.forfeit()` | challenge / accept / give up a PvP battle (new, being tested) |
| `mp.visuals(false)` / `mp.visuals(true)` | hide / show other players as characters on your map |

**After a trade, save your game in the game's menu.** If the game closes before you save, the script
re-applies the trade the next time it connects, so no Pokémon is ever duplicated or lost.

## Two game windows on one PC
Copy the ROM again as `work\rr2.gba` (so the two windows don't share a save) and open it in a second mGBA.
Each window automatically gets its own player profile (`Player1`, `Player2`, …).

## Troubleshooting
- **Panel says `connect failed`**: is the server window open? Is `host` in `mp_config.txt` right?
  Friends: are you all on Tailscale?
- **`game_mismatch`**: someone in the room uses a different ROM version.
- **`room_full`**: rooms hold 8 players; use another room code.
- The emulator must not be paused: a paused game drops out after 10 seconds and rejoins when unpaused.

## For developers
- `server/rr_server.py`: relay server (Python stdlib only). `client/rr_mp.lua`: mGBA client.
- `docs/protocol.md`: wire protocol · `docs/ram_map.md`: verified RAM addresses · `docs/progress.md`: status ·
  `docs/local_checklist.md`: in-emulator verification steps · `docs/phase4_battle.md`: battle plan.
- Tests: `run_tests.bat` or `python server/test_server.py`, `python server/soak.py --minutes 1`,
  `python tests/test_client.py` (runs the Lua client in a fake mGBA; needs `pip install lupa`).
- Phase 0 tools: `client/probe.lua` (RAM verifier), `client/pilot.lua` (test autopilot), `client/nettest.lua`.
