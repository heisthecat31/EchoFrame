"""Draws EchoFrame's icon: patcher/echoframe.png (512 px), patcher/echoframe.ico (window and
.exe), and patcher/echoframe-<px>.png for the window's sidebar. A blue-to-purple tile with a soft
light flare, a big "E" and a small "XR".

    python tools/make_icon.py
"""
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "patcher")
S = 1024   # drawn large, scaled down smooth
SIDEBAR = (44, 55, 66, 88)
WHITE = (255, 255, 255)


def font(names, size):
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            pass
    return ImageFont.load_default()


def gradient(size, c1, c2):
    g = Image.new("RGBA", (size, size))
    d = ImageDraw.Draw(g)
    for i in range(2 * size):   # diagonal, top left to bottom right
        t = i / (2 * size - 1)
        d.line([(i, 0), (0, i)], fill=tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)) + (255,), width=2)
    return g


def disc(img, cx, cy, r):
    """Echo's disc: two C-shaped halves split along a diagonal, a cut near each end, and a round
    hub, in white (the tile shows through the cuts)."""
    n = 1024   # drawn flat at this size, centre (n/2, n/2), outer radius 430
    c, R = n / 2, 430
    mask = Image.new("L", (n, n), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((c - R, c - R, c + R, c + R), fill=255)       # the rim...
    d.ellipse((c - 250, c - 250, c + 250, c + 250), fill=0)  # ...a ring
    u = (0.7071, 0.7071)    # along the diagonal, top left to bottom right
    v = (0.7071, -0.7071)   # across it

    def band(offset, width):   # a straight strip along the diagonal, cleared
        a, b = offset - width / 2, offset + width / 2
        pts = [(c + v[0] * o + u[0] * t, c + v[1] * o + u[1] * t) for o, t in ((a, -n), (b, -n), (b, n), (a, n))]
        d.polygon(pts, fill=0)
    band(0, 120)                 # the gap between the halves
    for o in (-180, 180):        # the cuts near each end
        band(o, 22)
    h = 170
    d.ellipse((c - h, c - h, c + h, c + h), fill=255)        # the hub
    layer = Image.new("RGBA", (n, n), WHITE + (0,))
    layer.putalpha(mask)
    w = int(2 * r * n / (2 * R))
    layer = layer.resize((w, w), Image.LANCZOS)
    img.alpha_composite(layer, (int(cx - layer.width / 2), int(cy - layer.height / 2)))


def draw():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    tile = Image.new("L", (S, S), 0)
    ImageDraw.Draw(tile).rounded_rectangle((24, 24, S - 24, S - 24), radius=230, fill=255)
    img.paste(gradient(S, (0x4f, 0x7b, 0xff), (0xa0, 0x58, 0xff)), (0, 0), tile)

    # a soft light flare, top left
    flare = Image.new("L", (S, S), 0)
    ImageDraw.Draw(flare).ellipse((40, 20, 560, 420), fill=90)
    flare = flare.filter(ImageFilter.GaussianBlur(120))
    img.paste(Image.new("RGBA", (S, S), WHITE + (255,)), (0, 0), flare)
    d = ImageDraw.Draw(img)
    bold = ["seguibl.ttf", "segoeuib.ttf", "arialbd.ttf"]
    d.text((385, 505), "E", font=font(bold, 640), fill=WHITE, anchor="mm")
    d.text((695, 325), "XR", font=font(bold, 200), fill=WHITE, anchor="mm")
    disc(img, 700, 590, 128)   # under the XR
    # keep everything inside the tile
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(img, (0, 0), tile)
    return out


def main():
    img = draw()
    img.resize((512, 512), Image.LANCZOS).save(os.path.join(OUT, "echoframe.png"))
    for px in SIDEBAR:   # the window's sidebar logo at 100, 125, 150 and 200 % display scaling
        img.resize((px, px), Image.LANCZOS).save(os.path.join(OUT, f"echoframe-{px}.png"))
    img.resize((256, 256), Image.LANCZOS).save(
        os.path.join(OUT, "echoframe.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote patcher/echoframe.png, echoframe.ico and the sidebar sizes")


if __name__ == "__main__":
    main()
