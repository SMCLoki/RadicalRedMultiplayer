"""Build the player download: dist/RadicalRedOnline/ (launcher .exe + client scripts + mGBA) and a .zip of it.

    py -3 tools/build_launcher.py [--mgba <folder with mGBA.exe>]

Needs PyInstaller (py -3 -m pip install --user pyinstaller). The mGBA folder must be a build with --script
(0.11 / development build); by default the newest one under work/mgba-dev/ is used.
No ROMs or saves are ever copied.
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "launcher"))
from rr_launcher import VERSION, mgba_has_script_option  # noqa: E402

CLIENT_FILES = ["rr_mp.lua", "rr_visuals.lua", "rr_battle.lua"]
README = """Radical Red Online {version}
==============================

Play Pokemon Radical Red together over the internet: see each other walk around, chat, trade and battle.
No game files are included: you need your own Radical Red ROM (FireRed v1.0 base, patched).

1. Unzip this folder anywhere (e.g. your Desktop) and double-click RadicalRedOnline.exe.
2. Browse... to your Radical Red ROM. Your save (.sav next to the ROM) is used as-is and backed up to
   work\\save_backups every time you press Play.
3. Type your name and a room code. Everyone who plays together uses the same room code.
4. One player chooses "Host a game" and presses Play. The launcher shows the address to give to friends
   (it asks your router to open port 8765 automatically; if that fails it tells you what to do).
   Windows may ask whether to allow RadicalRedOnline through the firewall: allow it on private networks.
5. Everyone else chooses "Join a friend", types the host's address (or presses Find LAN games) and presses Play.

Can't open the host's router? Everyone installs Radmin VPN (free, radmin-vpn.com) and joins the same Radmin
network. The host's launcher then shows a 26.x.x.x address, and Find LAN games also searches Radmin.

In the game: walk up to another player and press A to trade (TRADE CENTER) or battle (COLOSSEUM).
In a battle, Run = give up. Chat from the box at the bottom of the launcher.
Keep the launcher open while you play; the host must keep it open for everyone.

Everyone in a room needs the same ROM (the launcher shows its CRC32 so you can compare).
mGBA (https://mgba.io, MPL 2.0) is included in the mgba folder with its license.
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mgba", help="folder containing an mGBA build with --script support")
    a = ap.parse_args()

    mgba_dir = Path(a.mgba) if a.mgba else max((ROOT / "work" / "mgba-dev").glob("*/"), default=None)
    if not mgba_dir or not (mgba_dir / "mGBA.exe").is_file():
        sys.exit("no mGBA folder found (pass --mgba)")
    if not mgba_has_script_option(str(mgba_dir / "mGBA.exe")):
        sys.exit(f"{mgba_dir} mGBA has no --script option; use a 0.11 / development build")

    build = ROOT / "build"
    dist = ROOT / "dist"
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed",
                    "--name", "RadicalRedOnline", "--paths", str(ROOT / "launcher"), "--paths", str(ROOT / "server"),
                    "--hidden-import", "rr_server", "--hidden-import", "upnp", "--hidden-import", "lan",
                    "--distpath", str(build / "exe"), "--workpath", str(build / "work"), "--specpath", str(build),
                    str(ROOT / "launcher" / "rr_launcher.py")], check=True)

    out = dist / "RadicalRedOnline"
    if out.exists():
        shutil.rmtree(out)
    (out / "client").mkdir(parents=True)
    shutil.copy2(build / "exe" / "RadicalRedOnline.exe", out / "RadicalRedOnline.exe")
    for f in CLIENT_FILES:
        shutil.copy2(ROOT / "client" / f, out / "client" / f)
    shutil.copytree(mgba_dir, out / "mgba", ignore=shutil.ignore_patterns("*.gba", "*.sav", "*.ss*", "config.ini",
                                                                           "qt.ini", "*.log", "*.sqlite3"))
    (out / "README.txt").write_text(README.format(version=VERSION), encoding="utf-8")
    zip_base = dist / f"RadicalRedOnline-{VERSION}-win64"
    archive = shutil.make_archive(str(zip_base), "zip", root_dir=dist, base_dir="RadicalRedOnline")
    print("built", out)
    print("zip  ", archive, f"{Path(archive).stat().st_size // (1024 * 1024)} MB")


if __name__ == "__main__":
    main()
