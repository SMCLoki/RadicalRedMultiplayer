"""Radical Red Online launcher: pick your ROM, Host or Join, press Play.

- Host runs the relay server on this PC (port 8765 by default) and asks the router to forward the port (UPnP).
  Friends Join with the address the launcher shows.
- Play starts mGBA (a build with --script, i.e. 0.11 / development builds) with client/rr_mp.lua already loaded
  and passes name, room and server address in RRMP_* environment variables.
- The window then shows the game's live status (work/mp_status_<profile>.txt) and has a chat box
  (lines go to work/mp_cmd_<profile>.txt, which the client runs).

Run from source: py -3 launcher/rr_launcher.py      Build the .exe: tools/build_launcher.py
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import zlib
from pathlib import Path

APP = "Radical Red Online"
VERSION = "0.4.0"
DEFAULT_PORT = 8765
FROZEN = getattr(sys, "frozen", False)
BASE = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
CLIENT = BASE / "client"
WORK = BASE / "work"
SETTINGS = WORK / "launcher.json"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ------------------------------------------------------------------------------------------------
# helpers (no GUI)

def load_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(s: dict) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(s, indent=2), encoding="utf-8")


def check_rom(path: str) -> tuple[bool, str]:
    """FireRed (BPRE) v1.0 base, patched to Radical Red. Returns (ok, message)."""
    p = Path(path)
    if not p.is_file():
        return False, "ROM file not found"
    with p.open("rb") as f:
        head = f.read(0xC0)
    if len(head) < 0xC0 or head[0xAC:0xB0] != b"BPRE":
        return False, "not a FireRed-based ROM (game code is not BPRE)"
    if head[0xBC] != 0:
        return False, "Radical Red needs the FireRed v1.0 base; this ROM is v1.1"
    size = p.stat().st_size
    if size <= 16 * 1024 * 1024:
        return False, "this looks like unpatched FireRed (16 MB); patch it to Radical Red first"
    crc = 0
    with p.open("rb") as f:
        while chunk := f.read(1 << 20):
            crc = zlib.crc32(chunk, crc)
    return True, f"FireRed base, {size // (1024 * 1024)} MB, CRC32 {crc:08X} (everyone in a room needs the same ROM)"


def mgba_has_script_option(exe: str) -> bool:
    try:
        out = subprocess.run([exe, "--help"], capture_output=True, text=True, timeout=10,
                             creationflags=NO_WINDOW)
        return "--script" in (out.stdout + out.stderr)
    except (OSError, subprocess.SubprocessError):
        return False


def find_mgba(settings: dict) -> str | None:
    candidates = [settings.get("mgba") or "", str(BASE / "mgba" / "mGBA.exe")]
    candidates += [str(p) for p in sorted((WORK / "mgba-dev").glob("*/mGBA.exe"))]
    for c in candidates:
        if c and Path(c).is_file() and mgba_has_script_option(c):
            return c
    return None


def backup_save(rom: str, keep: int = 20) -> str | None:
    """Copy <rom>.sav to work/save_backups before every launch (trades change the save)."""
    sav = Path(rom).with_suffix(".sav")
    if not sav.is_file():
        return None
    dest_dir = WORK / "save_backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"{sav.stem}-{stamp}.sav"
    shutil.copy2(sav, dest)
    old = sorted(dest_dir.glob(f"{sav.stem}-*.sav"))
    for p in old[:-keep]:
        p.unlink(missing_ok=True)
    return str(dest)


def free_profile() -> int:
    """Same rule as rr_mp.lua: a profile is free when its lock is missing or older than 15 s."""
    now = time.time()
    for n in range(1, 9):
        try:
            t = float((WORK / f"mp_lock_{n}.txt").read_text().strip() or 0)
        except (OSError, ValueError):
            return n
        if now - t > 15:
            return n
    return 1


def parse_address(text: str) -> tuple[str, int]:
    text = text.strip()
    if not text:
        raise ValueError("enter the host's address")
    host, port = text, DEFAULT_PORT
    if text.count(":") == 1:
        host, p = text.split(":")
        port = int(p)
    if not host or not (0 < port < 65536):
        raise ValueError("address should look like 1.2.3.4 or 1.2.3.4:8765")
    return host, port


def server_ready(port: int) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def clean_line(text: str) -> str:
    return " ".join(text.split())[:200]


# ------------------------------------------------------------------------------------------------
# hosting: relay server subprocess + UPnP

def server_command(port: int) -> list[str]:
    # RRMP_BIND=127.0.0.1 keeps a test host on this PC only
    args = ["--run-server", "--host", os.environ.get("RRMP_BIND", "0.0.0.0"), "--port", str(port), "--data-dir", str(WORK / "server_data")]
    if FROZEN:
        return [sys.executable, *args]
    return [sys.executable, str(Path(__file__).resolve()), *args]


class Host:
    def __init__(self, port: int, on_info):
        self.port, self.on_info = port, on_info
        self.proc: subprocess.Popen | None = None
        self.gateway = None
        self.mapped = False
        self.log = None

    def start(self) -> None:
        WORK.mkdir(parents=True, exist_ok=True)
        self.log = open(WORK / "host_server.log", "ab")
        self.proc = subprocess.Popen(server_command(self.port), stdout=self.log, stderr=subprocess.STDOUT,
                                     creationflags=NO_WINDOW, cwd=str(BASE))
        threading.Thread(target=self._open_port, daemon=True).start()

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _open_port(self) -> None:
        import upnp
        if os.environ.get("RRMP_NO_UPNP") == "1":
            self.on_info(f"Server running on port {self.port} (UPnP skipped: RRMP_NO_UPNP=1).")
            return
        self.on_info(f"Server running on port {self.port}. Asking your router to open it (UPnP)...")
        try:
            gw = upnp.find_gateway()
            gw.add_mapping(self.port, f"{APP} {self.port}")
            self.gateway, self.mapped = gw, True
            ext = gw.external_ip()
            if not ext or upnp.is_private(ext):
                self.on_info(f"Port opened, but your router's internet address ({ext or 'unknown'}) is not public "
                             "(carrier-grade NAT / double router). Friends can't reach you directly: use Tailscale "
                             "and share your Tailscale IP instead.")
            else:
                self.on_info(f"Friends join with:  {ext}:{self.port}" if self.port != DEFAULT_PORT
                             else f"Friends join with:  {ext}")
        except Exception as e:  # noqa: BLE001 - shown to the user
            local = "this PC's address"
            try:
                import socket
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 53))   # no packet is sent; picks the outgoing interface
                local = s.getsockname()[0]
                s.close()
            except OSError:
                pass
            self.on_info(f"Could not open the port automatically ({e}). Forward TCP port {self.port} on your router "
                         f"to {local}, then share your public IP; or use Tailscale and share your Tailscale IP. "
                         f"Players on your home network can join with {local}.")

    def stop(self) -> None:
        if self.gateway and self.mapped:
            try:
                self.gateway.delete_mapping(self.port)
            except Exception:  # noqa: BLE001
                pass
            self.mapped = False
        if self.alive():
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.log:
            self.log.close()
            self.log = None


def run_server(argv: list[str]) -> None:
    if sys.stdout is None or sys.stderr is None:
        # the windowed .exe has no console streams: log to the file the launcher shows on errors
        WORK.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(WORK / "host_server.log", "a", buffering=1, encoding="utf-8")
    if not FROZEN:
        sys.path.insert(0, str(BASE / "server"))
    import rr_server
    rr_server.main(argv)


# ------------------------------------------------------------------------------------------------
# GUI

def main() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    settings = load_settings()
    root = tk.Tk()
    root.title(f"{APP} {VERSION}")
    root.minsize(560, 520)

    v_rom = tk.StringVar(value=settings.get("rom", ""))
    v_name = tk.StringVar(value=settings.get("name", ""))
    v_room = tk.StringVar(value=settings.get("room", "LOBBY"))
    v_mode = tk.StringVar(value=settings.get("mode", "join"))
    v_addr = tk.StringVar(value=settings.get("address", ""))
    v_port = tk.StringVar(value=str(settings.get("port", DEFAULT_PORT)))
    v_info = tk.StringVar(value="")
    v_rominfo = tk.StringVar(value="")
    state: dict = {"host": None, "game": None, "profile": None, "queue": []}

    frm = ttk.Frame(root, padding=12)
    frm.pack(fill="both", expand=True)
    frm.columnconfigure(1, weight=1)

    ttk.Label(frm, text="Radical Red ROM").grid(row=0, column=0, sticky="w")
    ttk.Entry(frm, textvariable=v_rom).grid(row=0, column=1, sticky="ew", padx=6)

    def browse():
        p = filedialog.askopenfilename(title="Your Radical Red ROM", filetypes=[("GBA ROM", "*.gba"), ("All", "*.*")])
        if p:
            v_rom.set(p)
            check()
    ttk.Button(frm, text="Browse...", command=browse).grid(row=0, column=2)
    ttk.Label(frm, textvariable=v_rominfo, foreground="#555").grid(row=1, column=1, columnspan=2, sticky="w", padx=6)

    def check():
        if v_rom.get():
            ok, msg = check_rom(v_rom.get())
            v_rominfo.set(("OK: " if ok else "Problem: ") + msg)

    ttk.Label(frm, text="Your name").grid(row=2, column=0, sticky="w", pady=(8, 0))
    ttk.Entry(frm, textvariable=v_name, width=14).grid(row=2, column=1, sticky="w", padx=6, pady=(8, 0))
    ttk.Label(frm, text="Room code").grid(row=3, column=0, sticky="w")
    ttk.Entry(frm, textvariable=v_room, width=18).grid(row=3, column=1, sticky="w", padx=6)
    ttk.Label(frm, text="everyone who plays together uses the same room code", foreground="#555").grid(
        row=4, column=1, columnspan=2, sticky="w", padx=6)

    mode = ttk.LabelFrame(frm, text="Connection", padding=8)
    mode.grid(row=5, column=0, columnspan=3, sticky="ew", pady=10)
    mode.columnconfigure(2, weight=1)
    ttk.Radiobutton(mode, text="Join a friend", variable=v_mode, value="join").grid(row=0, column=0, sticky="w")
    ttk.Label(mode, text="host address").grid(row=0, column=1, sticky="e", padx=(12, 4))
    ttk.Entry(mode, textvariable=v_addr).grid(row=0, column=2, sticky="ew")
    ttk.Radiobutton(mode, text="Host a game", variable=v_mode, value="host").grid(row=1, column=0, sticky="w")
    ttk.Label(mode, text="port").grid(row=1, column=1, sticky="e", padx=(12, 4))
    ttk.Entry(mode, textvariable=v_port, width=7).grid(row=1, column=2, sticky="w")
    ttk.Label(mode, textvariable=v_info, wraplength=500, foreground="#0a5").grid(
        row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

    play = ttk.Button(frm, text="Play")
    play.grid(row=6, column=0, columnspan=3, sticky="ew")

    status = tk.Text(frm, height=14, wrap="word", font=("Consolas", 9), state="disabled")
    status.grid(row=7, column=0, columnspan=3, sticky="nsew", pady=(10, 4))
    frm.rowconfigure(7, weight=1)
    chat = ttk.Frame(frm)
    chat.grid(row=8, column=0, columnspan=3, sticky="ew")
    chat.columnconfigure(0, weight=1)
    v_chat = tk.StringVar()
    chat_entry = ttk.Entry(chat, textvariable=v_chat)
    chat_entry.grid(row=0, column=0, sticky="ew")
    ttk.Label(frm, text="Chat, or a command like mp.battle(\"Ash\") / mp.who(). Trade and battle: walk up to a "
                        "player in the game and press A.", foreground="#555", wraplength=520).grid(
        row=9, column=0, columnspan=3, sticky="w")

    def set_status(text: str):
        status.configure(state="normal")
        status.delete("1.0", "end")
        status.insert("1.0", text)
        status.configure(state="disabled")

    def info(msg: str):
        root.after(0, v_info.set, msg)

    def send_chat(_evt=None):
        line = clean_line(v_chat.get())
        if not line or state["profile"] is None:
            return
        state["queue"].append(line if line.startswith("mp.") else "say " + line)
        v_chat.set("")
    chat_entry.bind("<Return>", send_chat)
    ttk.Button(chat, text="Send", command=send_chat).grid(row=0, column=1, padx=(6, 0))

    def flush_commands():
        prof = state["profile"]
        if prof is None or not state["queue"]:
            return
        path = WORK / f"mp_cmd_{prof}.txt"
        if path.exists():
            return   # the game hasn't picked up the last batch yet
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(state["queue"]) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        state["queue"].clear()

    def poll():
        prof = state["profile"]
        game = state["game"]
        if prof is not None:
            try:
                text = (WORK / f"mp_status_{prof}.txt").read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = "Starting the game..."
            if game is not None and game.poll() is not None:
                text = "The game was closed. Press Play to start it again.\n\n" + text
                state["game"] = None
                play.configure(state="normal")
            set_status(text)
            flush_commands()
        host = state["host"]
        if host is not None and not host.alive():
            v_info.set("The server stopped unexpectedly; see work/host_server.log")
            state["host"] = None
        root.after(1000, poll)

    def start():
        rom = v_rom.get().strip()
        ok, msg = check_rom(rom)
        if not ok:
            messagebox.showerror(APP, f"ROM problem: {msg}")
            return
        name = clean_line(v_name.get())[:12]
        room = "".join(ch for ch in v_room.get().strip() if ch.isalnum() or ch in "_-")[:16]
        if not name or not room:
            messagebox.showerror(APP, "Enter your name and a room code.")
            return
        try:
            port = int(v_port.get())
            if v_mode.get() == "join":
                host_addr, port = parse_address(v_addr.get())
            else:
                host_addr = "127.0.0.1"
        except ValueError as e:
            messagebox.showerror(APP, str(e))
            return
        mgba = find_mgba(settings)
        if not mgba:
            messagebox.showerror(APP, "mGBA with script support was not found. It should be in the 'mgba' folder "
                                      "next to this launcher (mGBA 0.11 or a development build from "
                                      "mgba.io/downloads.html).")
            return
        settings.update(rom=rom, name=name, room=room, mode=v_mode.get(), address=v_addr.get().strip(),
                        port=int(v_port.get() or DEFAULT_PORT), mgba=mgba)
        save_settings(settings)

        play.configure(state="disabled")
        if v_mode.get() == "host" and (state["host"] is None or not state["host"].alive()):
            state["host"] = Host(port, info)
            state["host"].start()
            set_status("Starting the server...")
            # the game does not retry a refused connection, so wait until the server accepts (the .exe can
            # take several seconds to unpack on first start)
            deadline = time.time() + 30

            def wait_server():
                if server_ready(port):
                    launch(mgba, rom, host_addr, port, room, name)
                elif time.time() > deadline or not state["host"].alive():
                    play.configure(state="normal")
                    set_status("The server did not start; see work/host_server.log")
                else:
                    root.after(300, wait_server)
            wait_server()
            return
        if v_mode.get() == "join":
            v_info.set(f"Joining {host_addr}:{port}, room {room}")
        launch(mgba, rom, host_addr, port, room, name)

    def launch(mgba, rom, host_addr, port, room, name):
        backup = backup_save(rom)
        prof = free_profile()
        env = dict(os.environ, RRMP_HOST=host_addr, RRMP_PORT=str(port), RRMP_ROOM=room, RRMP_NAME=name,
                   RRMP_PROFILE=str(prof))
        (WORK / f"mp_status_{prof}.txt").unlink(missing_ok=True)
        state["game"] = subprocess.Popen([mgba, "--script", str(CLIENT / "rr_mp.lua"), rom], env=env,
                                         cwd=str(Path(mgba).parent))
        state["profile"] = prof
        set_status("Starting the game..." + (f"\nSave backed up to {backup}" if backup else ""))

    play.configure(command=start)

    def on_close():
        if state["host"] is not None and state["host"].alive():
            if not messagebox.askyesno(APP, "You are hosting. Closing the launcher stops the server for everyone. "
                                            "Close anyway?"):
                return
            state["host"].stop()
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)

    check()
    if not v_name.get():
        v_name.set("Player")
    root.after(1000, poll)
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--run-server":
        run_server(sys.argv[2:])
    else:
        if not FROZEN:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
        main()
