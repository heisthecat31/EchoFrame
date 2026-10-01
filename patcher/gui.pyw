"""EchoQuestXR patcher window: pick your Echo VR Quest APK, get one that runs on OpenXR.

    pythonw gui.pyw      (double-click on Windows)

Same patches as patch.py; every run signs with a new random key. Drawn on one Canvas in
EchoXR's installer style (palette and gradient from EchoXR's installer/ui.h).
"""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog

import patch

# palette (EchoXR installer/ui.h)
BG, CARD, CARD_HI, BORDER = "#0e1015", "#171a21", "#1d212a", "#262b36"
TEXT, MUTED, FAINT = "#eef0f4", "#8e95a3", "#5a6170"
ACCENT, ACCENT2, GOOD, WARN, BAD = "#5b8cff", "#9a6bff", "#3ddc97", "#ffb547", "#ff5d6c"
W, H = 720, 560

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
        self.title("EchoQuestXR")
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
        self.rrect(32, y, W - 64, 70, 16, CARD, outline=BORDER)
        self.text(52, y + 14, "2   SAVE AS", "label", FAINT)
        self.text(52, y + 36, self.out or "Chosen after you pick the APK", "small", TEXT if self.out else FAINT, width=W - 200)
        self.button(W - 140, y + 18, 88, 34, "Change", "out", enabled=bool(self.apk) and self.state != "working")

        # status / result area
        y = 326
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
            self.text(94, y + 18, "Patched and signed", "h", TEXT)
            self.text(94, y + 46, "Uninstall Echo VR from the headset, then install the new APK:", "small", MUTED)
            self.text(94, y + 68, f'adb install "{os.path.basename(out)}"', "mono", TEXT)
            self.text(94, y + 88, f"New signing key {fp[:16]}...  saved as {os.path.basename(key_path)} (keep it private)", "small", FAINT)
        elif self.state == "error":
            self.rrect(32, y, W - 64, 72, 16, mix(BAD, BG, .88), outline=mix(BAD, BG, .6))
            self.text(52, y + 14, "Couldn't patch", "h", BAD)
            self.text(52, y + 40, self.message, "small", TEXT, width=W - 104)
        else:
            self.text(32, y + 4, "Every patch is signed with a brand-new random key, so no two installs share one.", "small", MUTED)
            self.text(32, y + 24, "Android only updates an app signed with the same key: uninstall Echo VR before installing.", "small", MUTED)

        # details (log)
        if self.lines:
            self.text(32, 450, ("▾ Hide details" if self.details else "▸ Show details"),
                      "small", ACCENT if self.hot == "details" else MUTED, tags=("details",))
            if self.details:
                self.rrect(32, 474, W - 64, 100, 10, CARD, outline=BORDER)
                self.text(44, 482, "\n".join(self.lines[-5:]), "mono", MUTED, width=W - 88)

        # footer
        busy = self.state == "working"
        if self.state == "done":
            self.button(32, hh - 66, 150, 42, "Open folder", "folder")
            self.button(W - 32 - 200, hh - 70, 200, 48, "Patch another", "again", primary=True)
        else:
            self.button(W - 32 - 200, hh - 70, 200, 48, "Patching..." if busy else "Patch and sign", "go",
                        primary=True, enabled=bool(self.apk and self.out) and self.verdict_ok is not None and not busy)

    # ------------------------------------------------------------------ events
    def item_tag(self, e):
        for item in reversed(self.cv.find_overlapping(e.x, e.y, e.x, e.y)):
            for t in self.cv.gettags(item):
                if t in ("pick", "out", "go", "folder", "again", "details"):
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
        self.draw()

    def pick_apk(self):
        path = filedialog.askopenfilename(title="Choose your Echo VR Quest APK",
                                          filetypes=[("Android app", "*.apk"), ("All files", "*.*")])
        if not path:
            return
        self.apk = path
        self.out = os.path.splitext(path)[0] + "_openxr.apk"
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
        threading.Thread(target=self.work, args=(self.apk, self.out), daemon=True).start()

    def work(self, apk, out):
        steps = {"Runtime": .2, "  added": .4, "  replaced": .5, "Generating": .6, "Wrote": .95}
        def log(m):
            for k, v in steps.items():
                if m.startswith(k):
                    self.msgs.put(("progress", v))
            self.msgs.put(("log", m))
            if m.startswith("Generating"):
                self.msgs.put(("message", "Signing with a new random key..."))
        try:
            self.msgs.put(("done", patch.patch(apk, out, log=log)))
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
        if changed:
            self.draw()
        self.after(50, self.tick)


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    App().mainloop()
