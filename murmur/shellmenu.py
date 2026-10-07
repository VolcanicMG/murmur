"""Explorer right-click verb "Don't use murmur in this app" on .exe and .lnk files (HKCU\\Software\\Classes, no admin).
The verb runs `murmur --exclude <file>`: a second short-lived murmur process that toggles the file's program in [apps] exclude;
the running tray app notices the config change on its 3 s poll (app._watch_config) and shows the result on the card.
On Windows 11 the verb sits in the legacy menu ("Show more options" / Shift+right-click): the new menu only takes packaged apps.
sync(cfg) is called at tray start and after a Config save: [apps] shell_menu (default true) installs or removes the keys."""
import os, sys
from pathlib import PureWindowsPath

from . import store

VERB, LABEL = "murmur.exclude", "Don't use murmur in this app"
CLASSES = ("exefile", "lnkfile")


def _cmd():
    """Command line Explorer runs: the frozen exe, or this interpreter with -m murmur in a checkout."""
    exe = sys.executable
    return f'"{exe}" --exclude "%1"' if getattr(sys, "frozen", False) else f'"{exe}" -m murmur --exclude "%1"'


def enabled(cfg):
    return bool(cfg.get("apps", {}).get("shell_menu", True))


def installed():
    """True when the verb exists for .exe files (the .lnk key follows it)."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\exefile\shell\{VERB}\command"):
            return True
    except OSError:
        return False


def install():
    import winreg
    icon = sys.executable if getattr(sys, "frozen", False) else ""
    for cls in CLASSES:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{cls}\shell\{VERB}") as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, LABEL)
            if icon:
                winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, icon)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{cls}\shell\{VERB}\command") as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, _cmd())


def remove():
    import winreg
    for cls in CLASSES:
        for sub in (rf"Software\Classes\{cls}\shell\{VERB}\command", rf"Software\Classes\{cls}\shell\{VERB}"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except OSError:
                pass


def sync(cfg):
    """Make the registry match [apps] shell_menu. Rewrites the command each time so a moved exe keeps working. Windows only."""
    if os.name != "nt":
        return
    install() if enabled(cfg) else remove()


def target_name(path):
    """Lower-case program name for a file Explorer handed us: the .lnk target's exe name, else the file's own name."""
    p = PureWindowsPath(path)  # Explorer hands Windows paths; this also makes the name logic testable off Windows
    if p.suffix.lower() == ".lnk":
        try:
            import win32com.client
            tgt = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(p)).TargetPath
            if tgt:
                return PureWindowsPath(tgt).name.lower()
        except Exception:
            pass
    return p.name.lower()


def exclude_file(path):
    """The verb's action: toggle the file's program in [apps] exclude and save config.toml. Returns (name, now_excluded)."""
    name = target_name(path)
    cfg = store.load_config()
    ex = store.apps_exclude(cfg)
    ex = [a for a in ex if a != name] if name in ex else ex + [name]
    cfg.setdefault("apps", {})["exclude"] = ex
    store.save_config(cfg)
    return name, name in ex
