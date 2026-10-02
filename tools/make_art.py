"""Makes the Steam library artwork for Echo VR on a Steam Frame (patcher/art/), from the cover
image in tools/art/cover-source.webp. frame_setup.py gives it to the Steam shortcut.

    python tools/make_art.py

  capsule.jpg   600x900    the library's portrait tile
  wide.jpg      920x430    the wide tile
  hero.jpg      1920x620   the banner at the top of the game's page
  logo.png      1280x720   the title over the banner (transparent)
"""
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "tools", "art", "cover-source.webp")
OUT = os.path.join(ROOT, "patcher", "art")
DISC = (602, 528)   # the middle of the disc in the source image
BOLD = ["seguibl.ttf", "segoeuib.ttf", "arialbd.ttf"]
WHITE = (255, 255, 255)


def font(size):
    for n in BOLD:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            pass
    return ImageFont.load_default()


def cover():
    """The cover image, as it is."""
    return Image.open(SRC).convert("RGB")


def crop(im, cx, cy, aspect, size):
    """The largest crop of aspect (w/h) around (cx, cy) that fits, scaled to size."""
    w, h = im.size
    cw, ch = (w, w / aspect) if w / aspect <= h else (h * aspect, h)
    x0 = min(max(cx - cw / 2, 0), w - cw)
    y0 = min(max(cy - ch / 2, 0), h - ch)
    return im.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch))).resize(size, Image.LANCZOS)


def logo():
    """The title over the banner: ECHO VR, white on transparency, with a soft shadow."""
    S = 1280, 720
    text = Image.new("RGBA", S, (0, 0, 0, 0))
    ImageDraw.Draw(text).text((S[0] // 2, S[1] // 2), "ECHO VR", font=font(220), fill=WHITE + (255,), anchor="mm")
    shadow = Image.new("RGBA", S, (0, 0, 0, 0))
    shadow.putalpha(text.getchannel("A").filter(ImageFilter.GaussianBlur(14)).point(lambda a: int(a * 0.6)))
    return Image.alpha_composite(shadow, text)


def main():
    os.makedirs(OUT, exist_ok=True)
    im = cover()
    crop(im, DISC[0], DISC[1] - 60, 600 / 900, (600, 900)).save(os.path.join(OUT, "capsule.jpg"), quality=92)
    crop(im, DISC[0], 430, 920 / 430, (920, 430)).save(os.path.join(OUT, "wide.jpg"), quality=92)
    crop(im, DISC[0], 470, 1920 / 620, (1920, 620)).save(os.path.join(OUT, "hero.jpg"), quality=90)
    logo().save(os.path.join(OUT, "logo.png"))
    print("wrote", ", ".join(sorted(os.listdir(OUT))), "in patcher/art")


if __name__ == "__main__":
    main()
