"""Steam Frame buttons: which Steam Frame controller button does each of Echo's.

Echo VR was made for Quest's Touch controllers: X, Y and Menu on the left, A and B on the
right. The Frame's left controller has a d-pad and View where Touch has X/Y and Menu, its right
one A/B/X/Y and Menu; both have a bumper. The layout is saved on the headset as
files/echoquestxr-buttons.txt in Echo's folder (device.write_buttons; Install writes it too) and
read by EchoQuestXR's runtime when Echo starts (FrameButtons() in runtime/vrapi_openxr.cpp). A
button only maps to an input on its own hand: OpenXR binds each hand's buttons to that hand.

ButtonsPanel is a section of the patcher window; the controllers are drawn after Valve's Steam
Frame controller diagram.
"""
import os
import tkinter as tk

# Echo's buttons, the hand they're on, and the Frame inputs they can take
ECHO_BUTTONS = [("X", "left"), ("Y", "left"), ("Menu", "left"), ("A", "right"), ("B", "right")]
CHOICES = {
    "left": [("dpad_up", "D-pad up"), ("dpad_down", "D-pad down"), ("dpad_left", "D-pad left"),
             ("dpad_right", "D-pad right"), ("view", "View"), ("bumper", "Bumper (L1)"), ("none", "Nothing")],
    "right": [("a", "A"), ("b", "B"), ("x", "X"), ("y", "Y"), ("menu", "Menu"), ("bumper", "Bumper (R1)"),
              ("none", "Nothing")],
}
DEFAULTS = {"X": "dpad_up", "Y": "dpad_right", "Menu": "view", "A": "y", "B": "x", "Sync": "skip",
            "Foveation": "medium", "Mic": "70"}
# written as DEFAULTS whatever was chosen; only the button layout is the player's
FIXED = ["Sync", "Foveation", "Mic"]
PANEL_W, PANEL_H = 656, 340


def parse(text):
    """{Echo button: Frame input} from the file's text, defaults for anything missing or wrong."""
    m = dict(DEFAULTS)
    hands = dict(ECHO_BUTTONS)
    for line in (text or "").splitlines():
        key, _, value = line.strip().partition("=")
        if key in hands and value in {v for v, _ in CHOICES[hands[key]]}:
            m[key] = value
    return m


def to_text(m):
    text = "".join(f"{k}={m.get(k, DEFAULTS[k])}\n" for k, _ in ECHO_BUTTONS)
    return text + "".join(f"{k}={DEFAULTS[k]}\n" for k in FIXED)


def load_local(folder):
    """The layout last chosen on this PC (defaults if none)."""
    try:
        with open(os.path.join(folder, "frame-buttons.txt"), encoding="utf-8") as f:
            return parse(f.read())
    except OSError:
        return dict(DEFAULTS)


def save_local(folder, m):
    try:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "frame-buttons.txt"), "w", encoding="utf-8") as f:
            f.write(to_text(m))
    except OSError:
        pass


class ButtonsPanel(tk.Frame):
    """colors: BG, CARD, BORDER, TEXT, MUTED, FAINT, ACCENT, GOOD, WARN. on_change(mapping) when
    the layout changes; on_save(mapping) for "Save to headset"."""

    def __init__(self, master, colors, scale, mapping, on_change, on_save):
        super().__init__(master, bg=colors["CARD"], highlightthickness=0, bd=0,
                         width=int(PANEL_W * scale), height=int(PANEL_H * scale))
        self.c, self.s = colors, scale
        self.on_change, self.on_save = on_change, on_save
        self.map = dict(mapping)
        self.status, self.status_tint = "", colors["MUTED"]
        self.can_save = False
        self.cv = tk.Canvas(self, width=self.px(PANEL_W), height=self.px(PANEL_H), bg=colors["CARD"],
                            highlightthickness=0, bd=0)
        self.cv.place(x=0, y=0)
        self.font = lambda size, bold=False: ("Segoe UI Semibold" if bold else "Segoe UI", -self.px(size))
        self.vars = {}
        self.build_controls()
        self.draw()

    def px(self, v):
        return int(round(v * self.s))

    # ---------------------------------------------------------------- controls
    def build_controls(self):
        c = self.c
        for i, (key, hand) in enumerate(ECHO_BUTTONS):
            y = 40 + i * 44 + (10 if hand == "right" else 0)
            tk.Label(self, text=f"Echo {key}", bg=c["CARD"], fg=c["TEXT"], font=self.font(12, True)).place(
                x=self.px(250), y=self.px(y + 4))
            var = tk.StringVar(value=dict(CHOICES[hand])[self.map[key]])
            var.trace_add("write", lambda *_, k=key, h=hand, v=var: self.pick(k, h, v))
            om = tk.OptionMenu(self, var, *[n for _, n in CHOICES[hand]])
            om.configure(bg=c["BORDER"], fg=c["TEXT"], activebackground=c["ACCENT"], activeforeground="#ffffff",
                         highlightthickness=0, bd=0, relief="flat", font=self.font(11), width=11, anchor="w",
                         cursor="hand2")
            om["menu"].configure(bg=c["CARD"], fg=c["TEXT"], activebackground=c["ACCENT"], activeforeground="#ffffff",
                                 font=self.font(11), bd=0)
            om.place(x=self.px(320), y=self.px(y))
            self.vars[key] = var

        def btn(text, x, w, cmd, primary=False):
            b = tk.Button(self, text=text, command=cmd, bg=c["ACCENT"] if primary else c["BORDER"],
                          fg="#ffffff" if primary else c["TEXT"], activebackground=c["ACCENT"],
                          activeforeground="#ffffff", relief="flat", bd=0, font=self.font(12, True), cursor="hand2")
            b.place(x=self.px(x), y=self.px(PANEL_H - 46), width=self.px(w), height=self.px(34))
            return b

        btn("Defaults", 20, 100, self.defaults)
        self.save_btn = btn("Save to headset", PANEL_W - 20 - 170, 170, self.save, primary=True)
        self.set_can_save(False)

    def set_can_save(self, ok):
        """Save to headset works once a headset is connected."""
        self.can_save = ok
        c = self.c
        self.save_btn.configure(state="normal" if ok else "disabled", bg=c["ACCENT"] if ok else c["BORDER"],
                                fg="#ffffff" if ok else c["FAINT"])

    def pick(self, key, hand, var):
        self.map[key] = {n: v for v, n in CHOICES[hand]}[var.get()]
        self.status = ""
        self.on_change(dict(self.map))
        self.draw()

    def set_mapping(self, m):
        for key, hand in ECHO_BUTTONS:
            self.vars[key].set(dict(CHOICES[hand])[m[key]])

    def defaults(self):
        self.set_mapping(DEFAULTS)

    def save(self):
        if self.can_save:
            self.on_save(dict(self.map))

    def show_status(self, text, tint):
        self.status, self.status_tint = text, tint
        self.draw()

    # ---------------------------------------------------------------- drawing
    def draw(self):
        cv, c, px = self.cv, self.c, self.px
        cv.delete("all")
        cv.create_text(px(20), px(12), anchor="nw", fill=c["MUTED"], font=self.font(11), width=px(PANEL_W - 40),
                       text="Which Frame button does each of Echo's. Install (or Save to headset) puts the layout "
                            "on the headset; Echo uses it the next time it starts.")
        on = {}   # Frame input -> Echo buttons on it
        for key, hand in ECHO_BUTTONS:
            if self.map[key] != "none":
                on.setdefault((hand, self.map[key]), []).append(key)
        self.controller(124, 150, "left", on)
        self.controller(PANEL_W - 104, 150, "right", on)
        if self.status:
            cv.create_text(px(PANEL_W - 200), px(PANEL_H - 29), text=self.status, anchor="e", fill=self.status_tint,
                           font=self.font(11))

    def badge(self, x, y, keys):
        """Echo's button names on a Frame input."""
        cv, c, px = self.cv, self.c, self.px
        text = "+".join(keys)
        w = 10 + 8 * len(text)
        cv.create_oval(px(x - w / 2), px(y - 10), px(x + w / 2), px(y + 10), fill=c["ACCENT"], outline=c["ACCENT"])
        cv.create_text(px(x), px(y), text=text, fill="#ffffff", font=self.font(10, True))

    def controller(self, cx, cy, hand, on):
        """One controller, top view, after Valve's diagram: head ring, handle, buttons. k scales it."""
        cv, c, px = self.cv, self.c, self.px
        k = 0.72
        line, faint, idle = c["MUTED"], c["FAINT"], c["BG"]
        mirror = 1 if hand == "left" else -1
        P = lambda dx, dy: (cx + dx * k, cy + dy * k)

        def oval(x, y, rx, ry, **kw):
            cv.create_oval(px(x - rx * k), px(y - ry * k), px(x + rx * k), px(y + ry * k), **kw)

        # handle, then the head over it
        hx0, hy0 = P(-34, 70)
        hx1, hy1 = P(26, 150)
        cv.create_polygon(px(hx0), px(hy0), px(P(34, 70)[0]), px(hy0), px(hx1), px(hy1), px(P(-26, 150)[0]), px(hy1),
                          fill=c["CARD"], outline=faint, width=1)
        oval(cx, cy, 108, 104, fill=c["CARD"], outline=line, width=2)
        cv.create_text(px(cx), px(P(0, 162)[1]), text=("LEFT" if hand == "left" else "RIGHT"), fill=faint,
                       font=self.font(10, True))

        def mark(name, x, y, draw, badge_at):
            used = on.get((hand, name))
            draw(c["ACCENT"] if used else line, c["BORDER"] if used else idle)
            if used:
                self.badge(*badge_at, used)

        # thumbstick (inner side) and the Steam button (SteamOS's)
        sx, sy = P(42 * mirror, 12)
        oval(sx, sy, 26, 26, outline=line, width=2, fill=idle)
        oval(sx, sy, 15, 15, outline=faint, width=1)
        cv.create_text(px(sx), px(P(0, 50)[1]), text=("L3" if hand == "left" else "R3"), fill=faint, font=self.font(9))
        stx, sty = P(0, 70)
        oval(stx, sty, 10, 10, outline=faint, width=1)
        # View (left) / Menu (right): a small pill above the stick
        name = "view" if hand == "left" else "menu"
        mx, my = P(2 * mirror, -62)
        mark(name, mx, my, lambda o, f: oval(mx, my, 14, 6, outline=o, width=2, fill=f), (mx, my - 18))
        # bumper: the far edge of the head, where it sits on the front
        bx, by = P(0, -104)
        mark("bumper", bx, by, lambda o, f: cv.create_arc(px(bx - 60 * k), px(by - 18 * k), px(bx + 60 * k),
                                                           px(by + 18 * k), start=20, extent=140, style="arc",
                                                           outline=o, width=4), (bx + 58 * mirror, by - 6))
        # the four face inputs: d-pad (left) or A/B/X/Y (right), on the outer side
        fx, fy = P(-48 * mirror, 8)
        if hand == "left":
            arms = {"dpad_up": (0, -22), "dpad_down": (0, 22), "dpad_left": (-22, 0), "dpad_right": (22, 0)}
            cv.create_rectangle(px(fx - 9 * k), px(fy - 9 * k), px(fx + 9 * k), px(fy + 9 * k), outline=line, fill=idle)
        else:
            arms = {"y": (0, -30), "a": (0, 30), "x": (-30, 0), "b": (30, 0)}
        for nm, (dx, dy) in arms.items():
            x0, y0 = fx + dx * k, fy + dy * k
            if hand == "left":
                draw = lambda o, f, x0=x0, y0=y0: cv.create_rectangle(px(x0 - 10 * k), px(y0 - 10 * k), px(x0 + 10 * k),
                                                                      px(y0 + 10 * k), outline=o, fill=f, width=2)
            else:
                draw = lambda o, f, x0=x0, y0=y0: oval(x0, y0, 15, 15, outline=o, fill=f, width=2)
            mark(nm, x0, y0, draw, (fx + dx * k * 2.4, fy + dy * k * 2.2))
            if hand == "right":
                cv.create_text(px(x0), px(y0), text=nm.upper(), fill=c["TEXT"], font=self.font(9, True))
