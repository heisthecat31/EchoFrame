"""EchoFrame (the EchoQuestXR patcher) window: a step-by-step guide from your Echo VR Quest APK to Echo running
on OpenXR, on a Meta Quest or a Steam Frame.

    pythonw gui.pyw      (double-click on Windows)

The steps (left) are pages: which headset, your APK, make the OpenXR version (patch.py; signed
with your own random key, reused for your updates), connect the headset, install Echo and its
game data, the Steam Frame's controls and sound (buttons.py), then play and save logs
(device.py). Drawn on one Canvas in EchoXR's installer style (palette and gradient from
EchoXR's installer/ui.h).
"""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import time
from tkinter import filedialog, messagebox

import buttons
import device
import patch

# palette (EchoXR installer/ui.h)
BG, SIDE, CARD, CARD_HI, BORDER = "#0e1015", "#12141a", "#171a21", "#1d212a", "#262b36"
TEXT, MUTED, FAINT = "#eef0f4", "#8e95a3", "#5a6170"
ACCENT, ACCENT2, GOOD, WARN, BAD = "#5b8cff", "#9a6bff", "#3ddc97", "#ffb547", "#ff5d6c"
W, H = 1000, 710
SIDE_W = 250
CX, CW = 290, 670            # the page's content column
TAGS = ("quest", "frame", "pick", "out", "go", "use", "skip", "folder", "details", "data", "connect", "getadb", "install",
        "launch", "logs", "back", "next", "advanced", "run", "q0", "q1", "q2", "q3", "q4", "step0", "step1", "step2", "step3", "step4", "step5", "step6")


def _version():
    """The release version: VERSION next to this file (packaged .exe) or at the repo root."""
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(here, "VERSION"), os.path.join(here, "..", "VERSION")):
        try:
            return "v" + open(p, encoding="utf-8").read().strip()
        except OSError:
            pass
    return ""


VERSION = _version()

if sys.platform == "win32":   # crisp on scaled displays
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def mix(a, b, t):
    a, b = int(a[1:], 16), int(b[1:], 16)
    ch = lambda s: int(((a >> s) & 255) + (((b >> s) & 255) - ((a >> s) & 255)) * t)
    return f"#{ch(16):02x}{ch(8):02x}{ch(0):02x}"


def _read(name, default=""):
    try:
        with open(os.path.join(device.DATA, name), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return default


def _write(name, text):
    try:
        os.makedirs(device.DATA, exist_ok=True)
        with open(os.path.join(device.DATA, name), "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.s = self.winfo_fpixels("1i") / 96.0   # logical px -> screen px
        self.title(f"EchoFrame {VERSION}".strip())
        here = os.path.dirname(os.path.abspath(__file__))
        icon = os.path.join(here, "echoframe.ico")
        if os.path.isfile(icon):
            try:
                self.iconbitmap(icon)
            except tk.TclError:
                pass
        self.logo = None   # the sidebar's icon (tools/make_icon.py), 44 px
        size = min((44, 55, 66, 88), key=lambda v: abs(v - self.px(44)))
        try:
            self.logo = tk.PhotoImage(file=os.path.join(here, f"echoframe-{size}.png"))
        except tk.TclError:
            pass
        self.configure(bg=BG)
        self.resizable(False, False)
        self.geometry(f"{self.px(W)}x{self.px(H)}")
        self.cv = tk.Canvas(self, width=self.px(W), height=self.px(H), bg=BG, highlightthickness=0, bd=0)
        self.cv.pack()
        self.f = {k: (fam, -self.px(size)) for k, (fam, size) in {
            "title": ("Segoe UI Semibold", 26), "h": ("Segoe UI Semibold", 15), "body": ("Segoe UI", 13),
            "small": ("Segoe UI", 11), "label": ("Segoe UI Semibold", 10), "btn": ("Segoe UI Semibold", 13),
            "step": ("Segoe UI Semibold", 12), "big": ("Segoe UI Semibold", 18), "mono": ("Consolas", 10)}.items()}

        # the guide
        self.step = 0
        self.headset = _read("headset") or None   # "quest" | "frame" (remembered)
        self.hot = None
        self.details = False
        # step 2-3: the APK and the patch
        self.apk = self.out = None
        self.verdict, self.verdict_ok = "", True
        self.state = "pick"           # pick -> ready -> working -> done | error
        self.message = ""
        self.result = None            # (out, key_path, fingerprint)
        self.skip_apk = False         # "Echo's already set up": past the APK steps, no APK to install
        self.built_for_frame = False
        self.progress = 0.0
        self.lines = []
        self.msgs = queue.Queue()
        # step 4-7: the headset
        self.adb = None
        self.devs = []                # [(serial, model, state)]
        self.dev_busy = False
        self.dev_msg, self.dev_tint = "", MUTED
        self.dev_progress = None      # 0..1 while game data is copied
        self.with_data = True         # Install also copies Echo's game data
        self.dev_frame = False        # the connected headset is a Steam Frame
        self.installed = False
        self.btn_map = buttons.load_local(device.DATA)   # Steam Frame controls and sound
        self.btn_panel = None
        self.addr = tk.Entry(self, bg=CARD_HI, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0,
                             highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
                             font=("Consolas", -self.px(14)))
        self.addr.insert(0, _read("frame-address"))
        self.addr.bind("<Return>", lambda e: self.click("connect"))
        # Advanced: a terminal on the headset (adb shell; on a Frame that's SteamOS)
        self.advanced = False
        self.term_proc = None
        self.history, self.hist_i = [], 0
        self.frame_serials = {}       # serial -> is a Steam Frame (asked once per headset)
        self.term_out = tk.Text(self, bg=CARD, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0, wrap="char",
                                highlightthickness=1, highlightbackground=BORDER, padx=self.px(10), pady=self.px(8),
                                font=("Consolas", -self.px(12)))
        self.term_out.tag_config("cmd", foreground=ACCENT)
        self.term_out.tag_config("note", foreground=FAINT)
        self.term_out.bind("<Key>", lambda e: None if (e.state & 4) else "break")   # read-only, but Ctrl+C copies
        self.term_in = tk.Entry(self, bg=CARD_HI, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0,
                                highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
                                font=("Consolas", -self.px(13)))
        self.term_in.bind("<Return>", lambda e: self.click("run"))
        self.term_in.bind("<Up>", lambda e: self.term_history(-1))
        self.term_in.bind("<Down>", lambda e: self.term_history(1))
        if self.headset:   # come back where it makes sense
            self.step = 1
        threading.Thread(target=self.poll_devices, daemon=True).start()
        if not device.find_adb():   # every headset action needs adb: install it right away
            self.dev_job(self.job_getadb)
        self.cv.bind("<Motion>", self.on_move)
        self.cv.bind("<Button-1>", self.on_click)
        self.after(50, self.tick)
        self.draw()

    # ------------------------------------------------------------------ drawing helpers
    def px(self, v):
        return int(round(v * self.s))

    def rrect(self, x, y, w, h, r, fill, outline=None, tags=(), width=1):
        x, y, w, h, r = (self.px(v) for v in (x, y, w, h, r))
        pts = [x + r, y, x + w - r, y, x + w, y, x + w, y + r, x + w, y + h - r, x + w, y + h,
               x + w - r, y + h, x + r, y + h, x, y + h, x, y + h - r, x, y + r, x, y]
        return self.cv.create_polygon(pts, smooth=True, fill=fill, outline=outline or fill, width=width, tags=tags)

    def pill_gradient(self, x, y, w, h, c1, c2, tags=()):
        """A horizontal gradient pill, drawn as one vertical line per screen column."""
        X, Y, Wp, Hp = self.px(x), self.px(y), self.px(w), self.px(h)
        r = Hp / 2
        for i in range(Wp):
            cx = i + 0.5
            d = 0.0
            if cx < r:
                d = r - (r * r - (r - cx) ** 2) ** 0.5
            elif cx > Wp - r:
                d = r - (r * r - (cx - (Wp - r)) ** 2) ** 0.5
            self.cv.create_line(X + i, Y + d, X + i, Y + Hp - d, fill=mix(c1, c2, i / max(1, Wp - 1)), tags=tags)

    def text(self, x, y, s, font="body", fill=TEXT, anchor="nw", width=None, tags=()):
        kw = {"width": self.px(width)} if width else {}
        return self.cv.create_text(self.px(x), self.px(y), text=s, font=self.f[font], fill=fill, anchor=anchor, tags=tags, **kw)

    def button(self, x, y, w, h, label, tag, primary=False, enabled=True):
        hot = enabled and self.hot == tag
        if primary and enabled:
            a, b = (mix(ACCENT, "#ffffff", .14), mix(ACCENT2, "#ffffff", .14)) if hot else (ACCENT, ACCENT2)
            self.pill_gradient(x, y, w, h, a, b, tags=(tag,))
            fg = "#ffffff"
        else:
            fill = (CARD_HI if hot else CARD) if enabled else "#2a2f3a"
            self.rrect(x, y, w, h, h / 2, fill, outline="#3a4150" if hot else BORDER, tags=(tag,))
            fg = TEXT if enabled else FAINT
        self.text(x + w / 2, y + h / 2, label, "btn", fg, anchor="center", tags=(tag,) if enabled else ())

    def link(self, x, y, label, tag, anchor="nw"):
        self.text(x, y, label, "small", ACCENT if self.hot == tag else mix(ACCENT, MUTED, .4), anchor=anchor, tags=(tag,))

    def status(self, x, y, line, tint, width=CW):
        self.cv.create_oval(self.px(x + 1), self.px(y + 5), self.px(x + 9), self.px(y + 13), fill=tint, outline=tint)
        self.text(x + 18, y, line, "small", tint, width=width - 18)

    def bullets(self, x, y, items, width=CW, gap=10):
        """Numbered instructions; returns the y under them."""
        for i, item in enumerate(items, 1):
            self.rrect(x, y + 1, 22, 22, 11, CARD_HI, outline=BORDER)
            self.text(x + 11, y + 12, str(i), "label", MUTED, anchor="center")
            t = self.text(x + 34, y + 2, item, "body", TEXT, width=width - 34)
            bbox = self.cv.bbox(t)
            y = (bbox[3] / self.s if bbox else y + 24) + gap
        return y

    def bar(self, x, y, w, frac):
        self.rrect(x, y, w, 8, 4, "#262b36")
        if frac is not None:
            self.pill_gradient(x, y, max(12, w * frac), 8, ACCENT, ACCENT2)

    # ------------------------------------------------------------------ the guide
    def steps(self):
        """(title, page) for each step; the Frame has one more."""
        s = [("Your headset", self.page_headset), ("Your Echo VR APK", self.page_apk),
             ("Make the OpenXR version", self.page_build), ("Connect the headset", self.page_connect),
             ("Install", self.page_install)]
        if self.headset == "frame":
            s.append(("Button layout", self.page_controls))
        s.append(("Play", self.page_play))
        return s

    def device_ready(self):
        dev = self.devs[0] if self.devs else None
        return bool(dev and dev[2] == "device"), dev

    def step_done(self, i):
        name = self.steps()[i][0]
        if name == "Your headset":
            return self.headset is not None
        if name == "Your Echo VR APK":
            return self.skip_apk or self.result is not None or (bool(self.apk) and self.verdict_ok is not None)
        if name == "Make the OpenXR version":
            return self.skip_apk or self.result is not None
        if name == "Connect the headset":
            return self.device_ready()[0]
        if name == "Install":
            return self.installed
        return False

    def can_next(self):
        name = self.steps()[self.step][0]
        if name in ("Your headset", "Your Echo VR APK", "Make the OpenXR version"):
            return self.step_done(self.step) and self.state != "working"
        if name == "Connect the headset":
            return self.device_ready()[0]
        return self.step < len(self.steps()) - 1

    def draw(self):
        cv = self.cv
        cv.delete("all")
        steps = self.steps()
        self.step = min(self.step, len(steps) - 1)
        # sidebar: logo and the steps
        cv.create_rectangle(0, 0, self.px(SIDE_W), self.px(H), fill=SIDE, outline=SIDE)
        if self.logo:
            cv.create_image(self.px(50), self.px(52), image=self.logo, anchor="center")
        else:
            self.pill_gradient(28, 30, 44, 44, "#4f7bff", "#9a5cff")
        self.text(84, 30, "EchoFrame", "h", TEXT)
        self.text(84, 54, VERSION or "Echo VR on OpenXR", "small", FAINT)
        y = 116
        for i, (title, _) in enumerate(steps):
            tag = f"step{i}"
            current, done = i == self.step, self.step_done(i)
            if current:
                self.rrect(16, y - 8, SIDE_W - 32, 44, 12, CARD_HI, outline=mix(ACCENT, CARD, .5), tags=(tag,))
            elif self.hot == tag:
                self.rrect(16, y - 8, SIDE_W - 32, 44, 12, CARD, tags=(tag,))
            dot = GOOD if done else (ACCENT if current else BORDER)
            cv.create_oval(self.px(32), self.px(y + 2), self.px(56), self.px(y + 26), fill=dot if (done or current) else SIDE,
                           outline=dot, width=self.px(2), tags=(tag,))
            self.text(44, y + 14, "✓" if done else str(i + 1), "label", BG if (done or current) else MUTED,
                      anchor="center", tags=(tag,))
            self.text(68, y + 14, title, "step", TEXT if current else (MUTED if done else FAINT), anchor="w", tags=(tag,))
            y += 52
        # Advanced, bottom left
        ay = H - 64
        if self.advanced:
            self.rrect(16, ay - 8, SIDE_W - 32, 44, 12, CARD_HI, outline=mix(ACCENT, CARD, .5), tags=("advanced",))
        elif self.hot == "advanced":
            self.rrect(16, ay - 8, SIDE_W - 32, 44, 12, CARD, tags=("advanced",))
        self.text(44, ay + 14, ">_", "label", ACCENT if self.advanced else MUTED, anchor="center", tags=("advanced",))
        self.text(68, ay + 14, "Advanced", "step", TEXT if self.advanced else MUTED, anchor="w", tags=("advanced",))

        if self.advanced:
            self.text(CX, 36, "ADVANCED", "label", FAINT)
            self.text(CX, 56, "Headset terminal", "title", TEXT)
            self.page_advanced()
            return

        # the page
        title, page = steps[self.step]
        self.text(CX, 36, f"STEP {self.step + 1} OF {len(steps)}", "label", FAINT)
        self.text(CX, 56, title, "title", TEXT)
        page()

        # back / next
        if self.step > 0:
            self.button(CX, H - 74, 120, 44, "Back", "back")
        if steps[self.step][0] == "Make the OpenXR version" and self.state not in ("done", "working"):
            # Make it sits where Next goes; once pressed, Next comes back (on when it's made)
            self.button(CX + CW - 160, H - 74, 160, 44, "Make it", "go", primary=True, enabled=bool(self.apk and self.out))
        elif self.step < len(steps) - 1:
            ok = self.can_next()
            name = steps[self.step][0]
            label = "Skip" if name == "Install" and not self.installed else "Next"
            self.button(CX + CW - 160, H - 74, 160, 44, label, "next", primary=ok and label == "Next", enabled=ok)

    # ------------------------------------------------------------------ pages
    def page_headset(self):
        self.text(CX, 108, "Which headset is Echo VR going on? The patcher makes the right version for it, and the "
                           "next steps show what to do on it.", "body", MUTED, width=CW)
        for i, (tag, name, lines) in enumerate((
                ("quest", "Meta Quest", "Quest 2, Quest 3, Quest Pro.\nConnect with a USB cable."),
                ("frame", "Steam Frame", "Runs in Lepton, the Frame's Android layer.\nConnect with a USB-C cable.")) ):
            x = CX + i * (CW // 2 + 8)
            w = CW // 2 - 8
            chosen = self.headset == tag
            hot = self.hot == tag
            self.rrect(x, 180, w, 190, 18, CARD_HI if (hot or chosen) else CARD,
                       outline=ACCENT if chosen else ("#3a4150" if hot else BORDER), tags=(tag,), width=2 if chosen else 1)
            self.cv.create_oval(self.px(x + 24), self.px(204), self.px(x + 48), self.px(228),
                                outline=ACCENT if chosen else MUTED, width=self.px(2), tags=(tag,))
            if chosen:
                self.cv.create_oval(self.px(x + 30), self.px(210), self.px(x + 42), self.px(222), fill=ACCENT, outline=ACCENT,
                                    tags=(tag,))
            self.text(x + 24, 246, name, "big", TEXT, tags=(tag,))
            self.text(x + 24, 284, lines, "body", MUTED, width=w - 48, tags=(tag,))
        if self.headset == "frame":
            self.text(CX, 396, "You'll need Developer Mode on the Frame and a USB-C cable. The patcher sets the rest "
                               "up itself.", "small", FAINT, width=CW)

    def page_apk(self):
        self.text(CX, 108, "Choose your own copy of the Echo VR Quest APK (your personalised one). The patcher reads it "
                           "and writes a new file next to it; the original isn't changed.", "body", MUTED, width=CW)
        hot = self.hot == "pick"
        self.rrect(CX, 186, CW, 150, 18, CARD_HI if hot else CARD,
                   outline=mix(ACCENT, CARD, .4) if self.apk else ("#3a4150" if hot else BORDER), tags=("pick",))
        if not self.apk:
            self.text(CX + CW / 2, 240, "Click to choose your Echo VR APK", "big", TEXT, anchor="center", tags=("pick",))
            self.text(CX + CW / 2, 276, "an .apk file", "small", FAINT, anchor="center", tags=("pick",))
        else:
            self.text(CX + 28, 210, "YOUR APK", "label", FAINT, tags=("pick",))
            self.text(CX + 28, 232, os.path.basename(self.apk), "h", TEXT, width=CW - 140, tags=("pick",))
            tint = GOOD if self.verdict_ok else (WARN if self.verdict_ok is False else BAD)
            self.status(CX + 28, 276, self.verdict, tint, width=CW - 56)
            self.text(CX + CW - 28, 210, "Change", "small", ACCENT, anchor="ne", tags=("pick",))
        # done this before: past the APK steps
        self.text(CX, 370, "DONE THIS BEFORE?", "label", FAINT)
        self.link(CX, 394, "Already made the OpenXR version? Use that file", "use")
        self.link(CX, 422, "Echo's already set up on the headset? Skip ahead to Connect", "skip")

    def page_build(self):
        frame = self.headset == "frame"
        self.text(CX, 108, "This makes the version of Echo that runs on OpenXR" +
                  (" on the Steam Frame: it also points Echo at a folder the Frame keeps, fixes Echo's Vulkan set-up for "
                   "the Frame's graphics driver, and stands in for Meta's Platform SDK." if frame else
                   ", the open VR standard your Quest supports.") + " It takes about a minute.",
                  "body", MUTED, width=CW)
        y = 210
        self.rrect(CX, y, CW, 70, 14, CARD, outline=BORDER)
        self.text(CX + 20, y + 14, "SAVED AS", "label", FAINT)
        self.text(CX + 20, y + 36, self.out or "", "small", TEXT, width=CW - 150)
        self.button(CX + CW - 110, y + 17, 90, 36, "Change", "out", enabled=self.state != "working")
        y = 318
        if self.state == "working":
            self.bar(CX, y, CW, self.progress)
            self.text(CX, y + 22, self.message or "Working...", "body", TEXT)
            below = y + 60
        elif self.state == "done":
            out, key_path, fp = self.result
            self.rrect(CX, y, CW, 128, 16, mix(GOOD, BG, .88), outline=mix(GOOD, BG, .6))
            self.cv.create_oval(self.px(CX + 20), self.px(y + 22), self.px(CX + 48), self.px(y + 50), fill=GOOD, outline=GOOD)
            self.text(CX + 34, y + 36, "✓", "h", BG, anchor="center")
            self.text(CX + 64, y + 20, "Done: " + os.path.basename(out), "h", TEXT, width=CW - 90)
            self.text(CX + 64, y + 50, ("Made for the Steam Frame. " if self.built_for_frame else "") +
                      "Signed with your own key (" + os.path.basename(key_path) + "): keep that file, and keep saving to "
                      "this same name, so updates install over Echo without deleting its data.", "small", MUTED, width=CW - 90)
            self.link(CX + 64, y + 100, "Open the folder", "folder")
            self.link(CX + 200, y + 100, "Make it again", "go")
            below = y + 146
        elif self.state == "error":
            self.rrect(CX, y, CW, 90, 16, mix(BAD, BG, .88), outline=mix(BAD, BG, .6))
            self.text(CX + 20, y + 16, "That didn't work", "h", BAD)
            self.text(CX + 20, y + 44, self.message, "small", TEXT, width=CW - 40)
            below = y + 108
        else:
            below = y
        if self.state not in ("done", "working"):
            self.link(CX, 288, "Already made one? Use that file", "use")
        self.details_box(below)   # right under the progress bar or the result

    def details_box(self, y):
        if not self.lines:
            return
        self.link(CX, y, "▾ Hide what happened" if self.details else "▸ Show what happened", "details")
        if self.details:
            self.rrect(CX, y + 22, CW, 92, 10, CARD, outline=BORDER)
            self.text(CX + 12, y + 30, "\n".join(self.lines[-5:]), "mono", MUTED, width=CW - 24)

    def page_connect(self):
        ok, dev = self.device_ready()
        if self.headset == "frame":
            self.text(CX, 108, "Connect the Frame to this PC:", "body", MUTED, width=CW)
            y = self.bullets(CX, 140, [
                "On the Frame: Steam Settings → System → turn on Developer Mode.",
                "Plug the Frame into this PC with a USB-C cable. It shows up below by itself.",
            ], gap=8)
            self.text(CX, y + 2, "No cable? Type the Frame's Wi-Fi adb address (IP:port) and press Connect:",
                      "small", MUTED, width=CW)
            y += 26
            self.cv.create_window(self.px(CX), self.px(y), window=self.addr, anchor="nw", width=self.px(CW - 170),
                                  height=self.px(40))
            self.button(CX + CW - 150, y, 150, 40, "Connect", "connect", primary=not ok, enabled=not self.dev_busy and bool(self.adb))
            y += 62
        else:
            self.text(CX, 108, "Connect the Quest to this PC with a USB cable:", "body", MUTED, width=CW)
            y = self.bullets(CX, 146, [
                "Turn on Developer Mode for the Quest (in the Meta Horizon app on your phone).",
                "Plug the Quest into this PC with a USB cable.",
                "Put the headset on and choose Allow on the \"Allow USB debugging?\" prompt "
                "(tick \"Always allow from this computer\").",
            ])
            y += 16
        # live status
        self.rrect(CX, y, CW, 60, 14, CARD, outline=BORDER)
        if not self.adb:
            line, tint = "Getting the connection tool (adb) ready...", MUTED
        elif ok:
            line, tint = f"Connected: {dev[1]}" + (" (Steam Frame)" if self.dev_frame else ""), GOOD
        elif dev:
            line, tint = f"{dev[1]}: {dev[2]}. Accept the prompt in the headset.", WARN
        else:
            line, tint = "Not connected yet.", MUTED
        self.status(CX + 20, y + 20, line, tint, width=CW - 40)
        if self.dev_msg:
            self.text(CX, y + 74, self.dev_msg, "small", self.dev_tint, width=CW)
        if not self.adb and not self.dev_busy:
            self.button(CX + CW - 130, y + 10, 110, 40, "Get adb", "getadb")

    def page_install(self):
        ok, dev = self.device_ready()
        frame = self.headset == "frame"
        self.text(CX, 108, "This puts your OpenXR version of Echo VR on the headset" +
                  ", and the game's data if the headset doesn't have it yet." +
                  (" The first time, it also installs Lepton (Valve's Android layer) from Steam, adds Echo VR to the "
                   "Frame's Steam library, and starts Echo once: keep the headset awake." if frame else ""),
                  "body", MUTED, width=CW)
        y = 170
        self.rrect(CX, y, CW, 112, 14, CARD, outline=BORDER)
        apk = self.installable()
        self.text(CX + 20, y + 16, "ECHO VR", "label", FAINT)
        self.text(CX + 20, y + 36, os.path.basename(apk) if apk else ("Already on the headset: game data only" if self.skip_apk
                                                       else "No APK made yet: game data only"), "small",
                  TEXT if apk else WARN, width=CW - 40)
        box = "☑" if self.with_data else "☐"
        self.text(CX + 20, y + 70, f"{box}  Also copy Echo's game data (about 900 MB, downloaded once; skipped if it's "
                                   "already there)", "small", ACCENT if self.hot == "data" else TEXT, tags=("data",),
                  width=CW - 40)
        y = 304
        self.button(CX, y, 240, 52, "Installing..." if self.dev_busy else "Install", "install", primary=True,
                    enabled=ok and not self.dev_busy and bool(apk or self.with_data))
        if not ok:
            self.text(CX + 264, y + 16, "Connect the headset first (step 4).", "small", WARN)
        y = 380
        if self.dev_progress is not None:
            self.bar(CX, y, CW, self.dev_progress)
            y += 22
        if self.dev_msg:
            self.text(CX, y, self.dev_msg, "body", self.dev_tint, width=CW)
        self.details_box(560)

    def page_controls(self):
        if not self.btn_panel:
            colors = dict(BG=BG, CARD=CARD, BORDER=BORDER, TEXT=TEXT, MUTED=MUTED, FAINT=FAINT, ACCENT=ACCENT,
                          GOOD=GOOD, WARN=WARN)
            self.btn_panel = buttons.ButtonsPanel(self.cv, colors, self.s, self.btn_map, self.buttons_changed,
                                                  self.buttons_save)
        ok, _ = self.device_ready()
        self.btn_panel.set_can_save(ok and not self.dev_busy)
        self.cv.create_window(self.px(CX + (CW - buttons.PANEL_W) / 2), self.px(110), window=self.btn_panel, anchor="nw")

    def page_play(self):
        ok, dev = self.device_ready()
        frame = self.headset == "frame"
        self.text(CX, 108, "Start Echo VR and put the headset on." +
                  (" On the Frame, Launch restarts Echo so it picks up anything you just installed or changed; you "
                   "can also start it from the Steam library." if frame else ""), "body", MUTED, width=CW)
        y = 186
        self.button(CX, y, 240, 52, "Launch Echo VR", "launch", primary=True, enabled=ok and not self.dev_busy)
        self.button(CX + 260, y, 200, 52, "Save logs", "logs", enabled=ok and not self.dev_busy)
        if not ok:
            self.text(CX, y + 70, "Connect the headset first (step 4).", "small", WARN)
        if self.dev_msg:
            self.text(CX, y + 70, self.dev_msg, "body", self.dev_tint, width=CW)
        self.rrect(CX, 340, CW, 190, 16, CARD, outline=BORDER)
        self.text(CX + 20, 356, "IF SOMETHING'S WRONG", "label", FAINT)
        self.bullets(CX + 20, 384, [
            "Play for a minute with the problem happening, then press Save logs and send the file.",
            "Updating later: make it again with the same file name (your key is reused), then Install.",
        ] + (["The Frame's Steam button and SteamOS keep their own controls; Echo's buttons are in step 6, Button layout."]
             if frame else []), width=CW - 40)

    def quick_commands(self):
        """(label, command) buttons over the terminal; on a Frame, $C is vrcmd and $K Echo's SteamVR app key."""
        if self.headset == "frame":
            return [("Unlock 72 fps", "$C --set-settings-int steamvr.powersaveFramesToThrottle=0"),
                    ("Frame rate settings", "for k in framesToThrottle powersaveFramesToThrottle motionSmoothing; "
                                            "do $C --settings-int steamvr.$k; done"),
                    ("Microphone volume", "pactl get-source-volume @DEFAULT_SOURCE@"),
                    ("Is Echo running?", "podman ps --format '{{.Names}} {{.Status}}' | grep lepton || echo not running")]
        return [("Is Echo running?", "pidof com.readyatdawn.r15 || echo not running"),
                ("Headset model", "getprop ro.product.model"),
                ("Free space", "df -h /sdcard | tail -1")]

    def page_advanced(self):
        ok, dev = self.device_ready()
        self.text(CX, 104, "Commands run straight on the headset" +
                  (" (SteamOS, as your user; $C is SteamVR's vrcmd)" if self.headset == "frame" else " (adb shell)") +
                  ". Only run commands you understand.", "small", MUTED, width=CW)
        x = CX
        for i, (label, cmd) in enumerate(self.quick_commands()):
            w = 24 + len(label) * 7.6
            self.button(x, 132, w, 30, label, f"q{i}", enabled=ok and not self.term_proc)
            x += w + 8
        self.cv.create_window(self.px(CX), self.px(176), window=self.term_out, anchor="nw", width=self.px(CW),
                              height=self.px(420))
        self.cv.create_window(self.px(CX), self.px(610), window=self.term_in, anchor="nw", width=self.px(CW - 130),
                              height=self.px(42))
        running = self.term_proc is not None
        self.button(CX + CW - 116, 610, 116, 42, "Stop" if running else "Run", "run", primary=not running,
                    enabled=running or ok)
        if not ok:
            self.text(CX, 664, "Connect the headset first (step 4).", "small", WARN)
        else:
            self.text(CX, 664, f"{dev[1]} ({dev[0]})  ·  Up/Down for earlier commands  ·  clear", "small", FAINT)
        if self.term_out.index("end-1c") == "1.0":
            self.term_out.insert("end", "Type a command below and press Enter, or use a button above.\n", "note")

    def term_history(self, d):
        if not self.history:
            return "break"
        self.hist_i = max(0, min(len(self.history), self.hist_i + d))
        self.term_in.delete(0, "end")
        if self.hist_i < len(self.history):
            self.term_in.insert(0, self.history[self.hist_i])
        return "break"

    def term_run(self, cmd):
        ok, dev = self.device_ready()
        cmd = cmd.strip()
        if not cmd or not ok or self.term_proc:
            return
        if not self.history or self.history[-1] != cmd:
            self.history.append(cmd)
        self.hist_i = len(self.history)
        self.term_in.delete(0, "end")
        if cmd in ("clear", "cls"):
            self.term_out.delete("1.0", "end")
            return
        self.term_out.insert("end", f"$ {cmd}\n", "cmd")
        self.term_out.see("end")
        self.term_proc = True   # busy until the thread has the process
        serial, adb = dev[0], self.adb
        def run():
            try:
                if serial not in self.frame_serials:
                    self.frame_serials[serial] = bool(device.frame_info(adb, serial))
                full = (device.FRAME_ENV + "; " + cmd) if self.frame_serials[serial] else cmd
                proc = device.shell_stream(adb, serial, full)
                self.term_proc = proc
                for line in proc.stdout:
                    self.msgs.put(("term", line))
                self.msgs.put(("term_end", proc.wait()))
            except Exception as e:
                self.msgs.put(("term", f"{type(e).__name__}: {e}\n"))
                self.msgs.put(("term_end", None))
        threading.Thread(target=run, daemon=True).start()

    def probe_frame(self, serial):
        """Is this newly connected headset (USB or Wi-Fi) a Steam Frame?"""
        try:
            is_frame = bool(device.frame_info(self.adb, serial))
        except Exception:
            return
        self.frame_serials[serial] = is_frame
        if is_frame:
            self.msgs.put(("frame", True))

    # ------------------------------------------------------------------ headset jobs
    def installable(self):
        if self.skip_apk:            # Echo's already on the headset: game data only
            return None
        if self.result:
            return self.result[0]
        return self.out if self.out and os.path.isfile(self.out) else None

    def poll_devices(self):
        while True:
            try:
                adb = device.find_adb()
                devs = device.devices(adb) if adb else []
                self.msgs.put(("devices", (adb, devs)))
            except Exception:
                pass
            time.sleep(2)

    def dev_job(self, fn, *args):
        self.dev_busy = True
        self.job_id = getattr(self, "job_id", 0) + 1
        me = self.job_id
        def run():
            try:
                fn(*args)
            except Exception as e:   # adb missing, device gone, network: say so
                self.msgs.put(("devmsg", (f"{type(e).__name__}: {e}", BAD)))
            self.msgs.put(("devdone", me))   # only the latest job clears "busy"
        threading.Thread(target=run, daemon=True).start()

    def job_getadb(self):
        self.msgs.put(("devmsg", ("Getting the connection tool (adb, Google's Android platform-tools)...", MUTED)))
        try:
            adb = device.download_adb(log=lambda m: None)
        except Exception as e:
            raise RuntimeError(f"couldn't get adb ({e}). Check the internet connection, then press Get adb.")
        self.msgs.put(("adb", adb))
        self.msgs.put(("devmsg", ("", MUTED)))

    def job_connect(self, target):
        self.msgs.put(("devmsg", (f"Connecting to {target}...", MUTED)))
        ok, out = device.connect(self.adb, target)
        if ok:
            serial = target.strip() if ":" in target else target.strip() + ":5555"
            if device.frame_info(self.adb, serial):
                self.msgs.put(("frame", True))
        self.msgs.put(("devmsg", ("Connected." if ok else f"Couldn't connect: {out}. Check the address, "
                                  "and that the Frame is awake.", GOOD if ok else BAD)))

    def job_install(self, serial, apk, with_data, allow_uninstall=False):
        if apk:
            self.msgs.put(("devmsg", (f"Installing {os.path.basename(apk)}... (about a minute)", MUTED)))
            if allow_uninstall:
                ok, out = device.uninstall(self.adb, serial)
                if not ok:
                    self.msgs.put(("devmsg", (f"Couldn't uninstall the old Echo VR: {out}", BAD)))
                    return
            def say(f, text):
                self.msgs.put(("devprogress", f))
                self.msgs.put(("devmsg", (text, MUTED)))
            kind, text = device.install(self.adb, serial, apk, say=say, log=lambda m: self.msgs.put(("log", m)))
            self.msgs.put(("devprogress", None))
            if kind == "signature":
                self.msgs.put(("ask_uninstall", (serial, apk, with_data)))
                return
            if kind != "ok":
                self.msgs.put(("devmsg", (f"Install failed: {text}", BAD)))
                return
        if with_data:
            self.job_data(serial)
        if device.frame_info(self.adb, serial):   # Steam Frame: its controls, sound and SteamVR settings too
            try:
                for line in device.write_buttons(self.adb, serial, buttons.to_text(self.btn_map)).splitlines():
                    self.msgs.put(("log", "SteamVR: " + line))
                self.msgs.put(("log", "Steam Frame settings: " + buttons.to_text(self.btn_map).replace("\n", " ")))
            except Exception as e:   # Echo's folder isn't there until it has started once
                self.msgs.put(("log", f"Steam Frame settings not saved: {e}"))
        self.msgs.put(("installed", True))
        self.msgs.put(("devmsg", ("Installed. Press Next.", GOOD)))

    def job_data(self, serial):
        def progress(f, text):
            self.msgs.put(("devprogress", f))
            self.msgs.put(("devmsg", (text, MUTED)))
        try:
            device.install_game_data(self.adb, serial, log=lambda m: self.msgs.put(("log", m)), progress=progress)
        finally:
            self.msgs.put(("devprogress", None))

    def job_launch(self, serial):
        ok, out = device.launch(self.adb, serial)
        self.msgs.put(("devmsg", ("Echo VR is starting: put the headset on." if ok else f"Couldn't start Echo: {out}",
                                  GOOD if ok else BAD)))

    def buttons_changed(self, m):
        self.btn_map = m
        buttons.save_local(device.DATA, m)

    def buttons_save(self, m):
        serial = self.devs[0][0] if self.devs else None
        if serial and not self.dev_busy:
            self.btn_panel.show_status("Saving...", MUTED)
            self.dev_job(self.job_buttons, serial, m)

    def job_buttons(self, serial, m):
        try:
            for line in device.write_buttons(self.adb, serial, buttons.to_text(m)).splitlines():
                self.msgs.put(("log", "SteamVR: " + line))
            self.msgs.put(("buttons_saved", (True, "Saved: Echo uses it the next time it starts (Launch).")))
        except Exception as e:
            self.msgs.put(("buttons_saved", (False, f"Couldn't save: {e}")))

    def job_logs(self, serial, path):
        n = device.save_logs(self.adb, serial, path)
        self.msgs.put(("devmsg", (f"Saved {n} log lines to {os.path.basename(path)}", GOOD)))

    # ------------------------------------------------------------------ events
    def item_tag(self, e):
        for item in reversed(self.cv.find_overlapping(e.x, e.y, e.x, e.y)):
            for t in self.cv.gettags(item):
                if t in TAGS:
                    return t
        return None

    def on_move(self, e):
        t = self.item_tag(e)
        if t != self.hot:
            self.hot = t
            self.cv.config(cursor="hand2" if t else "")
            self.draw()

    def on_click(self, e):
        self.click(self.item_tag(e))

    def click(self, t):
        if t is None:
            return
        steps = self.steps()
        working = self.state == "working"
        if t in ("quest", "frame") and not working:
            self.headset = t
            _write("headset", t)
            self.set_frame(t == "frame")
            self.step = 1
        elif t == "advanced":
            self.advanced = not self.advanced
            if self.advanced:
                self.after(10, self.term_in.focus_set)
        elif t == "run":
            if self.term_proc not in (None, True):
                try:
                    self.term_proc.kill()
                except OSError:
                    pass
            else:
                self.term_run(self.term_in.get())
        elif t[0] == "q" and t[1:].isdigit():
            cmds = self.quick_commands()
            if int(t[1:]) < len(cmds):
                self.term_run(cmds[int(t[1:])][1])
        elif t.startswith("step"):
            self.advanced = False
            i = int(t[4:])
            if i <= self.step or all(self.step_done(j) or steps[j][0] == "Install" for j in range(i)):
                self.step = i
        elif t == "back" and self.step > 0:
            self.step -= 1
        elif t == "next" and self.can_next():
            self.step += 1
        elif t == "pick" and not working:
            self.pick_apk()
        elif t == "out" and self.apk and not working:
            path = filedialog.asksaveasfilename(title="Save the OpenXR version as", defaultextension=".apk",
                                                initialdir=os.path.dirname(self.out), initialfile=os.path.basename(self.out),
                                                filetypes=[("Android app", "*.apk")])
            if path and os.path.abspath(path) != os.path.abspath(self.apk):
                self.out = path
        elif t == "go" and self.apk and self.out and not working:
            self.start()
        elif t == "use" and not working:
            path = filedialog.askopenfilename(title="Choose the OpenXR version you made earlier",
                                              filetypes=[("Android app", "*.apk")])
            if path:
                self.out = path
                key = os.path.splitext(path)[0] + ".signing-key.pem"
                self.result = (path, key, "")
                self.built_for_frame = path.endswith("_frame.apk") or self.headset == "frame"
                self.state = "done"
                self.skip_apk = False
                self.go_to("Connect the headset")
        elif t == "skip" and not working:   # Echo's on the headset already: Install only checks game data
            self.skip_apk = True
            self.result = None
            self.go_to("Connect the headset")
        elif t == "folder" and self.result and sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self.result[0])])
        elif t == "details":
            self.details = not self.details
        elif t == "data" and not self.dev_busy:
            self.with_data = not self.with_data
        elif t in ("getadb", "connect", "install", "launch", "logs") and not self.dev_busy:
            self.headset_click(t)
        self.draw()

    def go_to(self, name):
        names = [n for n, _ in self.steps()]
        if name in names:
            self.step = names.index(name)

    def set_frame(self, on):
        """The Steam Frame build or the Quest one, renaming the output to match."""
        if self.out and self.state != "done":
            stem, ext = os.path.splitext(self.out)
            stem = stem[:-len("_frame")] if stem.endswith("_openxr_frame") else stem
            self.out = stem + ("_frame" if on and stem.endswith("_openxr") else "") + ext

    def headset_click(self, t):
        if not self.adb:   # every headset action needs adb (it installs itself at startup)
            self.dev_job(self.job_getadb)
            return
        if t == "connect":
            target = self.addr.get().strip()
            if not target:
                self.dev_msg, self.dev_tint = "Plug the Frame in with a USB-C cable, or type its Wi-Fi adb address (like 192.168.1.50:5555).", WARN
                return
            _write("frame-address", target)
            self.dev_job(self.job_connect, target)
            return
        serial = self.devs[0][0] if self.devs else None
        if not serial:
            return
        if t == "install" and (self.installable() or self.with_data):
            if self.dev_frame and self.installable() and not self.built_for_frame and not messagebox.askyesno(
                    "EchoFrame", "This is a Steam Frame, but this APK was made for Quest, so Echo would look for its "
                    "game data where the Frame can't keep it.\n\nGo back to step 1, choose Steam Frame and make it "
                    "again. Install this one anyway?", icon="warning"):
                return
            self.dev_job(self.job_install, serial, self.installable(), self.with_data)
        elif t == "launch":
            self.dev_job(self.job_launch, serial)
        elif t == "logs":
            base = os.path.dirname(self.out) if self.out else os.path.expanduser("~")
            path = filedialog.asksaveasfilename(title="Save the EchoFrame log as", defaultextension=".txt",
                                                initialdir=base, initialfile=time.strftime("echoframe-log-%Y%m%d-%H%M%S.txt"),
                                                filetypes=[("Text", "*.txt")])
            if path:
                self.dev_job(self.job_logs, serial, path)

    def pick_apk(self):
        path = filedialog.askopenfilename(title="Choose your Echo VR Quest APK",
                                          filetypes=[("Android app", "*.apk"), ("All files", "*.*")])
        if not path:
            return
        self.apk = path
        self.skip_apk = False
        self.out = os.path.splitext(path)[0] + ("_openxr_frame.apk" if self.headset == "frame" else "_openxr.apk")
        self.state, self.lines, self.result = "ready", [], None
        try:
            self.verdict = patch.inspect(path)
            self.verdict_ok = "hasn't seen" not in self.verdict
        except patch.PatchError as e:
            self.verdict, self.verdict_ok = str(e), None
            self.apk = path   # keep it shown; Next stays off

    def start(self):
        self.state, self.message, self.progress, self.lines = "working", "Reading the APK...", 0.05, []
        self.draw()
        threading.Thread(target=self.work, args=(self.apk, self.out, self.headset == "frame"), daemon=True).start()

    def work(self, apk, out, for_frame=False):
        steps = {"Runtime": .2, "  added": .4, "  replaced": .5, "Generating": .6, "Reusing": .6, "Wrote": .95}
        def log(m):
            for k, v in steps.items():
                if m.startswith(k):
                    self.msgs.put(("progress", v))
            self.msgs.put(("log", m))
            if m.startswith(("Generating", "Reusing")):
                self.msgs.put(("message", "Signing..."))
        try:
            result = patch.patch(apk, out, log=log, frame=for_frame)
            self.msgs.put(("built_for_frame", for_frame))
            self.msgs.put(("done", result))
        except patch.PatchError as e:
            self.msgs.put(("error", str(e)))
        except Exception as e:   # anything unexpected: show it rather than vanish
            self.msgs.put(("error", f"{type(e).__name__}: {e}"))

    def tick(self):
        changed = False
        while not self.msgs.empty():
            kind, v = self.msgs.get()
            if kind == "devices":
                adb, devs = v
                if adb is None and self.adb and os.path.isfile(self.adb):
                    adb = self.adb   # a scan from before adb was installed: keep the new adb
                if (adb, devs) == (self.adb, self.devs):
                    continue        # nothing new: don't redraw every 2 s
                self.adb, self.devs = adb, devs
                for serial, _, state in devs:   # USB or Wi-Fi: find out once whether it's a Frame
                    if state == "device" and serial not in self.frame_serials:
                        self.frame_serials[serial] = None
                        threading.Thread(target=self.probe_frame, args=(serial,), daemon=True).start()
            changed = True
            if kind == "log":
                self.lines.append(v)
            elif kind == "progress":
                self.progress = v
            elif kind == "message":
                self.message = v
            elif kind == "done":
                self.state, self.result = "done", v
            elif kind == "error":
                self.state, self.message = "error", v
            elif kind == "buttons_saved":
                if self.btn_panel:
                    self.btn_panel.show_status(v[1], GOOD if v[0] else WARN)
            elif kind == "devprogress":
                self.dev_progress = v
            elif kind == "built_for_frame":
                self.built_for_frame = v
            elif kind == "installed":
                self.installed = True
            elif kind == "frame":   # connected to a Steam Frame
                self.dev_frame = True
                if self.headset != "frame":
                    self.headset = "frame"
                    _write("headset", "frame")
                    self.set_frame(True)
            elif kind == "devmsg":
                self.dev_msg, self.dev_tint = v
            elif kind == "adb":
                self.adb = v
            elif kind == "term":
                self.term_out.insert("end", v)
                self.term_out.see("end")
            elif kind == "term_end":
                self.term_proc = None
                if v:
                    self.term_out.insert("end", f"(exit code {v})\n", "note")
                self.term_out.see("end")
            elif kind == "devdone":
                if v == self.job_id:
                    self.dev_busy = False
            elif kind == "ask_uninstall":
                serial, apk, with_data = v
                if messagebox.askyesno("EchoFrame", "Echo VR on the headset is signed with a different key, so it "
                                       "has to be uninstalled first.\n\nUninstalling removes Echo VR's app data on the "
                                       "headset (settings and login). The game files in Android/media normally stay, "
                                       "but back them up first if you're unsure.\n\nUninstall Echo VR and install the "
                                       "new one?", icon="warning"):
                    self.dev_job(self.job_install, serial, apk, with_data, True)
                else:
                    self.dev_msg, self.dev_tint = "Not installed: the old Echo VR is still there.", WARN
        if changed:
            self.draw()
        self.after(50, self.tick)


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    if len(sys.argv) == 4 and sys.argv[1] == "--patch":   # headless: --patch <in.apk> <out.apk>
        try:
            patch.patch(sys.argv[2], sys.argv[3], log=lambda m: None)
        except Exception:
            sys.exit(1)
        sys.exit(0)
    App().mainloop()
