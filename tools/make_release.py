"""Builds the Windows release of the EchoFrame into build/release/:

    EchoFrame-v<version>.exe   the patcher window, standalone (no Python needed)
    EchoFrame-v<version>.zip   the .exe, README.txt and THIRD_PARTY_NOTICES.txt

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

README = """EchoFrame v{version}
==========================
Makes your Echo VR Quest APK run on OpenXR: on Quest (Meta's OpenXR runtime) and,
untested so far, on Steam Frame and other OpenXR headsets.

1. Run EchoFrame-v{version}.exe (Windows may warn about an unknown
   publisher: More info -> Run anyway).
2. Choose your own Echo VR Quest APK. Nothing is uploaded; the original isn't changed.
3. Patch and sign. The APK is signed with your own random key, saved next to it as
   .signing-key.pem (keep it private). Patching to the same file again reuses that
   key, so updates keep Echo's data (a new key means Echo gets reinstalled, which
   deletes its data, and on Steam Frame its game data).
4. Install:
   The window walks you through it step by step (Your headset, APK, Make it, Connect,
   Install, Play).
   - Quest: turn on Developer Mode, plug in with USB, allow USB debugging. Android only
     updates an app signed with the same key, so the window asks before uninstalling an
     Echo VR signed with another key (that removes Echo's app data on the headset; the
     game files normally stay).
   - Steam Frame: choose Steam Frame in step 1 (Echo then keeps its game data in its
     private folder, which the Frame keeps). Turn on Developer Mode and plug the Frame in
     with USB-C (or type its Wi-Fi adb address). The first Install sets everything up:
     it installs Lepton from Steam if needed, adds Echo VR to the Steam library, and
     starts Echo once to make its data folder (keep the headset awake). Frame Control
     isn't needed; the set-up is the same as Frame Control's (MIT, saphid).

Game data: with "Install also copies Echo's game data" ticked (the default), Install
also downloads Echo's game data (about 900 MB, once; from the same mirrors as the
Echo VR installer app) and copies it to Android/media/com.readyatdawn.r15/files on
the headset, where Echo reads it, plus the installer's asset patches. A headset that
already has game data is left as it is. Your config.json (login) is never touched.

Logs: "Save logs" in the window (everything Echo logged, the crash log and the
OpenXR runtime's lines; on Steam Frame, Echo must be running). adb (Google's
platform-tools) installs itself when the window opens.

The patch changes three things in the APK: lib/arm64-v8a/libvrapi.so (VrApi on
OpenXR), lib/arm64-v8a/libopenxr_loader.so (Khronos OpenXR loader) and the manifest
(launcher entry for Steam Frame, OpenXR permissions). The Steam Frame build also
changes libr15.so and libassetpatch.so (game data folder, Vulkan fixes) and replaces
lib/arm64-v8a/libovrplatformloader.so with a stand-in for Meta's Platform SDK, which
the Frame doesn't have. Everything else is copied as is.
"""


def run(cmd, **kw):
    print(">", " ".join(cmd))
    subprocess.check_call(cmd, **kw)


def notices():
    def read(*p):
        with open(os.path.join(*p), encoding="utf-8", errors="replace") as f:
            return f.read()
    parts = [
        "EchoFrame -- third-party notices\n",
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
    libs = [os.path.join(BUILD, f) for f in ("libvrapi.so", "libopenxr_loader.so", "libovrplatformloader.so")]
    missing = [p for p in libs if not os.path.isfile(p)]
    if missing:
        sys.exit("missing runtime libraries (run without --no-build):\n  " + "\n  ".join(missing))

    os.makedirs(REL, exist_ok=True)
    work = os.path.join(BUILD, "pyinstaller")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    ico = os.path.join(ROOT, "patcher", "echoframe.ico")   # tools/make_icon.py
    png = os.path.join(ROOT, "patcher", "echoframe.png")
    version_file = os.path.join(work, "VERSION")
    with open(version_file, "w") as f:
        f.write(VERSION + "\n")
    name = f"EchoFrame-v{VERSION}"
    sep = os.pathsep
    patcher = os.path.join(ROOT, "patcher")
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
         "--name", name, "--icon", ico, "--distpath", REL, "--workpath", os.path.join(work, "work"),
         "--specpath", work, "--paths", patcher,
         *[a for lib in libs for a in ("--add-data", f"{lib}{sep}runtime")],
         "--add-data", f"{ico}{sep}.", *[a for px in (44, 55, 66, 88) for a in ("--add-data", f"{png[:-4]}-{px}.png{sep}.")],
         "--add-data", f"{png}{sep}.", "--add-data", f"{os.path.join(ROOT, 'patcher', 'art')}{sep}art", "--add-data", f"{version_file}{sep}.",
         "--hidden-import", "patch", "--hidden-import", "device", "--hidden-import", "axml", "--hidden-import", "buttons", "--hidden-import", "minidump", "--hidden-import", "frame_setup",
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
