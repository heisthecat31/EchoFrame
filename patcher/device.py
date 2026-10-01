"""Headset side of the patcher: find adb, install the patched APK, start Echo, save logs.

Works with any Android headset adb can see (Quest; Steam Frame's Android container where
it exposes adb). Logs are filtered to EchoQuestXR's and the OpenXR runtime's own tags:
Echo's RAD log is left out on purpose, because it prints the community-server login
(including a password) in plain text.
"""
import io
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE = "com.readyatdawn.r15"
ACTIVITY = "com.oculus.gles3jni.MainActivity"
# Google's official Android platform-tools (adb)
PLATFORM_TOOLS_URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
LOG_TAGS = ["EchoQuestXR:V", "OpenXR:I", "OpenXR-Loader:V", "openxr_loader:V", "AndroidRuntime:E",
            "DEBUG:V", "libc:F", "*:S"]
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0   # CREATE_NO_WINDOW: no console flashes


def find_adb():
    """adb from the patcher folder, PATH, or the usual Android SDK places; None if missing."""
    exe = "adb.exe" if sys.platform == "win32" else "adb"
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [os.path.join(HERE, "platform-tools", exe), shutil.which("adb") or ""]
    for root in (os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
                 os.path.join(local, "Android", "Sdk") if local else None, r"J:\AndroidSDK", r"C:\Android\Sdk"):
        if root:
            candidates.append(os.path.join(root, "platform-tools", exe))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def download_adb(log=print):
    """Fetches Google's platform-tools into patcher/platform-tools/. Returns the adb path."""
    log("Downloading Android platform-tools from Google...")
    with urllib.request.urlopen(PLATFORM_TOOLS_URL, timeout=60) as r:
        data = r.read()
    zipfile.ZipFile(io.BytesIO(data)).extractall(HERE)   # creates platform-tools/
    adb = find_adb()
    if not adb:
        raise RuntimeError("platform-tools downloaded, but adb isn't in it")
    log(f"adb ready: {adb}")
    return adb


def run(adb, *args, timeout=120):
    p = subprocess.run([adb, *args], capture_output=True, text=True, timeout=timeout,
                       creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout + p.stderr).strip()


def devices(adb):
    """[(serial, model, state)] of attached devices."""
    code, out = run(adb, "devices", "-l", timeout=15)
    found = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and not line.startswith("*"):
            model = next((p.split(":", 1)[1] for p in parts if p.startswith("model:")), parts[0])
            found.append((parts[0], model.replace("_", " "), parts[1]))
    return found


def installed(adb, serial):
    code, out = run(adb, "-s", serial, "shell", "pm", "list", "packages", PACKAGE, timeout=20)
    return f"package:{PACKAGE}" in out.split()


def install(adb, serial, apk):
    """('ok' | 'signature' | 'error', message). 'signature': a copy signed with another key
    is installed, so it has to be uninstalled first (the caller asks the player)."""
    code, out = run(adb, "-s", serial, "install", "-r", apk, timeout=600)
    if code == 0 and "Success" in out:
        return "ok", "Installed."
    if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in out or "signatures do not match" in out.lower():
        return "signature", out.splitlines()[-1] if out else "different signing key"
    return "error", out.splitlines()[-1] if out else f"adb install failed ({code})"


def uninstall(adb, serial):
    code, out = run(adb, "-s", serial, "uninstall", PACKAGE, timeout=120)
    return code == 0 and "Success" in out, out


def launch(adb, serial):
    """Restarts Echo, with the device log cleared first so saved logs cover this run."""
    run(adb, "-s", serial, "shell", "am", "force-stop", PACKAGE, timeout=20)
    run(adb, "-s", serial, "logcat", "-c", timeout=20)
    code, out = run(adb, "-s", serial, "shell", "am", "start", "-n", f"{PACKAGE}/{ACTIVITY}", timeout=30)
    return code == 0 and "Error" not in out, out


def save_logs(adb, serial, path):
    """Writes what's in the device log for EchoQuestXR and OpenXR (no RAD lines) to `path`."""
    code, out = run(adb, "-s", serial, "logcat", "-d", "-v", "time", *LOG_TAGS, timeout=60)
    lines = [l for l in out.splitlines() if "/RAD" not in l]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return len(lines)


def clear_logs(adb, serial):
    run(adb, "-s", serial, "logcat", "-c", timeout=20)
