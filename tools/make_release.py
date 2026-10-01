"""Builds the Windows release of the EchoQuestXR patcher into build/release/:

    EchoQuestXR-Patcher-v<version>.exe   the patcher window, standalone (no Python needed)
    EchoQuestXR-Patcher-v<version>.zip   the .exe, README.txt and THIRD_PARTY_NOTICES.txt

    python tools/make_release.py              build the runtime, then package
    python tools/make_release.py --no-build   package the runtime already in build/

Needs: what runtime/build.py needs (NDK, CMake, Ninja, OpenXR SDK source), plus Python with
`cryptography`, Pillow (for the icon) and PyInstaller. No APK, game file or key goes in.
"""
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BUILD = os.path.join(ROOT, "build")
REL = os.path.join(BUILD, "release")
VERSION = open(os.path.join(ROOT, "VERSION"), encoding="utf-8").read().strip()
OPENXR_SDK = os.environ.get("OPENXR_SDK", os.path.join(ROOT, "..", "EchoVR-Hand-Tracking", "xr", "OpenXR-SDK"))

README = """EchoQuestXR patcher v{version}
==========================
Makes your Echo VR Quest APK run on OpenXR: on Quest (Meta's OpenXR runtime) and,
untested so far, on Steam Frame and other OpenXR headsets.

1. Run EchoQuestXR-Patcher-v{version}.exe (Windows may warn about an unknown
   publisher: More info -> Run anyway).
2. Choose your own Echo VR Quest APK. Nothing is uploaded; the original isn't changed.
3. Patch and sign. Every patch is signed with a brand-new random key, saved next to
   the APK as .signing-key.pem (keep it private).
4. Install:
   - Quest (or any headset over USB): step 3 "Headset" in the window. Turn on USB
     debugging first. Android only updates an app signed with the same key, so the
     window asks before uninstalling an Echo VR signed with another key (that removes
     Echo's app data on the headset; the game files normally stay).
   - Steam Frame: install the patched APK with Frame Control
     (https://github.com/saphid/frame-control): enable Developer Mode on the Frame,
     let Frame Control connect, then install the APK file. Echo appears in your
     Steam library.

Logs: "Save logs" in the window (EchoQuestXR and OpenXR lines only; Echo's own log
is left out because it prints the community-server password). The headset buttons
install adb (Google's platform-tools) by themselves the first time, after asking.
Steam Frame: launch Echo on the Frame, press Connect... and enter the adb address
Frame Control shows for Echo (IP:port), then Save logs.

The patch changes three things in the APK: lib/arm64-v8a/libvrapi.so (VrApi on
OpenXR), lib/arm64-v8a/libopenxr_loader.so (Khronos OpenXR loader) and the manifest
(launcher entry for Steam Frame, OpenXR permissions). Everything else is copied as is.
"""


def run(cmd, **kw):
    print(">", " ".join(cmd))
    subprocess.check_call(cmd, **kw)


def icon(path):
    """The window's badge: a blue-to-purple circle with "XR"."""
    from PIL import Image, ImageDraw, ImageFont
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = Image.new("RGBA", (size, size))
    for x in range(size):
        t = x / (size - 1)
        c = (int(0x4f + (0x9a - 0x4f) * t), int(0x7b + (0x5c - 0x7b) * t), 0xff, 255)
        ImageDraw.Draw(grad).line([(x, 0), (x, size)], fill=c)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((4, 4, size - 4, size - 4), fill=255)
    img.paste(grad, (0, 0), mask)
    try:
        font = ImageFont.truetype("seguisb.ttf", 104)
    except OSError:
        font = ImageFont.load_default()
    d = ImageDraw.Draw(img)
    d.text((size / 2, size / 2), "XR", font=font, fill="white", anchor="mm")
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def notices():
    def read(*p):
        with open(os.path.join(*p), encoding="utf-8", errors="replace") as f:
            return f.read()
    parts = [
        "EchoQuestXR patcher -- third-party notices\n",
        "==== Khronos OpenXR loader (libopenxr_loader.so, inside patched APKs) -- Apache License 2.0 ====\n"
        + read(OPENXR_SDK, "LICENSES", "Apache-2.0.txt"),
        "==== JsonCpp (part of the OpenXR loader) -- MIT ====\n" + read(OPENXR_SDK, "src", "external", "jsoncpp", "LICENSE"),
        "==== cryptography (signing) -- Apache License 2.0 or BSD-3-Clause ====\n"
        "https://github.com/pyca/cryptography -- used under the Apache License 2.0 (text above).\n",
        "==== Python and its standard library (bundled by PyInstaller) -- PSF License ====\n"
        "https://docs.python.org/3/license.html\n",
        "==== PyInstaller bootloader -- GPL 2.0 with the bootloader exception ====\n"
        "https://pyinstaller.org/en/stable/license.html\n",
    ]
    return "\n\n".join(parts)


def main():
    if "--no-build" not in sys.argv:
        run([sys.executable, os.path.join(ROOT, "runtime", "build.py"), "--lib-only"])
    libs = [os.path.join(BUILD, f) for f in ("libvrapi.so", "libopenxr_loader.so")]
    missing = [p for p in libs if not os.path.isfile(p)]
    if missing:
        sys.exit("missing runtime libraries (run without --no-build):\n  " + "\n  ".join(missing))

    os.makedirs(REL, exist_ok=True)
    work = os.path.join(BUILD, "pyinstaller")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    ico = os.path.join(work, "echoquestxr.ico")
    icon(ico)
    version_file = os.path.join(work, "VERSION")
    with open(version_file, "w") as f:
        f.write(VERSION + "\n")
    name = f"EchoQuestXR-Patcher-v{VERSION}"
    sep = os.pathsep
    patcher = os.path.join(ROOT, "patcher")
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
         "--name", name, "--icon", ico, "--distpath", REL, "--workpath", os.path.join(work, "work"),
         "--specpath", work, "--paths", patcher,
         "--add-data", f"{libs[0]}{sep}runtime", "--add-data", f"{libs[1]}{sep}runtime",
         "--add-data", f"{ico}{sep}.", "--add-data", f"{version_file}{sep}.",
         "--hidden-import", "patch", "--hidden-import", "device", "--hidden-import", "axml",
         os.path.join(patcher, "gui.pyw")])
    exe = os.path.join(REL, name + ".exe")
    zpath = os.path.join(REL, name + ".zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(exe, os.path.basename(exe))
        z.writestr("README.txt", README.format(version=VERSION).replace("\n", "\r\n"))
        z.writestr("THIRD_PARTY_NOTICES.txt", notices().replace("\r\n", "\n").replace("\n", "\r\n"))
    for p in (exe, zpath):
        print("%-60s %8.1f MB" % (os.path.relpath(p, ROOT), os.path.getsize(p) / 1024 / 1024))


if __name__ == "__main__":
    main()
