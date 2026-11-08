"""
Overlay — a small always-on-top window on the Windows desktop while the HUD tab is
not visible. Two looks (JARVIS_OVERLAY_STYLE):

  panda (default) a small robot panda standing free on the desktop, with a status pill
        under it: what Jarvis is doing, the last line, Stop and Mic. The panda acts out
        the state (mouth moves while he speaks, ears up while he hears you, head tilts
        while he thinks, sleeps on pause), and its face follows the tone of the answer
        (mood()). It follows the mouse with its eyes and is happy when clicked; five
        quick clicks make it spin.
  karte the older card: orb, one word, the last line, Stop and Mic.

Browsers only open a picture-in-picture window after a click (Opera ignores the
automatic route), so the window belongs to this server instead. The HUD reports
its state on every change and whether its tab is visible (POST /api/hud); the window
shows up when the tab is left and goes away on return. If the HUD stops reporting
(tab closed, browser gone) it disappears after STALE_S.

JARVIS_OVERLAY=0 switches it off. Standard library only (tkinter).
"""
import math
import os
import random
import threading
import time

import events

ENABLED = os.environ.get("JARVIS_OVERLAY", "1").strip().lower() not in ("0", "off", "false", "no", "aus")
STYLE = os.environ.get("JARVIS_OVERLAY_STYLE", "panda").strip().lower()
STALE_S = 90          # a hidden tab without Live may only run timers once a minute
W, H = 340, 124
LOOK = {
    "speaking":    ("SPRICHT", "#5fe4ff", 0.9),
    "hearing":     ("HÖRT DICH", "#2ed99b", 0.6),
    "listening":   ("HÖRT ZU", "#2ed99b", 0.25),
    "busy":        ("DENKT NACH", "#b98cff", 2.4),
    "sleeping":    ("BEREIT", "#2b8ba8", 0.12),
    "needs-click": ("KLICK NÖTIG", "#ffb340", 0.0),
    "blocked":     ("MIKRO AUS", "#ff6ba6", 0.0),
    "off":         ("PAUSE", "#6f8aa3", 0.08),
}
VISIBLE_S = 15        # a visible tab reports at least every 4 s; silent longer = closed without pagehide
STATE = {"visible": True, "st": "off", "detail": "", "line": "", "tone": "", "at": 0.0}
# Every HUD tab reports for itself (several can be open: a second window, a forgotten
# tab). The card shows only when no tab is visible, otherwise a hidden tab in the
# background would keep it up while you look at the HUD. {tab id: (visible, at)}
TABS = {}
_LOCK = threading.Lock()


def report(data):
    """From the HUD: {tab, visible, st, detail, line, tone, gone}. Cheap, no UI work on the request thread."""
    tab = str(data.get("tab") or "")[:40]
    now = time.time()
    with _LOCK:
        if data.get("gone"):                    # tab closed or reloaded
            TABS.pop(tab, None)
            return
        visible = bool(data.get("visible", True))
        TABS[tab] = (visible, now)
        for t, (_v, at) in list(TABS.items()):  # forget tabs that went quiet long ago
            if now - at > STALE_S:
                del TABS[t]
        # a hidden tab must not overwrite what the visible one shows
        if visible or not any(v for v, _at in TABS.values()):
            STATE.update({k: data[k] for k in ("visible", "st", "detail", "line", "tone") if k in data}, at=now)


def wanted():
    now = time.time()
    with _LOCK:
        if not ENABLED:
            return False
        fresh = [v for v, at in TABS.values() if now - at < (VISIBLE_S if v else STALE_S)]
        return bool(fresh) and not any(fresh)


def _tail(text, n=80):
    """The end of a long line, starting at a word — two lines fit on the card."""
    text = " ".join((text or "").split())
    if len(text) <= n:
        return text
    cut = text[-n:]
    return "… " + (cut.split(" ", 1)[1] if " " in cut else cut)


def _mix(hex_colour, f):
    """Colour towards the background, f = 0..1 brightness."""
    bg = (6, 11, 19)
    c = tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(int(b + (x - b) * f) for x, b in zip(c, bg))


def _window(tk, w, h):
    """A borderless, always-on-top tool window, bottom right above the taskbar."""
    root = tk.Tk()
    root.withdraw()
    root.overrideredirect(True)              # no title bar
    root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", 0.94)
        root.attributes("-toolwindow", True)  # no taskbar button
    except tk.TclError:
        pass
    root.configure(bg="#060b13")
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{sw - w - 18}+{sh - h - 58}")
    return root


def _show(root, want, shown):
    """Show or hide the window when the tab is left or entered."""
    if want != shown[0]:
        shown[0] = want
        (root.deiconify if want else root.withdraw)()
        if want:
            root.attributes("-topmost", True)


def _run():
    try:
        import tkinter as tk
    except ImportError:
        print("  Overlay: tkinter fehlt — kein Fenster beim Tab-Wechsel.", flush=True)
        return
    (_run_card if STYLE in ("karte", "card", "orb") else _run_panda)(tk)


# ── the card ─────────────────────────────────────────────────────────────────

def _run_card(tk):
    root = _window(tk, W, H)
    cv = tk.Canvas(root, width=W, height=H, bg="#060b13", highlightthickness=1,
                   highlightbackground="#1d4a5e", bd=0)
    cv.pack()

    # drag anywhere to move it
    drag = {}
    cv.bind("<ButtonPress-1>", lambda e: drag.update(x=e.x, y=e.y))
    cv.bind("<B1-Motion>", lambda e: root.geometry(f"+{root.winfo_x() + e.x - drag['x']}+{root.winfo_y() + e.y - drag['y']}"))

    def button(x, label, cmd, colour):
        tag = "btn_" + cmd
        cv.create_rectangle(x, H - 30, x + 62, H - 9, outline=_mix(colour, 0.7), fill=_mix(colour, 0.12),
                            width=1, tags=(tag, "ui"))
        cv.create_text(x + 31, H - 19, text=label, fill="#dbe9f5", font=("Segoe UI", 9, "bold"), tags=(tag, "ui"))
        # the HUD tab does the work, so Stop and Mic behave exactly like its own buttons
        cv.tag_bind(tag, "<ButtonRelease-1>", lambda e: events.publish("hud", cmd=cmd))

    angle = [0.0]
    last = [time.time()]
    shown = [False]

    def frame():
        now = time.time()
        want = wanted()
        _show(root, want, shown)
        if want:
            with _LOCK:
                st, detail, line = STATE["st"], STATE["detail"], STATE["line"]
            word, colour, spin = LOOK.get(st, LOOK["off"])
            angle[0] += (now - last[0]) * spin
            breathe = 0.5 + 0.5 * math.sin(now * 1.1)
            cv.delete("anim")
            cx, cy = 52, 60
            cv.create_oval(cx - 38, cy - 38, cx + 38, cy + 38, outline=_mix(colour, 0.3), tags="anim")
            for i, (r, length, w, dirn) in enumerate(((32, 70, 3, 1), (26, 50, 2, -1.6), (20, 35, 2, 2.3))):
                start = math.degrees(angle[0] * dirn) + i * 120
                for off in (0, 180):
                    cv.create_arc(cx - r, cy - r, cx + r, cy + r, start=start + off, extent=length * (1 if off == 0 else 0.6),
                                  style="arc", outline=_mix(colour, 0.95 - i * 0.2), width=w, tags="anim")
            core = 8 + (4 if st in ("speaking", "hearing") else 2) * breathe
            cv.create_oval(cx - core - 5, cy - core - 5, cx + core + 5, cy + core + 5, fill=_mix(colour, 0.25),
                           outline="", tags="anim")
            cv.create_oval(cx - core, cy - core, cx + core, cy + core, fill=_mix(colour, 0.9), outline="", tags="anim")
            cv.create_text(104, 18, anchor="w", text="J · A · R · V · I · S", fill="#6f8aa3",
                           font=("Consolas", 7), tags="anim")
            cv.create_text(104, 38, anchor="w", text=word, fill=colour, font=("Segoe UI", 15, "bold"), tags="anim")
            cv.create_text(104, 58, anchor="w", text=detail[:44], fill="#9fb4c8", font=("Segoe UI", 8), tags="anim")
            if line:
                cv.create_text(104, 68, anchor="nw", text=_tail(line), fill="#dbe9f5", width=W - 112,
                               font=("Segoe UI", 8), tags="anim")
            cv.tag_raise("ui")
        last[0] = now
        root.after(50 if want else 300, frame)

    button(104, "■ Stopp", "stop", "#ff6ba6")      # under the text column, the orb keeps the left
    button(174, "🎙 Mikro", "mic", "#5fe4ff")
    frame()
    root.mainloop()


# ── the panda ────────────────────────────────────────────────────────────────

PW, PH = 240, 170          # panda on top, status pill below
KEY = "#010203"            # painted in this colour = see-through, the panda stands free on the desktop
K = 0.7                    # drawing scale: the panda is drawn in units of a ~130 high figure
HX, HY = PW / 2, 54        # centre of the head in the window
PILL = (12, 118, PW - 12, PH - 6)
BUTTONS = (("stop", "■", "#ff6ba6", PW - 72), ("mic", "🎙", "#5fe4ff", PW - 42))   # round, like ChatGPT's
BR = 13                    # button radius
FUR, PATCH, VISOR, WARN = "#f2f2f2", "#1d1d1d", "#07111c", "#ffb340"

# The answer's tone tag ([dry], [amused], …, see commands.py) or its words set the face.
MOODS = (
    ("worried",   ("concern", "worr", "sigh", "sad", "sorry", "apolog", "caut", "alarm", "urgent", "grave"),
                  ("fehler", "warnung", "achtung", "leider", "kaputt", "fehlgeschlagen", "ausgefallen", "störung", "offline")),
    ("critical",  ("dry", "sarcas", "pointed", "skept", "wry", "deadpan", "critic", "doubt", "unimpress"), ()),
    ("happy",     ("amused", "pleased", "warm", "cheer", "laugh", "delight", "happy", "excit", "playful", "chuckl"), ()),
    ("proud",     ("satisf", "proud", "smug", "confident", "triumph"), ("erledigt", "geschafft", "fertig", "erfolgreich")),
    ("surprised", ("surpris", "astonish", "gasp", "impress", "amaz"), ()),
    ("serious",   ("firm", "serious", "measured", "stern", "crisp", "neutral", "calm", "whisper"), ()),
)
MOOD_HOLD_S = 8            # the face stays a while after he has finished speaking


def mood(tone, text=""):
    """The face for an answer: from its tone tag first, then from a few telling words."""
    tone, text = (tone or "").lower(), (text or "").lower()
    for name, tags, _words in MOODS:
        if any(t in tone for t in tags):
            return name
    for name, _tags, words in MOODS:
        if any(w in text for w in words):
            return name
    return None


def _oval(cx, cy, rx, ry, n=24):
    """Points along an ellipse — polygons can be tilted, canvas ovals cannot."""
    return [(cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n)) for i in range(n)]


def _rot(pts, cx, cy, deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return [(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c) for x, y in pts]


def _round(x0, y0, x1, y1, r):
    """A rounded rectangle for create_polygon(..., smooth=True), as points."""
    return [(x0 + r, y0), (x1 - r, y0), (x1, y0), (x1, y0 + r), (x1, y1 - r), (x1, y1),
            (x1 - r, y1), (x0 + r, y1), (x0, y1), (x0, y1 - r), (x0, y0 + r), (x0, y0)]


def _flat(pts):
    return [v for p in pts for v in p]


def _run_panda(tk):
    root = _window(tk, PW, PH)
    key = KEY
    try:
        root.attributes("-transparentcolor", KEY)   # Windows: clicks on the empty parts go to the desktop
    except tk.TclError:
        key = "#060b13"
    root.configure(bg=key)
    cv = tk.Canvas(root, width=PW, height=PH, bg=key, highlightthickness=0, bd=0)
    cv.pack()

    S = {"pet": -9.0, "pets": [], "spin": -9.0, "blink": time.time() + 3, "hearts": [],
         "ptr": (0, 0), "ptr_at": 0.0, "look": (0.0, 0.0), "look_at": 0.0, "press": None, "moved": False,
         "mood": None, "mood_until": 0.0}
    shown = [False]

    def on_panda(x, y):
        return abs(x - HX) < 40 and HY - 34 < y < PILL[1]

    def on_button(x, y):
        by = (PILL[1] + PILL[3]) / 2
        for cmd, _label, _colour, bx in BUTTONS:
            if (x - bx) ** 2 + (y - by) ** 2 <= (BR + 3) ** 2:
                return cmd
        return None

    def pet():
        now = time.time()
        S["pet"] = now
        S["pets"] = [t for t in S["pets"] if now - t < 3] + [now]
        if len(S["pets"]) >= 5:
            S["spin"], S["pets"] = now, []
        for i in range(3):
            S["hearts"].append([HX + random.uniform(-30, 30), HY - 20 + random.uniform(-6, 6), now + i * 0.13])

    # drag anywhere to move it; a click without moving pets the panda or presses a button
    def press(e):
        S["press"], S["moved"] = (e.x_root, e.y_root, root.winfo_x(), root.winfo_y()), False

    def motion(e):
        p = S["press"]
        if not p or (not S["moved"] and abs(e.x_root - p[0]) + abs(e.y_root - p[1]) < 5):
            return
        S["moved"] = True
        root.geometry(f"+{p[2] + e.x_root - p[0]}+{p[3] + e.y_root - p[1]}")

    def release(e):
        p, S["press"] = S["press"], None
        if not p or S["moved"]:
            return
        cmd = on_button(e.x, e.y)
        if cmd:   # the HUD tab does the work, so Stop and Mic behave exactly like its own buttons
            events.publish("hud", cmd=cmd)
        elif on_panda(e.x, e.y):
            pet()

    cv.bind("<ButtonPress-1>", press)
    cv.bind("<B1-Motion>", motion)
    cv.bind("<ButtonRelease-1>", release)

    def gaze(st, now, ex, ey):
        """Where the pupils go, in pixels from the eye's centre."""
        if st == "busy":                              # thinking: eyes circle
            return 2.2 * math.cos(now * 3), 1.6 * math.sin(now * 3)
        if st == "needs-click":
            return 0.0, 0.0
        px, py = root.winfo_pointerxy()
        if (px, py) != S["ptr"]:
            S["ptr"], S["ptr_at"] = (px, py), now
        if now - S["ptr_at"] > 8:                     # mouse idle: look around now and then
            if now - S["look_at"] > random.uniform(2.5, 5):
                S["look"], S["look_at"] = (random.uniform(-2, 2), random.uniform(-1.5, 1.5)), now
            return S["look"]
        dx, dy = px - (root.winfo_rootx() + ex), py - (root.winfo_rooty() + ey)
        d = math.hypot(dx, dy) or 1
        k = min(2.4, d / 40) / d
        return dx * k, dy * k * 0.8

    def frame():
        now = time.time()
        want = wanted()
        _show(root, want, shown)
        if not want:
            root.after(300, frame)
            return
        with _LOCK:
            st, detail, line, tone = STATE["st"], STATE["detail"], STATE["line"], STATE["tone"]
        word, colour, _spin = LOOK.get(st, LOOK["off"])
        if st == "speaking":                          # the face follows the answer and lingers after it
            S["mood"], S["mood_until"] = mood(tone, line), now + MOOD_HOLD_S
        face = S["mood"] if now < S["mood_until"] and st not in ("off", "hearing") else None
        cv.delete("all")

        happy = now - S["pet"] < 1.6
        if happy:
            face = "happy"
        asleep = st == "off" and not happy
        if now > S["blink"] + 0.14:
            S["blink"] = now + random.uniform(2.5, 6)
        blinking = S["blink"] <= now and not asleep
        glow_colour = WARN if face == "worried" else colour

        # body language
        breathe = math.sin(now * (1.4 if asleep else 2.0))
        hop = 6 * abs(math.sin(math.pi * (now - S["pet"]) / 0.4)) if now - S["pet"] < 0.8 else 0
        if face == "surprised" and now - S["mood_until"] + MOOD_HOLD_S < 0.4:
            hop = max(hop, 4)
        sx, sy = 1 - 0.012 * breathe, 1 + 0.02 * breathe
        t_spin = now - S["spin"]
        facing = True
        if t_spin < 1.0:
            c = math.cos(2 * math.pi * t_spin)
            sx, facing = sx * max(abs(c), 0.08), c > 0
        tilt = {"busy": 9 * math.sin(now * 1.3), "hearing": -6, "needs-click": 12}.get(st, 0)
        tilt = {"critical": -10, "worried": 6}.get(face, tilt)
        if asleep:
            tilt = 8
        if face == "proud":
            tilt = 4 * math.sin(now * 5)              # a contented little nod
        perk = {"hearing": 6, "listening": 3, "speaking": 1}.get(st, 0) + (5 if face == "surprised" else 0)
        wave = 40 * math.sin(now * 9) ** 2 if happy else (14 * math.sin(now * 7) if st == "speaking" else 0)

        feet = 78                                     # body space: head centre at 0,0, feet at y=78

        def tb(pts):        # body space → window, breathing round the feet
            return [(HX + x * K * sx, HY - hop + (feet + (y - feet) * sy) * K) for x, y in pts]

        def th(pts):        # head space: tilted round the neck, then like the body
            return tb(_rot(pts, 0, 30, tilt))

        def poly(pts, fill, outline="", width=1, smooth=False):
            cv.create_polygon(_flat(pts), fill=fill, outline=outline, width=width, smooth=smooth)

        edge = _mix(colour, 0.5)
        # arms behind the body, waving round the shoulders
        for side in (-1, 1):
            arm = _rot(_oval(side * 26, 50, 8, 15), side * 20, 38, -side * wave)
            poly(tb(arm), PATCH, edge)
        poly(tb(_oval(0, 52, 27, 25)), FUR, edge)                           # body
        for side in (-1, 1):                                                # legs
            poly(tb(_oval(side * 14, 74, 10, 6)), PATCH)
        # the arc reactor in its chest, pulsing with what Jarvis does
        glow = 0.55 + 0.45 * math.sin(now * (6 if st in ("speaking", "hearing") else 2))
        (cx, cy), = tb([(0, 50)])
        r = 3.5 + 1 * glow
        cv.create_oval(cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3, fill=_mix(glow_colour, 0.2 + 0.15 * glow), outline="")
        cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=_mix(glow_colour, 0.6 + 0.4 * glow), outline="")
        cv.create_oval(cx - 1.5, cy - 1.5, cx + 1.5, cy + 1.5, fill="#eafcff", outline="")

        # head: round black ears, white head, the dark visor where a panda has its eye patches
        for side in (-1, 1):
            poly(th(_oval(side * 27, -26 - perk, 11, 11)), PATCH, edge)
        poly(th(_oval(0, 0, 35, 32)), FUR, edge)
        if facing:
            poly(th(_round(-27, -14, 27, 13, 12)), VISOR, _mix(colour, 0.6), 1, smooth=True)
            for side in (-1, 1):
                (ex, ey), = th([(side * 12, -1)])
                er = 5.2 if st == "hearing" else 4.6
                if face == "surprised":
                    er = 5.8
                if face == "critical" and side == 1:
                    er = 3.8                                                # the doubting squint
                if blinking or asleep:                                      # closed: a line
                    cv.create_line(ex - er, ey, ex, ey + 1.5, ex + er, ey, fill=colour, width=2, smooth=True)
                elif face in ("happy", "proud"):                            # ^ ^
                    cv.create_line(ex - er, ey + 2, ex, ey - 3, ex + er, ey + 2, fill=colour, width=2.5,
                                   capstyle="round", joinstyle="round")
                else:
                    cv.create_oval(ex - er - 2, ey - er - 2, ex + er + 2, ey + er + 2, fill=_mix(colour, 0.22), outline="")
                    cv.create_oval(ex - er, ey - er, ex + er, ey + er, fill=colour, outline="")
                    gx, gy = gaze(st, now, ex, ey)
                    pr = 1.6 if face == "surprised" else 2.3
                    cv.create_oval(ex + gx - pr, ey + gy - pr, ex + gx + pr, ey + gy + pr, fill=VISOR, outline="")
                    cv.create_oval(ex + gx - 2, ey + gy - 2.6, ex + gx - 0.6, ey + gy - 1.2, fill="#ffffff", outline="")
                    lid = {"serious": 0.45, "critical": 0.3 if side == -1 else 0.5}.get(face, 0)
                    if not lid and st in ("blocked", "sleeping"):           # half lids: calm, or muted
                        lid = 0.35 if st == "sleeping" else 0.55
                    if lid:
                        cv.create_rectangle(ex - er - 2, ey - er - 2, ex + er + 2, ey - er + 2 * er * lid,
                                            fill=VISOR, outline="")
                # brows on the visor say most of the mood (x from the eye's centre; -side*7 is the inner end)
                brow = None
                if face == "critical":
                    brow = ((-6, -9), (7, -13)) if side == 1 else ((-7, -8), (7, -8))   # one brow up
                elif face == "worried":
                    brow = ((-side * 7, -12), (side * 7, -9))                        # inner ends up
                elif face == "serious":
                    brow = ((-7, -9), (7, -9))
                elif face == "surprised":
                    brow = ((-6, -12), (6, -12))
                if brow and not asleep:
                    (a, b), (c, d) = brow
                    pts = th([(side * 12 + a, b), (side * 12 + c, d)])
                    cv.create_line(_flat(pts), fill=colour, width=2, capstyle="round")
            # nose, and a mouth that speaks and shows the mood
            poly(th(_oval(0, 18, 4.5, 2.8, 12)), PATCH)
            talk = abs(math.sin(now * 11)) * abs(math.sin(now * 3.7)) if st == "speaking" else 0
            if talk > 0.15:
                poly(th(_oval(0, 25, 3.5, 1.5 + 3.5 * talk, 12)), "#7a2b3a", PATCH)
            elif face in ("happy", "proud") or happy:
                cv.create_line(_flat(th([(-6, 23), (0, 27), (6, 23)])), fill=PATCH, width=2, smooth=True)
            elif face == "worried":
                cv.create_line(_flat(th([(-5, 27), (0, 24), (5, 27)])), fill=PATCH, width=2, smooth=True)
            elif face == "surprised":
                poly(th(_oval(0, 25, 2.5, 3, 10)), PATCH)
            elif face == "critical":
                cv.create_line(_flat(th([(-5, 25), (5, 23)])), fill=PATCH, width=2)
            elif face == "serious":
                cv.create_line(_flat(th([(-5, 25), (5, 25)])), fill=PATCH, width=2)
            else:
                cv.create_line(_flat(th([(-3, 23), (0, 25), (3, 23)])), fill=PATCH, width=1.5, smooth=True)

        # what floats around the head
        (hx, hy), = th([(0, 0)])
        if st == "speaking":                                                # sound waves
            for i in range(3):
                on = (now * 3 - i * 0.33) % 1 < 0.6
                rr = 6 + i * 5
                cv.create_arc(hx + 34 - rr, hy + 6 - rr, hx + 34 + rr, hy + 6 + rr, start=-35, extent=70, style="arc",
                              outline=_mix(colour, 0.9 if on else 0.3), width=2)
        elif st == "hearing":                                               # sound coming in at the ears
            for side in (-1, 1):
                for i in range(2):
                    rr = 5 + ((now * 14 + i * 5) % 10)
                    cv.create_arc(hx + side * 42 - rr, hy - 20 - rr, hx + side * 42 + rr, hy - 20 + rr,
                                  start=(160 if side > 0 else -20), extent=40, style="arc",
                                  outline=_mix(colour, 1 - (rr - 5) / 12), width=2)
        elif st == "busy":                                                  # thinking dots
            for i in range(3):
                on = int(now * 3) % 3 == i
                cv.create_oval(hx + 26 + i * 8 - 2.5, hy - 38 - 2.5, hx + 26 + i * 8 + 2.5, hy - 38 + 2.5,
                               fill=_mix(colour, 1 if on else 0.35), outline="")
        elif st in ("needs-click", "blocked"):
            mark = "?" if st == "needs-click" else "!"
            cv.create_text(hx + 38, hy - 30 + 2 * math.sin(now * 4), text=mark, fill=colour,
                           font=("Segoe UI", 13, "bold"))
        if face == "worried" and st != "speaking":
            cv.create_oval(hx + 26, hy - 16, hx + 31, hy - 9, fill="#8fd3ff", outline="")   # a drop of sweat
        if asleep:                                                          # Zzz
            for i in range(3):
                p = (now * 0.5 + i / 3) % 1
                cv.create_text(hx + 30 + 16 * p, hy - 26 - 26 * p, text="z", fill=_mix("#9fb4c8", 1 - 0.6 * p),
                               font=("Segoe UI", int(7 + 5 * p), "bold"))
        hearts = []
        for h in S["hearts"]:
            age = now - h[2]
            if age < 0:
                hearts.append(h)
            elif age < 1.3:
                hearts.append(h)
                cv.create_text(h[0] + 6 * math.sin(age * 6), h[1] - 34 * age, text="♥", fill="#ff6ba6",
                               font=("Segoe UI", max(5, int(12 - 5 * age)), "bold"))
        S["hearts"] = hearts

        # status pill
        x0, y0, x1, y1 = PILL
        poly(_round(x0, y0, x1, y1, (y1 - y0) / 2), "#0b1622", _mix(colour, 0.55), 1, smooth=True)
        cv.create_text(x0 + 16, y0 + 6, anchor="nw", text=word, fill=colour, font=("Segoe UI", 9, "bold"))
        text = _tail(line, 50) if line else detail[:50]
        if text:
            cv.create_text(x0 + 16, y0 + 21, anchor="nw", text=text, fill="#cfdeea", width=BUTTONS[0][3] - BR - x0 - 24,
                           font=("Segoe UI", 7))
        by = (y0 + y1) / 2
        for cmd, label, bcolour, bx in BUTTONS:
            cv.create_oval(bx - BR, by - BR, bx + BR, by + BR, fill=_mix(bcolour, 0.14), outline=_mix(bcolour, 0.7))
            cv.create_text(bx, by, text=label, fill="#dbe9f5", font=("Segoe UI", 8, "bold"))
        root.after(40, frame)

    frame()
    root.mainloop()


def start():
    if ENABLED:
        threading.Thread(target=_run, daemon=True, name="overlay").start()
