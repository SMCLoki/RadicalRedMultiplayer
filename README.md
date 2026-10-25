# Radical Red Online (work in progress)

Play Pokémon Radical Red together online: up to **8 players per room** see where everyone is, chat, trade
Pokémon and battle each other. Other players on your map walk around as characters.
It is a launcher with a small relay server plus a script that runs inside the mGBA emulator.
**No game files are included**: bring your own legally obtained Radical Red ROM.

Status: alpha, tested in real mGBA on one PC (see `docs/progress.md`).

## Easiest: the launcher (Windows)

1. Download `RadicalRedOnline-<version>-win64.zip` (from the releases page, or build it: `py -3 tools/build_launcher.py`),
   unzip it and double-click **RadicalRedOnline.exe**. mGBA is included (a development build, which can load the
   multiplayer script by itself).
2. **Browse...** to your Radical Red ROM, type your name and a room code.
3. **One player hosts** on their own PC (no server to rent, no account, no VPN or other outside service): choose
   *Host a game* and press **Play**. The launcher runs the relay server on that PC, asks the router to open port
   8765 (UPnP) and shows the address friends should use. If Windows asks about network access, allow it on
   **Private** networks.
4. **Friends** choose *Join a friend*, type the host's address and press **Play**. On the same network (same house /
   Wi-Fi) just press **Find LAN games** instead of typing anything.
5. The launcher shows who is online and has a chat box. Your `.sav` is backed up to `work\save_backups` on every Play.

Everyone in a room must use the same ROM (the launcher shows its CRC32 to compare).

### Playing over the internet: opening your router (host only, once)
Friends outside your home reach the host PC through the host's own router. The launcher tries to do this
automatically (UPnP). If it says *"Your router did not open the port automatically"*:
1. Open the router's admin page shown in the message (often `http://192.168.1.1`, `http://192.168.0.1` or
   `http://10.0.0.1`; some providers only offer their router app for this) and log in.
2. Either **turn on UPnP** (often under *Advanced*) and press **Retry router** in the launcher, or add a **port
   forward**: TCP port **8765** to the host PC's address (the launcher shows it, e.g. `10.0.0.199`). Reserve that
   address for the PC in the router's DHCP settings so it doesn't change.
3. Give friends your public address: the launcher shows it when UPnP works; otherwise it is on the router's
   status page ("WAN IP" / "Internet IP").

If the launcher says **carrier-grade NAT**, your internet provider doesn't give you your own internet address and
nobody can connect in: ask the provider for a public IPv4 address (often free), or let a friend whose connection
works host. If it says **double router**, the provider's modem sits in front of your router: put the modem in
bridge mode or forward the port on both. Players on your own network can always join, whatever the router does.
The room code works like a password: pick an unusual one if you host over the internet.

## Manual setup (any mGBA 0.10+, or to run the server yourself)

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
- **Playing with other PCs**: run `start_server.bat --host 0.0.0.0`. The window prints the address players on your
  network use (`players on your network connect to 192.168.x.y`). For friends over the internet, open TCP port
  8765 on your router as described in *Playing over the internet* above and give them your public address.

### Step 3: start the game and the script (every player)
1. Double-click **`start_client.bat`** (or open `work\rr.gba` in mGBA yourself). Load your save.
2. In mGBA open **Tools → Scripting…**, then in that window **File → Load script…** and pick
   **`client\rr_mp.lua`**. (Next time it is under *File → Load recent script*.)
3. A panel called **RR Online** appears in the scripting window, showing the other players.

### Step 4: settings (name, room, server address)
The first run creates `work\mp_config.txt`. Open it with Notepad:
```
host=127.0.0.1     <- the server's address (friends: the host's LAN or public address)
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
  Friends over the internet: is the port open on the host's router (see *Playing over the internet*)? Did the
  host allow the Windows firewall prompt? On the same network, try the host's LAN address first.
- **`server_busy`**: the host has too many connections from your address (12) or in total (64).
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
