"""Radical Red Online one-click installer.

Built by tools/build_launcher.py --installer into RadicalRedOnline-Setup-<version>.exe, with the player package
(launcher, client scripts, mGBA) inside as payload.zip. Running it:
- installs to %LOCALAPPDATA%\\RadicalRedOnline (per user, no admin rights), keeping an existing work\\ folder
  (settings, save backups) when updating;
- adds Desktop and Start-menu shortcuts and an uninstaller;
- starts the launcher.
No ROM or save is included or touched.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

APP = "Radical Red Online"
EXE = "RadicalRedOnline.exe"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def install_dir() -> Path:
    base = os.environ.get("RRMP_INSTALL_DIR") or os.path.join(os.environ.get("LOCALAPPDATA", str(Path.home())),
                                                              "RadicalRedOnline")
    return Path(base)


def payload_path() -> Path:
    here = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return here / "payload.zip"


def close_running(target: Path) -> None:
    """Stop launcher/mGBA copies running from the install folder so their files can be replaced."""
    ps = ("Get-Process RadicalRedOnline,mGBA -ErrorAction SilentlyContinue | "
          f"Where-Object {{ $_.Path -like '{target}\\*' }} | Stop-Process -Force")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=NO_WINDOW, timeout=20)


def extract(zip_path: Path, target: Path) -> None:
    """Unpack the package into target. Everything is replaced except work\\ (the player's own data)."""
    target.mkdir(parents=True, exist_ok=True)
    for child in target.iterdir():
        if child.name.lower() == "work":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            parts = Path(info.filename).parts
            if len(parts) < 2 or ".." in parts:
                continue                              # the zip holds one top folder: RadicalRedOnline/...
            rel = Path(*parts[1:])
            if rel.parts[0].lower() == "work":
                continue
            dest = target / rel
            if info.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)


def shortcut_paths() -> list[Path]:
    if os.environ.get("RRMP_SHORTCUT_DIR"):          # tests: keep shortcuts out of the real Desktop
        return [Path(os.environ["RRMP_SHORTCUT_DIR"]) / f"{APP}.lnk"]
    appdata = os.environ.get("APPDATA", "")
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    start = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return [desktop / f"{APP}.lnk", start / f"{APP}.lnk"]


def make_shortcut(link: Path, target_exe: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:RR_LINK); "
          "$s.TargetPath = $env:RR_TARGET; $s.WorkingDirectory = $env:RR_DIR; "
          "$s.IconLocation = $env:RR_TARGET + ',0'; $s.Save()")
    env = dict(os.environ, RR_LINK=str(link), RR_TARGET=str(target_exe), RR_DIR=str(target_exe.parent))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, creationflags=NO_WINDOW, timeout=30,
                   check=True)


UNINSTALL_BAT = r"""@echo off
rem Removes Radical Red Online (the program and its shortcuts). Your ROM and its .sav are never touched.
echo This removes Radical Red Online, including its settings and save backups in:
echo   %~dp0work
choice /m "Continue"
if errorlevel 2 exit /b
taskkill /im RadicalRedOnline.exe /f >nul 2>nul
del "%USERPROFILE%\Desktop\Radical Red Online.lnk" >nul 2>nul
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Radical Red Online.lnk" >nul 2>nul
cd /d "%TEMP%"
rmdir /s /q "%~dp0" & exit /b
"""


def install(log=print) -> Path:
    target = install_dir()
    log(f"Installing to {target} ...")
    close_running(target)
    extract(payload_path(), target)
    (target / "Uninstall.bat").write_text(UNINSTALL_BAT, encoding="ascii")
    exe = target / EXE
    for link in shortcut_paths():
        try:
            make_shortcut(link, exe)
        except (OSError, subprocess.SubprocessError) as e:
            log(f"(could not create shortcut {link.name}: {e})")
    return exe


def main() -> None:
    if "--quiet" in sys.argv:          # for tests: install without a window and without starting the launcher
        install()
        return
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(f"{APP} setup")
    root.geometry("420x150")
    msg = tk.StringVar(value="Installing...")
    ttk.Label(root, textvariable=msg, wraplength=390, padding=14).pack(fill="x")
    bar = ttk.Progressbar(root, mode="indeterminate")
    bar.pack(fill="x", padx=14)
    bar.start(12)

    def work():
        try:
            exe = install(lambda m: root.after(0, msg.set, m))
        except Exception as e:  # noqa: BLE001 - shown to the user
            root.after(0, done, None, f"Install failed: {e}")
            return
        root.after(0, done, exe, None)

    def done(exe, error):
        bar.stop()
        if error:
            msg.set(error)
            return
        msg.set(f"Installed. Starting {APP}...\nNext time use the '{APP}' shortcut on your Desktop.\n"
                "You still need your own Radical Red ROM (Browse... in the launcher).")
        subprocess.Popen([str(exe)], cwd=str(exe.parent))
        root.after(4000, root.destroy)

    threading.Thread(target=work, daemon=True).start()
    root.mainloop()


if __name__ == "__main__":
    main()
