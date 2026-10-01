"""EchoQuestXR patcher window: pick your Echo VR Quest APK, get one that runs on OpenXR.

    pythonw gui.pyw      (double-click on Windows)

Same patches as patch.py; every run signs with a new random key. Step 3 installs the
result on a connected headset, starts Echo and saves its logs (device.py). Drawn on one
Canvas in EchoXR's installer style (palette and gradient from EchoXR's installer/ui.h).
"""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import time
from tkinter import filedialog, messagebox, simpledialog

import device
import patch

# palette (EchoXR installer/ui.h)
BG, CARD, CARD_HI, BORDER = "#0e1015", "#171a21", "#1d212a", "#262b36"
TEXT, MUTED, FAINT = "#eef0f4", "#8e95a3", "#5a6170"
ACCENT, ACCENT2, GOOD, WARN, BAD = "#5b8cff", "#9a6bff", "#3ddc97", "#ffb547", "#ff5d6c"
W, H = 720, 756


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


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.s = self.winfo_fpixels("1i") / 96.0   # logical px -> screen px
        self.title(f"EchoQuestXR {VERSION}".strip())
        icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "echoquestxr.ico")
        if os.path.isfile(icon):
            try:
                self.iconbitmap(icon)
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
            "mono": ("Consolas", 10)}.items()}

        self.apk = self.out = None
        self.verdict, self.verdict_ok = "", True
        self.state = "pick"           # pick -> ready -> working -> done | error
        self.message = ""
        self.result = None            # (out, key_path, fingerprint)
        self.details = False
        self.hot = None
        self.progress = 0.0
        self.lines = []
        self.msgs = queue.Queue()
        # headset (step 3)
        self.adb = None
        self.devs = []                # [(serial, model, state)]
        self.dev_busy = False
        self.dev_msg, self.dev_tint = "", MUTED
        self.dev_progress = None      # 0..1 while game data is copied
        self.with_data = True         # Install also copies Echo's game data
        self.for_frame = False        # Steam Frame build: game data in Echo's private folder
        self.built_for_frame = False
        self.dev_frame = False        # the connected headset is a Steam Frame
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

    def rrect(self, x, y, w, h, r, fill, outline=None, tags=()):
        x, y, w, h, r = (self.px(v) for v in (x, y, w, h, r))
        pts = [x + r, y, x + w - r, y, x + w, y, x + w, y + r, x + w, y + h - r, x + w, y + h,
               x + w - r, y + h, x + r, y + h, x, y + h, x, y + h - r, x, y + r, x, y]
        return self.cv.create_polygon(pts, smooth=True, fill=fill, outline=outline or fill, width=1, tags=tags)

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
        self.text(x + w / 2, y + h / 2, label, "btn", fg, anchor="center", tags=(tag,))
        if enabled:
            self.cv.tag_bind(tag, "<Enter>", lambda e: self.cv.config(cursor="hand2"))
            self.cv.tag_bind(tag, "<Leave>", lambda e: self.cv.config(cursor=""))

    # ------------------------------------------------------------------ pages
    def draw(self):
        cv = self.cv
        cv.delete("all")
        hh = H + (110 if self.details and self.lines else 0)   # details push the footer down
        if int(cv.cget("height")) != self.px(hh):
            cv.config(height=self.px(hh))
            self.geometry(f"{self.px(W)}x{self.px(hh)}")
        # header glow
        for i in range(60):
            cv.create_line(0, self.px(i * 2.5), self.px(W), self.px(i * 2.5), fill=mix("#18214a", BG, i / 59), width=self.px(3))
        self.pill_gradient(32, 34, 56, 56, "#4f7bff", "#9a5cff")
        self.text(60, 62, "XR", "h", "#ffffff", anchor="center")
        self.text(104, 32, "EchoQuestXR", "title")
        self.text(105, 72, "Run the Echo VR Quest APK on OpenXR: Quest, Steam Frame and more.", "body", MUTED)

        # step 1: the APK (the whole card is the picker)
        y = 124
        hot = self.hot == "pick" and self.state != "working"
        self.rrect(32, y, W - 64, 104, 16, CARD_HI if hot else CARD, outline=mix(ACCENT, CARD, .4) if self.apk else BORDER, tags=("pick",))
        self.text(52, y + 18, "1   YOUR ECHO VR APK", "label", FAINT, tags=("pick",))
        if not self.apk:
            self.text(52, y + 44, "Click to choose your Echo VR Quest APK", "h", TEXT, tags=("pick",))
            self.text(52, y + 72, "Your own copy: nothing is uploaded, and the original file isn't changed.", "small", MUTED, tags=("pick",))
        else:
            self.text(52, y + 42, os.path.basename(self.apk), "h", TEXT, tags=("pick",))
            dot = GOOD if self.verdict_ok else WARN
            cv.create_oval(self.px(53), self.px(y + 77), self.px(61), self.px(y + 85), fill=dot, outline=dot, tags=("pick",))
            self.text(68, y + 72, self.verdict, "small", dot, width=W - 190, tags=("pick",))
            self.text(W - 52, y + 18, "Change", "small", ACCENT, anchor="ne", tags=("pick",))
        cv.tag_bind("pick", "<Enter>", lambda e: cv.config(cursor="hand2"))
        cv.tag_bind("pick", "<Leave>", lambda e: cv.config(cursor=""))

        # step 2: where it goes
        y = 240
        self.rrect(32, y, W - 64, 96, 16, CARD, outline=BORDER)
        self.text(52, y + 14, "2   SAVE AS", "label", FAINT)
        self.text(52, y + 36, self.out or "Chosen after you pick the APK", "small", TEXT if self.out else FAINT, width=W - 200)
        self.button(W - 140, y + 18, 88, 34, "Change", "out", enabled=bool(self.apk) and self.state != "working")
        box = "☑" if self.for_frame else "☐"
        self.text(52, y + 66, f"{box}  For Steam Frame: Echo reads its game data from its private folder "
                  f"({patch.FRAME_DATA_DIR})", "small",
                  ACCENT if self.hot == "frame" else (TEXT if self.for_frame else MUTED), tags=("frame",))

        # status / result area
        y = 352
        if self.state == "working":
            self.rrect(32, y, W - 64, 8, 4, "#262b36")
            fill = max(16, (W - 64) * self.progress)
            self.pill_gradient(32, y, fill, 8, ACCENT, ACCENT2)
            self.text(32, y + 20, self.message or "Patching...", "body", TEXT)
        elif self.state == "done":
            out, key_path, fp = self.result
            self.rrect(32, y, W - 64, 112, 16, mix(GOOD, BG, .88), outline=mix(GOOD, BG, .6))
            cv.create_oval(self.px(52), self.px(y + 20), self.px(80), self.px(y + 48), fill=GOOD, outline=GOOD)
            self.text(66, y + 34, "✓", "h", BG, anchor="center")
            self.text(94, y + 18, "Patched and signed" + (" for Steam Frame" if self.built_for_frame else ""), "h", TEXT)
            self.text(94, y + 46, "Install it on your headset below, or with adb yourself:", "small", MUTED)
            self.text(94, y + 68, f'adb install "{os.path.basename(out)}"', "mono", TEXT)
            self.text(94, y + 88, f"New signing key {fp[:16]}...  saved as {os.path.basename(key_path)} (keep it private)", "small", FAINT)
        elif self.state == "error":
            self.rrect(32, y, W - 64, 72, 16, mix(BAD, BG, .88), outline=mix(BAD, BG, .6))
            self.text(52, y + 14, "Couldn't patch", "h", BAD)
            self.text(52, y + 40, self.message, "small", TEXT, width=W - 104)
        else:
            self.text(32, y + 4, "Every patch is signed with a brand-new random key, so no two installs share one.", "small", MUTED)
            self.text(32, y + 24, "Android only updates an app signed with the same key: uninstall Echo VR before installing.", "small", MUTED)

        self.draw_headset(478)

        # details (log)
        if self.lines:
            self.text(32, 628, ("▾ Hide details" if self.details else "▸ Show details"),
                      "small", ACCENT if self.hot == "details" else MUTED, tags=("details",))
            if self.details:
                self.rrect(32, 652, W - 64, 100, 10, CARD, outline=BORDER)
                self.text(44, 660, "\n".join(self.lines[-5:]), "mono", MUTED, width=W - 88)

        # footer
        busy = self.state == "working"
        if self.state == "done":
            self.button(32, hh - 66, 150, 42, "Open folder", "folder")
            self.button(W - 32 - 200, hh - 70, 200, 48, "Patch another", "again", primary=True)
        else:
            self.button(W - 32 - 200, hh - 70, 200, 48, "Patching..." if busy else "Patch and sign", "go",
                        primary=True, enabled=bool(self.apk and self.out) and self.verdict_ok is not None and not busy)

    def installable(self):
        if self.result:
            return self.result[0]
        return self.out if self.out and os.path.isfile(self.out) else None

    def draw_headset(self, y):
        self.rrect(32, y, W - 64, 136, 16, CARD, outline=BORDER)
        self.text(52, y + 14, "3   HEADSET", "label", FAINT)
        dev = self.devs[0] if self.devs else None
        if not self.adb:
            line, tint = "Getting adb (Android platform-tools) ready...", MUTED
        elif not dev:
            line, tint = "No headset. Quest: plug in by USB and allow USB debugging. Steam Frame: Connect...", MUTED
        elif dev[2] != "device":
            line, tint = f"{dev[1]}: {dev[2]} (accept the USB debugging prompt in the headset)", WARN
        else:
            line, tint = f"{dev[1]} connected", GOOD
        self.cv.create_oval(self.px(53), self.px(y + 43), self.px(61), self.px(y + 51), fill=tint, outline=tint)
        buttons_left = W - 52 - (312 if dev else 140)
        self.text(68, y + 38, line, "small", tint, width=buttons_left - 84)
        if self.dev_msg:
            self.text(52, y + 72, self.dev_msg, "small", self.dev_tint, width=W - 104)
        if self.dev_progress is not None:
            self.rrect(52, y + 96, W - 104, 6, 3, "#262b36")
            self.pill_gradient(52, y + 96, max(10, (W - 104) * self.dev_progress), 6, ACCENT, ACCENT2)
        box = "☑" if self.with_data else "☐"
        self.text(52, y + 110, f"{box}  Install also copies Echo's game data (about 900 MB, downloaded once; "
                  "skipped if the headset already has it)", "small",
                  ACCENT if self.hot == "data" else (TEXT if self.with_data else MUTED), tags=("data",))
        ready = bool(dev and dev[2] == "device") and not self.dev_busy
        if not self.adb:   # installing itself (or failed: retry)
            self.button(W - 52 - 120, y + 30, 120, 38, "Get adb", "getadb", enabled=not self.dev_busy)
        elif not dev:   # nothing over USB: offer adb over the network (Steam Frame)
            self.button(W - 52 - 140, y + 30, 140, 38, "Connect...", "connect", enabled=not self.dev_busy)
        else:
            self.button(W - 52 - 312, y + 30, 100, 38, "Install", "install",
                        enabled=ready and bool(self.installable() or self.with_data))
            self.button(W - 52 - 204, y + 30, 92, 38, "Launch", "launch", enabled=ready)
            self.button(W - 52 - 104, y + 30, 104, 38, "Save logs", "logs", enabled=ready)

    # ------------------------------------------------------------------ headset
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
        self.msgs.put(("devmsg", ("Installing adb (Android platform-tools from Google)...", MUTED)))
        try:
            adb = device.download_adb(log=lambda m: None)
        except Exception as e:
            raise RuntimeError(f"couldn't install adb ({e}). Check the internet connection, then Get adb.")
        self.msgs.put(("adb", adb))
        self.msgs.put(("devmsg", ("adb installed.", GOOD)))

    def job_connect(self, target):
        self.msgs.put(("devmsg", (f"Connecting to {target}...", MUTED)))
        ok, out = device.connect(self.adb, target)
        if ok:
            serial = target.strip() if ":" in target else target.strip() + ":5555"
            if device.frame_info(self.adb, serial):
                self.msgs.put(("frame", True))
        self.msgs.put(("devmsg", ("Connected. Install sends Echo and its game data; Launch starts it." if ok else f"Couldn't connect: {out}",
                                  GOOD if ok else BAD)))

    def job_install(self, serial, apk, with_data, allow_uninstall=False):
        if apk:
            self.msgs.put(("devmsg", (f"Installing {os.path.basename(apk)}... (about a minute)", MUTED)))
            if allow_uninstall:
                ok, out = device.uninstall(self.adb, serial)
                if not ok:
                    self.msgs.put(("devmsg", (f"Couldn't uninstall the old Echo VR: {out}", BAD)))
                    return
            kind, text = device.install(self.adb, serial, apk)
            if kind == "signature":
                self.msgs.put(("ask_uninstall", (serial, apk, with_data)))
                return
            if kind != "ok":
                self.msgs.put(("devmsg", (f"Install failed: {text}", BAD)))
                return
        if with_data:
            self.job_data(serial)
        self.msgs.put(("devmsg", ("Installed. Press Launch, then put the headset on." if apk else
                                  "Game data ready. Press Launch, then put the headset on.", GOOD)))

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
        self.msgs.put(("devmsg", ("Echo VR started: put the headset on." if ok else f"Couldn't start Echo: {out}",
                                  GOOD if ok else BAD)))

    def job_logs(self, serial, path):
        n = device.save_logs(self.adb, serial, path)
        self.msgs.put(("devmsg", (f"Saved {n} log lines to {os.path.basename(path)}", GOOD)))

    # ------------------------------------------------------------------ events
    def item_tag(self, e):
        for item in reversed(self.cv.find_overlapping(e.x, e.y, e.x, e.y)):
            for t in self.cv.gettags(item):
                if t in ("pick", "out", "go", "folder", "again", "details", "data", "frame", "getadb", "connect", "install",
                         "launch", "logs"):
                    return t
        return None

    def on_move(self, e):
        t = self.item_tag(e)
        if t != self.hot:
            self.hot = t
            self.draw()

    def on_click(self, e):
        t = self.item_tag(e)
        if self.state == "working" and t != "details":
            return
        if t == "pick":
            self.pick_apk()
        elif t == "out" and self.apk:
            path = filedialog.asksaveasfilename(title="Save the patched APK as", defaultextension=".apk",
                                                initialdir=os.path.dirname(self.out), initialfile=os.path.basename(self.out),
                                                filetypes=[("Android app", "*.apk")])
            if path and os.path.abspath(path) != os.path.abspath(self.apk):
                self.out = path
        elif t == "go" and self.apk and self.out:
            self.start()
        elif t == "folder" and self.result:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self.result[0])]) if sys.platform == "win32" else None
        elif t == "again":
            self.apk = self.out = self.result = None
            self.state, self.lines, self.details = "pick", [], False
        elif t == "details":
            self.details = not self.details
        elif t == "data" and not self.dev_busy:
            self.with_data = not self.with_data
        elif t == "frame" and self.state != "working":
            self.set_frame(not self.for_frame)
        elif t in ("getadb", "connect", "install", "launch", "logs") and not self.dev_busy:
            self.headset_click(t)
        self.draw()

    def set_frame(self, on):
        """Ticks or unticks the Steam Frame build, renaming the output to match."""
        self.for_frame = on
        if self.out and self.state != "done":
            stem, ext = os.path.splitext(self.out)
            stem = stem[:-len("_frame")] if stem.endswith("_openxr_frame") else stem
            self.out = stem + ("_frame" if on and stem.endswith("_openxr") else "") + ext

    def headset_click(self, t):
        if not self.adb:   # every headset action needs adb (it installs itself at startup)
            self.dev_job(self.job_getadb)
            return
        if t == "connect":
            target = simpledialog.askstring(
                "EchoQuestXR", "Steam Frame: the adb address of Echo's app, shown by Frame Control.\n\n"
                "Enter it as IP:port, e.g. 192.168.1.50:5555 (or localhost:5555 through an SSH tunnel).",
                parent=self)
            if target:
                self.dev_job(self.job_connect, target)
            return
        serial = self.devs[0][0] if self.devs else None
        if not serial:
            return
        if t == "install" and (self.installable() or self.with_data):
            if self.dev_frame and self.installable() and not self.built_for_frame and not messagebox.askyesno(
                    "EchoQuestXR", "This is a Steam Frame, but the APK wasn't patched with \"For Steam Frame\" "
                    "ticked, so Echo will look for its game data where the Frame can't keep it.\n\n"
                    "Tick it, Patch and sign again, then Install. Install this one anyway?", icon="warning"):
                return
            self.dev_job(self.job_install, serial, self.installable(), self.with_data)
        elif t == "launch":
            self.dev_job(self.job_launch, serial)
        elif t == "logs":
            base = os.path.dirname(self.out) if self.out else os.path.expanduser("~")
            path = filedialog.asksaveasfilename(title="Save the EchoQuestXR log as", defaultextension=".txt",
                                                initialdir=base, initialfile=time.strftime("echoquestxr-log-%Y%m%d-%H%M%S.txt"),
                                                filetypes=[("Text", "*.txt")])
            if path:
                self.dev_job(self.job_logs, serial, path)

    def pick_apk(self):
        path = filedialog.askopenfilename(title="Choose your Echo VR Quest APK",
                                          filetypes=[("Android app", "*.apk"), ("All files", "*.*")])
        if not path:
            return
        self.apk = path
        self.out = os.path.splitext(path)[0] + ("_openxr_frame.apk" if self.for_frame else "_openxr.apk")
        self.state, self.lines, self.result = "ready", [], None
        try:
            self.verdict = patch.inspect(path)
            self.verdict_ok = "hasn't seen" not in self.verdict
        except patch.PatchError as e:
            self.verdict, self.verdict_ok = str(e), None
            self.apk = path   # keep it shown, but the button stays off
        self.draw()

    def start(self):
        self.state, self.message, self.progress, self.lines = "working", "Reading the APK...", 0.05, []
        self.draw()
        threading.Thread(target=self.work, args=(self.apk, self.out, self.for_frame), daemon=True).start()

    def work(self, apk, out, for_frame=False):
        steps = {"Runtime": .2, "  added": .4, "  replaced": .5, "Generating": .6, "Wrote": .95}
        def log(m):
            for k, v in steps.items():
                if m.startswith(k):
                    self.msgs.put(("progress", v))
            self.msgs.put(("log", m))
            if m.startswith("Generating"):
                self.msgs.put(("message", "Signing with a new random key..."))
        try:
            result = patch.patch(apk, out, log=log, data_dir=patch.FRAME_DATA_DIR if for_frame else None)
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
            elif kind == "devices":
                adb, devs = v
                if adb is None and self.adb and os.path.isfile(self.adb):
                    adb = self.adb   # a scan from before adb was installed: keep the new adb
                self.adb, self.devs = adb, devs
            elif kind == "devprogress":
                self.dev_progress = v
            elif kind == "built_for_frame":
                self.built_for_frame = v
            elif kind == "frame":   # connected to a Steam Frame: its build is the one to make
                self.dev_frame = True
                if not self.for_frame and self.state != "done":
                    self.set_frame(True)
            elif kind == "devmsg":
                self.dev_msg, self.dev_tint = v
            elif kind == "adb":
                self.adb = v
            elif kind == "devdone":
                if v == self.job_id:
                    self.dev_busy = False
            elif kind == "ask_uninstall":
                serial, apk, with_data = v
                if messagebox.askyesno("EchoQuestXR", "Echo VR on the headset is signed with a different key, so it "
                                       "has to be uninstalled first.\n\nUninstalling removes Echo VR's app data on the "
                                       "headset (settings and login). The game files in Android/media normally stay, "
                                       "but back them up first if you're unsure.\n\nUninstall Echo VR and install the "
                                       "patched one?", icon="warning"):
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
