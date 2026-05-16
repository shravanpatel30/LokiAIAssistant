"""
Scans the system for installed apps and writes apps.json.
Sources:
  1. Start Menu shortcuts (.lnk files)
  2. Microsoft Store / modern apps (Get-StartApps)
  3. Windows Registry: App Paths
  4. Windows Registry: Uninstall keys
  5. Direct Program Files scan (fallback)
Re-run any time you install/uninstall something.
"""
import json
import os
import subprocess
import winreg
from pathlib import Path

OUTPUT_FILE = "apps.json"

START_MENU_DIRS = [
    Path(os.environ["PROGRAMDATA"]) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs",
]

PROGRAM_DIRS = [
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")),
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")),
    Path(os.environ["LOCALAPPDATA"]) / "Programs",
]

# Don't recurse into these — they're noisy and rarely contain user-launchable apps
SKIP_DIRS = {
    "common files", "windowsapps", "microsoft", "windows defender",
    "windows nt", "windows photo viewer", "windows mail", "windows media player",
    "internet explorer", "windows sidebar", "windows multimedia platform",
    "modifiablewindowsapps",
}


def _add(found, name, info, source):
    """Add to dict only if not already present (earlier sources win)."""
    name = name.lower().strip()
    if not name or name in found:
        return
    info["source"] = source
    found[name] = info


# ---- Source 1: Start Menu shortcuts ----
def find_lnk_targets():
    import win32com.client
    shell = win32com.client.Dispatch("WScript.Shell")
    found = {}
    for root in START_MENU_DIRS:
        if not root.exists():
            continue
        for lnk in root.rglob("*.lnk"):
            try:
                shortcut = shell.CreateShortcut(str(lnk))
                target = shortcut.TargetPath
                if target and target.lower().endswith(".exe") and Path(target).exists():
                    _add(found, lnk.stem, {"type": "exe", "path": target}, "start_menu")
            except Exception:
                continue
    return found


# ---- Source 2: Microsoft Store apps ----
def find_store_apps():
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            data = [data]
        found = {}
        for entry in data:
            name = entry.get("Name", "")
            appid = entry.get("AppID", "")
            if not name or not appid:
                continue
            if "!" in appid:
                _add(found, name, {"type": "store", "appid": appid}, "store")
        return found
    except Exception as e:
        print(f"  (Store scan failed: {e})")
        return {}


# ---- Source 3: Registry — App Paths ----
def find_app_paths():
    """HKLM/HKCU \\Software\\Microsoft\\Windows\\CurrentVersion\\App Paths"""
    found = {}
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    ]
    for hive, path in roots:
        try:
            with winreg.OpenKey(hive, path) as key:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(key, i)
                        i += 1
                    except OSError:
                        break
                    try:
                        with winreg.OpenKey(key, sub) as subkey:
                            exe_path, _ = winreg.QueryValueEx(subkey, "")
                            if exe_path and Path(exe_path).exists():
                                name = sub.replace(".exe", "")
                                _add(found, name, {"type": "exe", "path": exe_path}, "app_paths")
                    except (OSError, FileNotFoundError):
                        continue
        except FileNotFoundError:
            continue
    return found


# ---- Source 4: Registry — Uninstall keys ----
def find_uninstall_entries():
    """Catches almost any app installed via an MSI or standard installer."""
    found = {}
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, path in roots:
        try:
            with winreg.OpenKey(hive, path) as key:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(key, i)
                        i += 1
                    except OSError:
                        break
                    try:
                        with winreg.OpenKey(key, sub) as subkey:
                            try:
                                name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                            except FileNotFoundError:
                                continue
                            install_loc = ""
                            for val in ("InstallLocation", "DisplayIcon"):
                                try:
                                    install_loc = winreg.QueryValueEx(subkey, val)[0]
                                    if install_loc:
                                        break
                                except FileNotFoundError:
                                    continue
                            if not install_loc:
                                continue
                            # DisplayIcon often points right at an exe (e.g. RustDesk does this)
                            if install_loc.lower().endswith(".exe") and Path(install_loc.split(",")[0]).exists():
                                exe = install_loc.split(",")[0]
                                _add(found, name, {"type": "exe", "path": exe}, "uninstall")
                                continue
                            # Otherwise scan the install folder for a likely main exe
                            folder = Path(install_loc.strip('"'))
                            if folder.is_file():
                                folder = folder.parent
                            if not folder.exists():
                                continue
                            exe = _pick_main_exe(folder, name)
                            if exe:
                                _add(found, name, {"type": "exe", "path": str(exe)}, "uninstall")
                    except OSError:
                        continue
        except FileNotFoundError:
            continue
    return found


def _pick_main_exe(folder, app_name):
    """Heuristic: pick the .exe in folder whose name best matches app_name."""
    candidates = list(folder.glob("*.exe"))
    if not candidates:
        # Look one level deep
        candidates = list(folder.glob("*/*.exe"))
    if not candidates:
        return None
    # Prefer exes whose name contains a word from the app name
    words = [w.lower() for w in app_name.split() if len(w) > 2]
    for exe in candidates:
        stem = exe.stem.lower()
        if any(w in stem for w in words):
            return exe
    # Fallback: first exe alphabetically (deterministic)
    return sorted(candidates)[0]


# ---- Source 5: Direct Program Files scan ----
def find_program_files_exes():
    """Last-resort scan of common install roots."""
    found = {}
    for root in PROGRAM_DIRS:
        if not root.exists():
            continue
        for app_dir in root.iterdir():
            if not app_dir.is_dir():
                continue
            if app_dir.name.lower() in SKIP_DIRS:
                continue
            exe = _pick_main_exe(app_dir, app_dir.name)
            if exe:
                _add(found, app_dir.name, {"type": "exe", "path": str(exe)}, "program_files")
    return found


def main():
    print("Scanning Start Menu shortcuts...")
    s1 = find_lnk_targets()
    print(f"  {len(s1)} apps")

    print("Scanning Microsoft Store / modern apps...")
    s2 = find_store_apps()
    print(f"  {len(s2)} apps")

    print("Scanning registry App Paths...")
    s3 = find_app_paths()
    print(f"  {len(s3)} apps")

    print("Scanning registry Uninstall entries...")
    s4 = find_uninstall_entries()
    print(f"  {len(s4)} apps")

    print("Scanning Program Files directly...")
    s5 = find_program_files_exes()
    print(f"  {len(s5)} apps")

    # Merge: earlier sources take priority (Start Menu names tend to be cleanest)
    merged = {}
    for source in (s1, s2, s3, s4, s5):
        for name, info in source.items():
            if name not in merged:
                merged[name] = info

    # Friendly aliases
    simplifications = {
        "google chrome": "chrome",
        "mozilla firefox": "firefox",
        "visual studio code": "vscode",
        "microsoft edge": "edge",
        "windows powershell": "powershell",
        "file explorer": "explorer",
    }
    for long_name, short in simplifications.items():
        if long_name in merged and short not in merged:
            merged[short] = merged[long_name]

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)

    print(f"\nWrote {len(merged)} apps to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()