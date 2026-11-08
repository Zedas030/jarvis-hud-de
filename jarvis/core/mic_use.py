"""
Which programs are using the microphone right now — Windows' own privacy record, no audio is touched.

Windows notes every microphone use under HKCU\\...\\CapabilityAccessManager\\ConsentStore\\microphone: per program a
LastUsedTimeStart and a LastUsedTimeStop; a program that holds the microphone open has Stop = 0. Desktop programs
sit under "NonPackaged" (the key is the exe path with '#' for '\\'), Store apps (Teams, WhatsApp) directly below.

  in_use()    ["teams.exe", "zoom.exe", ...] — lower-case program names; [] on Linux or when nothing records

Used by the proactive mode to keep quiet during a call. On a Linux server this returns [] and the
do-not-disturb signal comes from Home Assistant instead (config/proactive.yaml → calls.entities).
"""
import sys

KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"


def _open_now(winreg, key):
    try:
        stop, _ = winreg.QueryValueEx(key, "LastUsedTimeStop")
        start, _ = winreg.QueryValueEx(key, "LastUsedTimeStart")
    except OSError:
        return False
    return bool(start) and stop == 0


def _children(winreg, key):
    i = 0
    while True:
        try:
            yield winreg.EnumKey(key, i)
        except OSError:
            return
        i += 1


def in_use():
    if sys.platform != "win32":
        return []
    import winreg
    found = []
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY)
    except OSError:
        return []
    with root:
        for name in list(_children(winreg, root)):
            try:
                sub = winreg.OpenKey(root, name)
            except OSError:
                continue
            with sub:
                if name == "NonPackaged":
                    for exe in list(_children(winreg, sub)):
                        try:
                            with winreg.OpenKey(sub, exe) as k:
                                if _open_now(winreg, k):
                                    found.append(exe.rsplit("#", 1)[-1].lower())
                        except OSError:
                            continue
                elif _open_now(winreg, sub):
                    found.append(name.split("_", 1)[0].lower())       # e.g. MSTeams_8wekyb3d8bbwe → msteams
    return found
