"""Headset side of the patcher: find adb, install the patched APK, copy Echo's game data
over, start Echo, save logs.

Works with any Android headset adb can see (Quest; Steam Frame's Android container where
it exposes adb). Save logs keeps everything Echo's process logged (its own log included),
the crash log, and the OpenXR runtime's lines.

Game data comes from the same mirrors the Echo VR installer app uses
(github.com/heisthecat31/EchoVR-Installer): _data.zip goes to Echo's own media folder,
/sdcard/Android/media/com.readyatdawn.r15/files/, which is where Echo reads it on any
Android (Quest, or Steam Frame's Lepton container). The asset patches from the installer's
update manifest go next to it. config.json (the community-server login) is never touched.
"""
import hashlib
import io
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
# Where a downloaded adb lives: next to the scripts, or for the packaged .exe (which
# unpacks to a temporary folder) in %LOCALAPPDATA%\EchoQuestXR.
DATA = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "EchoQuestXR") if getattr(sys, "frozen", False) else HERE
PACKAGE = "com.readyatdawn.r15"
ACTIVITY = "com.oculus.gles3jni.MainActivity"
# Google's official Android platform-tools (adb)
PLATFORM_TOOLS_URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
MEDIA = f"/sdcard/Android/media/{PACKAGE}"
UA = {"User-Agent": "EchoVR-Installer"}   # the game-data mirrors refuse Python's default agent
GAME_DATA_URLS = ["https://mia.cdn.echo.taxi/_data.zip", "https://files.echovr.de/_data.zip"]
PATCH_MANIFEST_URL = "https://files.echovr.de/updates/quest/update.manifest"
SAFE_PATH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*$")
# Android tools by full path too: a headset shell whose PATH lacks /system/bin (seen on
# Steam Frame) answers "logcat: not found" otherwise
SYS = "/system/bin/"
LOG_TAGS =["EchoQuestXR:V", "OpenXR:I", "OpenXR-Loader:V", "openxr_loader:V", "AndroidRuntime:E",
            "DEBUG:V", "libc:F", "*:S"]
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0   # CREATE_NO_WINDOW: no console flashes


def find_adb():
    """adb from the patcher folder, PATH, or the usual Android SDK places; None if missing."""
    exe = "adb.exe" if sys.platform == "win32" else "adb"
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [os.path.join(DATA, "platform-tools", exe), shutil.which("adb") or ""]
    for root in (os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
                 os.path.join(local, "Android", "Sdk") if local else None):
        if root:
            candidates.append(os.path.join(root, "platform-tools", exe))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def download_adb(log=print):
    """Fetches Google's platform-tools into <DATA>/platform-tools/. Returns the adb path."""
    log("Downloading Android platform-tools from Google...")
    with urllib.request.urlopen(urllib.request.Request(PLATFORM_TOOLS_URL, headers=UA), timeout=60) as r:
        data = r.read()
    os.makedirs(DATA, exist_ok=True)
    zipfile.ZipFile(io.BytesIO(data)).extractall(DATA)   # creates platform-tools/
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


def connect(adb, target):
    """adb over the network (Steam Frame's Lepton apps each have an adb port): 'host:port'.
    Returns (ok, message)."""
    target = target.strip()
    if ":" not in target:
        target += ":5555"
    code, out = run(adb, "connect", target, timeout=20)
    ok = "connected to" in out and "cannot" not in out and "failed" not in out
    return ok, out.splitlines()[-1] if out else f"adb connect failed ({code})"


def not_found(out):
    low = out.lower()
    return any(s in low for s in ("not found", "inaccessible", "no such file or directory"))


# What a headset shell is, for when it isn't the Android shell adb normally gives (on Steam
# Frame it can be a plain Linux shell outside Lepton's Android): system, where Android and
# its tools are, what runs. Read-only.
PROBE = "; ".join([
    "echo '## shell'", "echo PATH=$PATH", "id", "uname -a", "cat /etc/os-release 2>/dev/null | head -4",
    "echo '## tools'", "for t in logcat am pm getprop waydroid lxc-attach lxc-ls nsenter podman docker; "
    "do printf '%s: ' $t; command -v $t || echo -; done",
    "echo '## root'", "ls / 2>&1",
    "echo '## android dirs'", "ls -d /system /system/bin /data /sdcard /storage /apex /var/lib/waydroid "
    "/var/lib/lepton* /opt/* 2>&1",
    "echo '## storage'", "echo HOME=$HOME; pwd", "ls -ld /sdcard /sdcard/ /sdcard/Android /sdcard/Android/media /storage/emulated/0 2>&1",
    f"ls -la {MEDIA} 2>&1 | head -10",
    "mount 2>/dev/null | grep -iE 'sdcard|storage|media|/data|emulated|lepton|android' | head -25",
    "df 2>/dev/null | head -20",
    "for d in /sdcard/Android/media /storage/emulated/0/Android/media /data/local/tmp /data/media/0 $HOME /tmp; "
    "do if touch \"$d/.eqx-probe\" 2>/dev/null; then rm -f \"$d/.eqx-probe\"; echo \"writable: $d\"; "
    "else echo \"read-only/missing: $d\"; fi; done",
    "echo '## logcat binaries'", "find / \\( -path /proc -o -path /sys -o -path /dev \\) -prune -o -maxdepth 7 "
    "-name logcat -type f -print 2>/dev/null | head -10",
    "echo '## echo media folders'", "find / \\( -path /proc -o -path /sys -o -path /dev \\) -prune -o -maxdepth 9 "
    "-type d -name com.readyatdawn.r15 -print 2>/dev/null | head -10",
    "echo '## processes'", "ps -eo pid,user,comm 2>/dev/null | grep -iE 'lepton|android|logd|adbd|zygote|surfaceflinger|"
    "lxc|r15|crun|bwrap' | head -30",
])

# Steam Frame: adb lands on SteamOS, and Lepton keeps each Android app's storage under
# ~/Applications/Android/<package>. Where Echo's storage and Android's log are. Read-only;
# journal lines are limited to EchoQuestXR/OpenXR/Lepton and never Echo's RAD log.
LEPTON = "$HOME/Applications/Android"
LEPTON_CONTAINER = f"lepton-steamlaunch-$(cat {LEPTON}/{PACKAGE}/instance.id)"   # launch.sh's name
PROBE_STEAMOS = "; ".join([
    "echo '## lepton apps'", f"ls -la {LEPTON} 2>&1 | head -20",
    "echo '## echo storage'", f"find {LEPTON}/{PACKAGE} -maxdepth 6 2>&1 | head -80",
    f"du -s {LEPTON}/{PACKAGE} 2>&1",
    f"find {LEPTON}/{PACKAGE} -maxdepth 6 -type d \\( -name media -o -name Android -o -name files "
    "-o -name sdcard -o -name emulated \\) 2>/dev/null | head -20",
    "echo '## containers'", "podman ps -a --format '{{.ID}} {{.Image}} {{.Names}} {{.Status}}' 2>&1 | head -10",
    "echo '## android-related processes'", "ps -eo pid,user,args 2>/dev/null | grep -iE "
    "'android|lepton|r15|binder|bwrap|crun|conmon|podman|zygote|logd' | grep -v grep | cut -c1-200 | head -30",
    "echo '## log files'", "find $HOME /tmp /run/user/$(id -u) /var/log -maxdepth 6 \\( -iname '*lepton*' "
    "-o -iname '*logcat*' -o -iname '*android*.log' \\) 2>/dev/null | head -30",
    "echo '## launch.sh'", f"head -c 4000 {LEPTON}/{PACKAGE}/launch.sh 2>&1",
    "echo; echo '## meta.json'", f"head -c 3000 {LEPTON}/{PACKAGE}/meta.json 2>&1",
    "echo; echo '## lepton install'", "find / \\( -path /proc -o -path /sys -o -path /dev -o -path /home \\) -prune "
    "-o -maxdepth 5 -iname '*lepton*' -print 2>/dev/null | head -20",
    "find $HOME -maxdepth 7 -iname '*lepton*' 2>/dev/null | head -30",
    "echo '## echo data, deeper'", "find $HOME -maxdepth 16 \\( -name com.readyatdawn.r15 -o -name _data \\) "
    f"2>/dev/null | grep -vx \"{LEPTON}/{PACKAGE}\" | head -20",
    "echo '## disk images'", "find $HOME /var -maxdepth 10 \\( -name '*.img' -o -name '*.qcow2' -o -name '*.erofs' "
    "-o -name '*.sfs' \\) -size +50M 2>/dev/null | head -20",
    # launch.sh: Echo runs in podman container lepton-steamlaunch-<instance.id>, its data in
    # compatdata/<id>/internal, and only ~/.local/share/Steam is mounted in the container
    f"ID=$(cat {LEPTON}/{PACKAGE}/instance.id 2>/dev/null); C=lepton-steamlaunch-$ID; "
    "S=$HOME/.local/share/Steam; echo instance=$ID container=$C",
    "echo '## steam dir'", "readlink -f $S; ls -la $HOME/.local/share/ 2>&1 | grep -i steam",
    "echo '## lepton (followed)'", "ls -la $S/steamapps/common/Lepton/ 2>&1 | head -30",
    # launch.sh only looks in the home Steam library; Lepton is Steam app 3056000
    "echo '## steam libraries'", "grep -E '\"path\"|\"3056000\"' $S/steamapps/libraryfolders.vdf 2>&1 | head -20",
    "ls $S/steamapps/appmanifest_3056000.acf 2>&1; ls $S/steamapps/common/ 2>&1 | head -40",
    "for L in $(grep '\"path\"' $S/steamapps/libraryfolders.vdf 2>/dev/null | cut -d'\"' -f4); do "
    "ls -d \"$L/steamapps/common/Lepton\" \"$L/steamapps/appmanifest_3056000.acf\" 2>/dev/null; done",
    "find / \\( -path /proc -o -path /sys -o -path /dev \\) -prune -o -maxdepth 9 -type f -name lepton -print "
    "2>/dev/null | head -10",
    "echo '## compatdata'", "find -L $S/steamapps/compatdata/$ID -maxdepth 8 -type d 2>/dev/null | head -100",
    "echo '## echo data (followed)'", "find -L $S -maxdepth 14 \\( -name com.readyatdawn.r15 -o -name _data \\) "
    "-type d 2>/dev/null | head -20",
    "echo '## container'", "podman ps -a --format '{{.Names}} {{.Status}}' 2>&1 | head",
    "podman inspect --format '{{.State.Running}} {{range .Mounts}}{{.Source}} -> {{.Destination}}; {{end}}' $C 2>&1 "
    "| head -c 3000",
    "echo; echo '## inside the container'", "podman exec $C sh -c 'id; ls -ld /sdcard /storage/emulated/0 "
    f"/sdcard/Android/media {MEDIA}; ls -la {MEDIA}; touch {MEDIA}/.eqx && rm {MEDIA}/.eqx && echo media-writable; "
    "mount | grep -iE \"sdcard|storage|media|/data|Steam\" | head -30' 2>&1 | head -60",
    "echo '## journal'", "journalctl --user -n 3000 --no-pager 2>/dev/null | grep -E "
    "'EchoQuestXR|OpenXR|openxr|[Ll]epton|AndroidRuntime|readyatdawn' | grep -v RAD | tail -60",
])


class Frame:
    """A Steam Frame seen through its SteamOS adb: Echo set up (by the patcher, frame_setup.py, or Frame Control) as its own
    Lepton instance (~/Applications/Android/<package>: app.apk, instance.id, shortcut.id).
    `data` is Echo's private folder, /data/data/<package> inside Lepton, which survives
    restarts and APK updates; when /sdcard has no game data, EchoQuestXR's libvrapi points
    Echo there. It belongs to the container's user namespace, so writes go through
    `podman unshare` (where SteamOS's user is root)."""

    def __init__(self, home, instance, shortcut):
        self.home, self.instance, self.shortcut = home, instance, shortcut
        self.app_dir = f"{home}/Applications/Android/{PACKAGE}"
        self.data = f"{home}/.local/share/Steam/steamapps/compatdata/{instance}/internal/{PACKAGE}"
        self.stage = f"{home}/.cache/echoquestxr-copy"


def frame_info(adb, serial):
    """A Frame for a Steam Frame's SteamOS adb (instance None if Echo isn't set
    up), None for an Android headset."""
    code, out = shell(adb, serial, "grep -q '^ID=steamos' /etc/os-release 2>/dev/null && echo FRAME $HOME "
                      f"$(cat {LEPTON}/{PACKAGE}/instance.id 2>/dev/null || echo -) "
                      f"$(cat {LEPTON}/{PACKAGE}/shortcut.id 2>/dev/null || echo -)", timeout=20)
    parts = out.split()
    if len(parts) < 4 or parts[0] != "FRAME":
        return None
    num = lambda s: s if s.isdigit() else None
    return Frame(parts[1], num(parts[2]), num(parts[3]))


def frame_ready(adb, serial, fr):
    """Echo's launcher, Lepton and Echo's data folder are all on this Frame."""
    code, out = shell(adb, serial, f"[ -x '{fr.app_dir}/launch.sh' ] && [ -x $HOME/.local/share/Steam/steamapps/common/"
                                   f"Lepton/lepton ] && podman unshare test -d '{fr.data}' && echo ready", timeout=30)
    return "ready" in out


def tool(adb, serial, name, *args, timeout=60):
    """Runs an Android tool (pm, am, logcat...) on the headset, by full path if the shell's
    PATH doesn't have it."""
    code, out = run(adb, "-s", serial, "shell", name, *args, timeout=timeout)
    if not_found(out):
        code, out = run(adb, "-s", serial, "shell", SYS + name, *args, timeout=timeout)
    return code, out


def installed(adb, serial):
    code, out = tool(adb, serial, "pm", "list", "packages", PACKAGE, timeout=20)
    return f"package:{PACKAGE}" in out.split()


def install(adb, serial, apk, say=lambda f, t: None, log=print):
    """('ok' | 'signature' | 'error', message). 'signature': a copy signed with another key
    is installed, so it has to be uninstalled first (the caller asks the player).
    say(fraction or None, text): progress while a Steam Frame is set up."""
    fr = frame_info(adb, serial)
    if fr and not (fr.instance and fr.shortcut and frame_ready(adb, serial, fr)):
        import frame_setup   # first time on this Frame: Lepton, launcher, Steam shortcut, data folder
        try:
            frame_setup.set_up(adb, serial, apk, say, log)
        except IOError as e:
            return "error", str(e)
        return "ok", "Installed. Press Launch: it restarts Echo so Lepton uses the new APK."
    if fr:   # Steam Frame: Lepton runs ~/Applications/Android/<package>/app.apk; it rebuilds on change
        part = f"{fr.app_dir}/app.apk.part"
        code, out = run(adb, "-s", serial, "push", apk, part, timeout=900)
        if code == 0:
            code, out = shell(adb, serial, f"mv -f '{part}' '{fr.app_dir}/app.apk' && echo moved")
        if "moved" in out:
            return "ok", "Installed. Press Launch: it restarts Echo so Lepton uses the new APK."
        return "error", out.strip().splitlines()[-1] if out.strip() else f"copy failed ({code})"
    code, out = run(adb, "-s", serial, "install", "-r", apk, timeout=600)
    if not_found(out):   # adb install couldn't run the package manager: copy, then pm by path
        tmp = "/data/local/tmp/echoquestxr.apk"
        code, out = run(adb, "-s", serial, "push", apk, tmp, timeout=600)
        if code == 0:
            code, out = tool(adb, serial, "pm", "install", "-r", tmp, timeout=600)
            run(adb, "-s", serial, "shell", "rm", "-f", tmp, timeout=20)
    if code == 0 and "Success" in out:
        return "ok", "Installed."
    if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in out or "signatures do not match" in out.lower():
        return "signature", out.splitlines()[-1] if out else "different signing key"
    return "error", out.splitlines()[-1] if out else f"adb install failed ({code})"


def uninstall(adb, serial):
    code, out = tool(adb, serial, "pm", "uninstall", PACKAGE, timeout=120)
    return "Success" in out, out


def launch(adb, serial):
    """Restarts Echo, with the device log cleared first so saved logs cover this run."""
    fr = frame_info(adb, serial)
    if fr:   # Steam Frame: restart Echo's Lepton instance, then start its Steam shortcut
        if not fr.shortcut:
            return False, "No Steam shortcut for Echo on this Frame: start it from the Steam library."
        # Echo's process stays up after a start-up error, so its container (and Steam's
        # "running") does too, and a new app.apk is only used by a fresh container
        container = f"lepton-steamlaunch-{fr.instance}"
        shell(adb, serial, f"podman stop -t 5 {container} >/dev/null 2>&1; "
                           f"for i in 1 2 3 4 5 6 7 8 9 10; do podman container exists {container} || break; "
                           "sleep 1; done", timeout=60)
        game = (int(fr.shortcut) << 32) | 0x02000000
        code, out = shell(adb, serial, f"nohup steam steam://rungameid/{game} >/dev/null 2>&1 & echo started")
        return "started" in out, out
    tool(adb, serial, "am", "force-stop", PACKAGE, timeout=20)
    clear_logs(adb, serial)
    code, out = tool(adb, serial, "am", "start", "-n", f"{PACKAGE}/{ACTIVITY}", timeout=30)
    return code == 0 and "Error" not in out and not not_found(out), out


# Everything Echo's process logged (its own log included), the crash log, and the OpenXR
# runtime's lines from other processes. Runs in an Android shell (Quest, or Lepton's container).
LOG_SCRIPT = "; ".join([
    f"P=$(pidof {PACKAGE})",
    "if [ -n \"$P\" ]; then echo \"=== Echo VR (pid $P)\"; logcat -d -v time --pid=$P; "
    "else echo '=== Echo VR is not running: the last 4000 lines of everything'; logcat -d -v time -t 4000; fi",
    "echo '=== crash log'", "logcat -d -v time -b crash",
    "echo '=== EchoQuestXR and OpenXR, all processes'", "logcat -d -v time " + " ".join(f"'{t}'" for t in LOG_TAGS),
    # if Echo hangs (no picture, "Deadlock detected!"): where each of its threads is, and
    # whether Android resumed its activity and gave it a window. Needs root (Lepton's
    # container); a Quest's shell gets "permission denied" here.
    "if [ -n \"$P\" ]; then echo '=== Echo activity and window'; "
    "dumpsys activity activities 2>&1 | grep -iE 'readyatdawn|mResumed|ResumedActivity' | head -20; "
    "dumpsys window windows 2>&1 | grep -iE 'readyatdawn|mCurrentFocus|mFocusedApp' | head -20; "
    "echo '=== Echo threads (debuggerd -b)'; debuggerd -b $P 2>&1 | head -3000; fi",
])


# Steam Frame, once Echo's container has stopped: Lepton's saved logcats, the instance's log,
# Echo's crash dumps (listed) and the journal lines about Echo. Runs on SteamOS.
FRAME_KEPT_LOGS = "; ".join([
    f"S=$HOME/.local/share/Steam; ID=$(cat {LEPTON}/{PACKAGE}/instance.id 2>/dev/null)",
    f"D=$S/steamapps/compatdata/$ID/internal/{PACKAGE}",
    "echo '=== Lepton logcats'", "ls -lt $S/logs/lepton-logcats 2>&1 | head -10",
    "for f in $(find $S/logs/lepton-logcats -type f -printf '%T@ %p\\n' 2>/dev/null | sort -rn | head -2 | cut -d' ' -f2-); "
    # Echo's, the runtime's and the driver's lines first (the whole run), then the unfiltered end
    "do echo \"=== $f (Echo, EchoQuestXR, Vulkan and crash lines)\"; "
    "grep -a -E 'EchoQuestXR|RAD |RAD:|vulkan|MESA|Fossilize|fdm|AssetPatch|OpenXR|DEBUG|libc|AndroidRuntime' \"$f\" "
    "| tail -c 3000000; echo \"=== $f (the end, unfiltered)\"; tail -c 300000 \"$f\"; echo; done",
    "echo \"=== lepton-steamlaunch-$ID.log\"", "tail -c 200000 $S/logs/lepton-steamlaunch-$ID.log 2>&1",
    "echo; echo '=== Echo crash dumps'", "podman unshare ls -la $D/files/_temp/crashes 2>&1 | head -20",
    "echo '=== journal: Echo, its SteamVR client, crashes'",
    "(journalctl -n 6000 --no-pager -o short-precise 2>/dev/null; journalctl --user -n 6000 --no-pager -o short-precise "
    "2>/dev/null) | grep -E 'readyatdawn|coredump|xrclient|lepton' | grep -vE 'steamvr_logs_to_journald|podman\\[' | tail -200",
])

# Steam Frame: SteamVR's own logs, for how it paces Echo's frames (it ran Echo at half rate,
# 36 Hz, with the GPU taking under 2 ms a frame): the end of the compositor's log, and the
# server's lines about timing, throttling, reprojection and the refresh rate. Runs on SteamOS.
FRAME_STEAMVR_LOGS = "; ".join([
    "L=$HOME/.local/share/Steam/logs",
    "echo '=== SteamVR compositor (vrcompositor.txt, the end)'", "tail -n 500 $L/vrcompositor.txt 2>&1",
    "echo '=== SteamVR server (vrserver.txt: timing, throttling, reprojection, refresh rate, Echo)'",
    "tail -n 6000 $L/vrserver.txt 2>/dev/null | grep -aiE 'throttl|reproject|motion smooth|refresh|hz|frame ?tim|"
    "late|dropped|readyatdawn|lepton|prediction' | tail -n 200",
    "echo '=== SteamVR client log for Echo (xrclient_com.readyatdawn.r15.txt, the end)'",
    "tail -n 300 $L/xrclient_com.readyatdawn.r15.txt 2>&1",
    "echo '=== SteamVR settings: throttling, motion smoothing, refresh, per-app'",
    "grep -aiE 'throttl|motionSmooth|reproject|refresh|framesTo|prediction|readyatdawn|lepton|preferredRefresh|"
    "allowSupersample|supersample|steam.app|\"apps\"' $HOME/.config/openvr/config/steamvr.vrsettings $HOME/.local/share/Steam/config/steamvr.vrsettings 2>&1 | head -60",
    "echo '=== vrclient.so (SteamVR in Lepton): its strings about frame timing, fences, environment'",
    "V=$(ls /opt/steamvr/bin/androidarm64/vrclient.so $HOME/.local/share/Steam/steamapps/common/SteamVR/bin/androidarm64/"
    "vrclient.so 2>/dev/null | head -1); echo \"$V\"; [ -n \"$V\" ] && grep -a -o -E '[A-Za-z_][A-Za-z0-9_ %:.,()/-]{5,90}' \"$V\" "
    "| grep -iE 'throttl|timeline|semaphore|gpu fence|fence|frame ?tim|vsync|interval|prediction|Disable[A-Z]|Enable[A-Z]|"
    "Force[A-Z]|refresh|reproject|late|half|halv|LEPTON|getenv' | sort -u | head -150",
    "echo '=== SteamOS microphone (default source, volume)'",
    "(export XDG_RUNTIME_DIR=/run/user/$(id -u); pactl get-default-source; pactl get-source-volume @DEFAULT_SOURCE@) 2>&1",
    "echo '=== SteamVR logs present'", "ls -lt $L 2>&1 | head -25",
])

# Steam Frame: Echo's newest crash dump: its name, then the file in base64 (the Frame's adb
# is a SteamOS shell, and the folder belongs to Echo's user inside the container).
FRAME_CRASH_DUMP = "; ".join([
    f"S=$HOME/.local/share/Steam; ID=$(cat {LEPTON}/{PACKAGE}/instance.id 2>/dev/null)",
    f"C=$S/steamapps/compatdata/$ID/internal/{PACKAGE}/files/_temp/crashes",
    "F=$(podman unshare sh -c 'ls -t \"$0\"/*.dmp 2>/dev/null | head -1' \"$C\")",
    "echo \"$F\"", "[ -n \"$F\" ] && podman unshare base64 -w0 \"$F\"",
])


def frame_crash_dump(adb, serial, path):
    """Copies Echo's newest crash dump from the Frame to <path>-crash.dmp and returns what it
    says (the signal, where it crashed, the stack) as lines for the log."""
    import base64
    import minidump   # next to this file
    code, out = shell(adb, serial, FRAME_CRASH_DUMP, timeout=120)
    name, _, data = out.strip().partition("\n")
    if not name.endswith(".dmp") or not data.strip():
        return ["(no crash dump found)"]
    try:
        dump = base64.b64decode("".join(data.split()))
    except ValueError as e:
        return [f"{name}: couldn't copy it ({e})"]
    local = os.path.splitext(path)[0] + "-crash.dmp"
    with open(local, "wb") as f:
        f.write(dump)
    lines = [f"{name} ({len(dump)} bytes, saved as {os.path.basename(local)})"]
    try:
        return lines + minidump.summary(dump)
    except (minidump.MinidumpError, struct.error) as e:
        return lines + [f"couldn't read it: {e}"]



def save_logs(adb, serial, path):
    """Writes Echo's log (everything its process logged), the crash log and the OpenXR lines to
    `path`. If the headset's shell can't run logcat at all, writes what that shell is instead,
    so the log says why. Returns the number of lines."""
    script = f"PATH=$PATH:{SYS.rstrip('/')}; " + LOG_SCRIPT
    code, has = shell(adb, serial, f"PATH=$PATH:{SYS.rstrip('/')}; command -v logcat", timeout=20)
    android = "logcat" in has
    if android:
        code, out = shell(adb, serial, script, timeout=90)
    else:
        out = has.strip() or "logcat: not found"
    if not android and frame_info(adb, serial):   # Steam Frame: Android runs in Echo's Lepton container
        code, got = shell(adb, serial, f"podman exec {LEPTON_CONTAINER} sh -c {shlex.quote(script)} 2>&1", timeout=90)
        if code == 0 and got.strip() and "no such container" not in got and "Error:" not in got[:200]:
            out, android = "(logs from Echo's Lepton container)\n" + got, True
        else:   # it has stopped (Echo exited or crashed): what Lepton and Steam kept on SteamOS
            code, kept = shell(adb, serial, FRAME_KEPT_LOGS, timeout=120)
            out, android = ("(Echo's Lepton container isn't running: the logs Lepton and Steam kept)\n"
                            + kept.replace("\0", "")), True
            out += "\n=== Echo's newest crash dump\n" + "\n".join(frame_crash_dump(adb, serial, path)) + "\n"
        code, steamvr = shell(adb, serial, FRAME_STEAMVR_LOGS, timeout=60)   # why SteamVR paces Echo as it does
        out += "\n" + steamvr.replace("\0", "")
        out += "\n=== SteamVR throttle settings (vrcmd)\n" + frame_steamvr_throttle(adb, serial, set_it=False) + "\n"
    if not android:
        first = out.strip().splitlines()[-1] if out.strip() else "no output"
        code, diag = run(adb, "-s", serial, "shell", PROBE, timeout=90)
        if "steamos" in diag.lower():   # Steam Frame: look into Lepton too
            code, more = run(adb, "-s", serial, "shell", PROBE_STEAMOS, timeout=300)
            diag += "\n" + more
        found = diag.split("## logcat binaries", 1)[-1].split("##", 1)[0].split()
        tried = []
        for exe in (f for f in found if f.startswith("/")):   # a logcat elsewhere (inside a container)
            code, got = run(adb, "-s", serial, "shell", exe, "-d", "-v", "time", *LOG_TAGS, timeout=60)
            tried.append(f"{exe}: {(got.strip().splitlines() or ['no output'])[0][:200]}")
            if code == 0 and not not_found(got):
                out = f"(logcat from {exe})\n" + got
                break
        else:
            out = ("logcat couldn't run through this adb connection (" + first + ").\n"
                   "It isn't Android's shell. What it is:\n\n" + diag +
                   ("\n## other logcats tried\n" + "\n".join(tried) if tried else ""))
    lines = out.splitlines()
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return len(lines)


def clear_logs(adb, serial):
    tool(adb, serial, "logcat", "-c", timeout=20)


# ---------------------------------------------------------------------- game data
def fetch(url, path, progress=None):
    """Downloads `url` to `path`, resuming a cut-off <path>.partial. progress(done, total)."""
    tmp = path + ".partial"
    have = os.path.getsize(tmp) if os.path.isfile(tmp) else 0
    headers = dict(UA, **({"Range": f"bytes={have}-"} if have else {}))
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
        if r.status != 206:   # no resume from this server: start over
            have = 0
        total = have + int(r.headers.get("Content-Length") or 0)
        done = have
        with open(tmp, "ab" if have else "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
                done += len(b)
                if progress:
                    progress(done, total)
    if total and done != total:
        raise IOError(f"download cut short ({done} of {total} bytes); try again to resume")
    os.replace(tmp, path)


def shell(adb, serial, cmd, timeout=60):
    return run(adb, "-s", serial, "shell", cmd, timeout=timeout)


def shell_stream(adb, serial, cmd):
    """adb shell cmd as a running process (stdout and stderr together, line by line), for the
    Advanced terminal; no input, so interactive commands won't work."""
    return subprocess.Popen([adb, "-s", serial, "shell", cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", bufsize=1,
                            creationflags=NO_WINDOW)


def push(adb, serial, local, remote, progress=None):
    """adb push one file, reporting progress(bytes on the headset) about once a second. Gives
    up if nothing arrives for two minutes."""
    p = subprocess.Popen([adb, "-s", serial, "push", local, remote], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
    import time
    last, moved = -1, time.monotonic()
    while p.poll() is None:
        try:
            p.wait(1.5)
        except subprocess.TimeoutExpired:
            code, out = shell(adb, serial, f"stat -c %s '{remote}' 2>/dev/null", timeout=15)
            size = int(out.strip()) if out.strip().isdigit() else -1
            if size != last:
                last, moved = size, time.monotonic()
                if progress and size >= 0:
                    progress(size)
            elif time.monotonic() - moved > 120:
                p.kill()
                raise IOError(f"copying {os.path.basename(local)} to {remote} stalled (no progress for 2 minutes)")
    out = p.stdout.read().strip()
    if p.returncode != 0:
        raise IOError(f"copying {os.path.basename(local)} failed: {out.splitlines()[-1] if out else p.returncode}")


class Storage:
    """Echo's storage folder on a headset and how to write it.
    Android (Quest): /sdcard/Android/media/<package>, through adb's Android shell.
    Steam Frame: Echo's private folder in its Lepton instance (Frame.data), through SteamOS's
    adb: files are pushed to ~/.cache first, then moved in and given to Echo's user with
    `podman unshare`. Either way a copy lands under its final name only once complete."""

    def __init__(self, adb, serial):
        self.adb, self.serial = adb, serial
        self.frame = frame_info(adb, serial)
        if self.frame:
            if not self.frame.instance:
                raise IOError("Echo VR isn't set up on this Frame yet: press Install (with the APK from step 3) "
                              "and the patcher sets it up.")
            self.base, self.stage = self.frame.data, self.frame.stage
        else:
            self.base, self.stage = MEDIA, f"{MEDIA}/files/.echoquestxr-copy"

    def sh(self, cmd, timeout=60):
        """A command on Echo's storage (on a Frame, inside the container's user namespace)."""
        if self.frame:
            cmd = "podman unshare sh -c " + shlex.quote(cmd)
        return shell(self.adb, self.serial, cmd, timeout=timeout)

    def check_writable(self):
        if self.frame:
            code, out = self.sh(f"test -d '{self.base}' && echo ok")
            if "ok" not in out:
                raise IOError("Echo hasn't made its data folder on this Frame yet: press Install with the APK from "
                              "step 3 and the patcher starts Echo once to make it.")
            code, out = shell(self.adb, self.serial, f"mkdir -p '{self.stage}' && echo writable")
        else:
            code, out = self.sh(f"mkdir -p '{self.stage}' && echo writable")
        if "writable" not in out:
            raise IOError("this adb connection can't write to Echo's storage on the headset "
                          f"({out.strip().splitlines()[-1] if out.strip() else 'no answer'}). "
                          "Press Save logs and send the file: it records what the connection can reach.")

    def sizes(self, rel):
        """{relative path: size} of the files under base/rel."""
        code, out = self.sh(f"cd '{self.base}' && find '{rel}' -type f -exec stat -c '%s %n' {{}} + 2>/dev/null",
                            timeout=120)
        found = {}
        for line in out.splitlines():
            size, _, name = line.partition(" ")
            if size.isdigit():
                found[name] = int(size)
        return found

    def has_game_data(self):
        """The installer's check: two folders with 2+ files each."""
        base = f"{self.base}/files/_data/5932408047/rad15/android"
        for sub in ("manifests", "packages"):
            code, out = self.sh(f"ls '{base}/{sub}' 2>/dev/null", timeout=20)
            if len(out.split()) < 2:
                return False
        return True

    def sha256(self, rel):
        code, out = self.sh(f"sha256sum '{self.base}/{rel}' 2>/dev/null")
        return (out.split() or [""])[0]

    def put(self, local, rel, progress=None):
        """Copies a local file to base/rel."""
        part = f"{self.stage}/{os.path.basename(rel)}"
        push(self.adb, self.serial, local, part, progress)
        dest = f"{self.base}/{rel}"
        # only what this copy made (still root's, the container's user namespace being root here):
        # a chown -R of the whole folder also re-owned Echo's own files, its logs among them
        own = (f" && find '{self.base}/{rel.split('/')[0]}' -user 0 -exec chown --reference='{self.base}' {{}} +"
               if self.frame else "")
        code, out = self.sh(f"mkdir -p '{dest.rsplit('/', 1)[0]}' && mv -f '{part}' '{dest}'{own} && echo moved")
        if "moved" not in out:
            raise IOError(f"couldn't move {rel} into place on the headset: {out.strip()}")

    def remove(self, rel):
        self.sh(f"rm -f '{self.base}/{rel}'")

    def done(self):
        if self.frame:
            shell(self.adb, self.serial, f"rm -rf '{self.stage}'")
        else:
            self.sh(f"rm -rf '{self.stage}'")


BUTTONS_FILE = "files/echoquestxr-buttons.txt"   # Steam Frame button layout, read when Echo starts


def read_buttons(adb, serial):
    """The button layout file's text on the headset ('' if there's none)."""
    store = Storage(adb, serial)
    code, out = store.sh(f"cat '{store.base}/{BUTTONS_FILE}' 2>/dev/null")
    return out


def write_buttons(adb, serial, text):
    """Writes the Steam Frame settings file (buttons.to_text); Echo uses it the next time it starts."""
    import tempfile
    store = Storage(adb, serial)
    store.check_writable()
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, newline="\n") as f:
        f.write(text)
        local = f.name
    try:
        store.put(local, BUTTONS_FILE)
    finally:
        os.remove(local)
    store.done()
    if store.frame:
        settings = dict(line.partition("=")[::2] for line in text.split() if "=" in line)
        # Sync=skip: SteamVR's client is told not to wait on the frame's GPU semaphore
        env = {"DisableTimelineSemaphoreWait": "1"} if settings.get("Sync") == "skip" else {}
        frame_launch_env(adb, serial, store.frame, env)
        if settings.get("Mic", "keep") != "keep":
            frame_mic_volume(adb, serial, settings["Mic"])
        return frame_steamvr_throttle(adb, serial, set_it=True)   # Echo's own SteamVR throttle: 0
    return ""


# SteamVR's frame throttling for Echo. SteamVR ran Echo at half rate (36 Hz, every other frame
# reprojected) from its first frame, whatever the GPU load: a throttle setting. SteamVR's client
# (vrclient) reads framesToThrottle, and powersaveFramesToThrottle when the SteamOS power
# profile is power-save, globally ("steamvr") and per app (Echo's app key, from SteamVR's log
# for Echo). vrcmd changes them in the running SteamVR (IVRSettings). Runs on SteamOS.
FRAME_ENV = "; ".join([   # also the Advanced terminal's set-up: $K is Echo's app key, $C is vrcmd
    "export XDG_RUNTIME_DIR=/run/user/$(id -u)",
    "K=$(grep -a -o 'App key after connect message:[^ ]*' $HOME/.local/share/Steam/logs/xrclient_com.readyatdawn.r15.txt "
    "2>/dev/null | tail -1 | cut -d: -f2)",
    "C=$(ls /opt/steamvr/bin/linuxarm64/vrcmd /opt/steamvr/bin/linux64/vrcmd "
    "$HOME/.local/share/Steam/steamapps/common/SteamVR/bin/linuxarm64/vrcmd 2>/dev/null | head -1)",
    "[ -n \"$C\" ] && export LD_LIBRARY_PATH=$(dirname \"$C\"):${LD_LIBRARY_PATH:-}",
])
FRAME_VRCMD = "; ".join([FRAME_ENV, "echo \"Echo's SteamVR app key: ${K:-unknown}; vrcmd: ${C:-not found}\""])
# vrcmd takes "section.key" as one argument (and prints "section.key=value"); Echo's app key
# has dots itself. Setting: "section.key=value", else "section.key" "value"; read back after.
FRAME_VRCMD_READ = "; ".join([
    "for k in framesToThrottle powersaveFramesToThrottle additionalFramesToPredict motionSmoothing; do "
    "echo \"steamvr.$k: $(timeout 10 \"$C\" --settings-int steamvr.$k 2>&1 | tail -1)\"; done",
    "[ -n \"$K\" ] && for k in framesToThrottle powersaveFramesToThrottle motionSmoothingOverride; do "
    "echo \"$K.$k: $(timeout 10 \"$C\" --settings-int \"$K.$k\" 2>&1 | tail -1)\"; done",
])
# The Frame read steamvr.powersaveFramesToThrottle=1 (SteamOS's power-save profile: every other
# frame, 36 of 72 Hz) and nothing per app: SteamVR has no per-app value for it. So it's set
# globally to 0 (apps aren't throttled in power-save mode), and read back.
FRAME_VRCMD_SET = "; ".join([
    "[ -n \"$C\" ] && { echo \"set steamvr.powersaveFramesToThrottle=0: "
    "$(timeout 10 \"$C\" --set-settings-int steamvr.powersaveFramesToThrottle=0 2>&1 | tail -1)\"; "
    "case \"$(timeout 10 \"$C\" --settings-int steamvr.powersaveFramesToThrottle 2>&1 | tail -1)\" in *=0) ;; *) "
    "echo \"set steamvr.powersaveFramesToThrottle 0: "
    "$(timeout 10 \"$C\" --set-settings-int steamvr.powersaveFramesToThrottle 0 2>&1 | tail -1)\";; esac; }",
])


def frame_steamvr_throttle(adb, serial, set_it):
    """SteamVR's throttle settings for Echo (read, and with set_it, Echo's own set to 0).
    Returns what vrcmd answered, for the log."""
    script = "; ".join([FRAME_VRCMD] + ([FRAME_VRCMD_SET] if set_it else []) + [FRAME_VRCMD_READ])
    code, out = shell(adb, serial, script, timeout=90)
    return out.strip()


def frame_mic_volume(adb, serial, percent):
    """SteamOS's default microphone input volume (PipeWire, through pactl), as the user steamos."""
    code, out = shell(adb, serial, f"export XDG_RUNTIME_DIR=/run/user/$(id -u); "
                                   f"pactl set-source-volume @DEFAULT_SOURCE@ {int(percent)}% && "
                                   f"pactl get-source-volume @DEFAULT_SOURCE@", timeout=20)
    if code != 0:
        raise IOError(f"couldn't set the microphone level: {out.strip()}")


def frame_launch_env(adb, serial, fr, env):
    """Environment variables for Echo's Lepton launch on a Steam Frame, in a marked block of
    Frame Control's launch.sh (Lepton passes vrclient's own variables, such as
    DisableTimelineSemaphoreWait, on to the app). An empty env removes the block."""
    lines = ["# >>> EchoQuestXR settings (written by the EchoQuestXR patcher)"]
    lines += [f"export {k}={shlex.quote(v)}" for k, v in env.items()]
    lines += ["# <<< EchoQuestXR settings"]
    block = "\\n".join(lines) if env else ""
    script = (f"F='{fr.app_dir}/launch.sh'; [ -f \"$F\" ] || {{ echo 'no launch.sh'; exit 0; }}; "
              f"awk -v block={shlex.quote(block)} '/^# >>> EchoQuestXR/{{skip=1;next}} /^# <<< EchoQuestXR/{{skip=0;next}} "
              "skip{next} {print} /^set -euo pipefail/ && block!=\"\" {print block}' \"$F\" > \"$F.eqx\" "
              "&& chmod 755 \"$F.eqx\" && mv -f \"$F.eqx\" \"$F\" && grep -c EchoQuestXR \"$F\"")
    code, out = shell(adb, serial, script, timeout=30)
    if code != 0:
        raise IOError(f"couldn't update Echo's launch.sh: {out.strip()}")


def game_data_on_headset(adb, serial):
    """True when the headset has the game data the installer checks for (two folders, 2+ files each)."""
    return Storage(adb, serial).has_game_data()


def install_game_data(adb, serial, log=print, progress=None):
    """Downloads Echo's game data once (kept in <DATA>/cache until it's on a headset) and copies
    over what the headset doesn't have yet, then the asset patches. progress(fraction, text)."""
    say = progress or (lambda f, t: None)
    cache = os.path.join(DATA, "cache")
    os.makedirs(cache, exist_ok=True)
    zpath = os.path.join(cache, "_data.zip")
    store = Storage(adb, serial)
    if store.frame:
        log(f"Steam Frame: Echo's storage is {store.base}")
    if store.has_game_data():
        # Never copy over an existing install: Echo updates its data itself (a headset can
        # hold newer manifests and extra packages than the zip), so the zip could downgrade it
        say(0.96, "Game data is already on the headset. Checking the asset patches...")
        install_asset_patches(store, cache, log, say)
        say(1.0, "Game data is already on the headset; asset patches up to date.")
        return
    store.check_writable()   # before 900 MB of downloading

    if not (os.path.isfile(zpath) and zipfile.is_zipfile(zpath)):
        if shutil.disk_usage(cache).free < 2_200_000_000:
            raise IOError(f"not enough free space in {cache}: the game data needs about 2 GB while copying")
        last = None
        for url in GAME_DATA_URLS:
            try:
                log(f"Downloading game data from {url}")
                fetch(url, zpath, lambda d, t: say(0.5 * d / t if t else 0,
                                                   f"Downloading game data: {d / 1e6:.0f} of {t / 1e6:.0f} MB"))
                last = None
                break
            except Exception as e:
                last = e
                log(f"  failed: {e}")
        if last:
            raise IOError(f"couldn't download the game data: {last}")

    with zipfile.ZipFile(zpath) as z:
        files = [i for i in z.infolist() if not i.is_dir()]
        for i in files:
            if not SAFE_PATH.match(i.filename) or ".." in i.filename:
                raise IOError(f"unexpected file in the game data: {i.filename}")
        there = store.sizes("files/_data")
        total = sum(i.file_size for i in files) or 1
        done = 0
        tmpdir = os.path.join(cache, "push")
        for n, i in enumerate(files, 1):
            rel = f"files/{i.filename}"
            label = f"Copying game data to the headset (file {n} of {len(files)})"
            if there.get(rel) != i.file_size:   # already there with the right size: skip
                say(0.5 + 0.45 * done / total, label + ": unpacking...")
                local = z.extract(i, tmpdir)
                try:
                    store.put(local, rel, lambda b, d=done: say(0.5 + 0.45 * (d + b) / total,
                                                                f"{label}: {(d + b) / 1e6:.0f} of {total / 1e6:.0f} MB"))
                finally:
                    os.remove(local)
            done += i.file_size
            say(0.5 + 0.45 * done / total, label)
        shutil.rmtree(tmpdir, ignore_errors=True)
    if not store.has_game_data():
        raise IOError("the game data was copied, but the headset doesn't show all of it")

    say(0.96, "Applying asset patches...")
    install_asset_patches(store, cache, log, say)
    store.done()
    os.remove(zpath)   # on the headset now: give the PC its 900 MB back
    say(1.0, "Game data installed.")


def install_asset_patches(store, cache, log=print, say=None):
    """The Echo VR installer's mod patches: files listed in its update manifest, sha256-checked,
    relative to Echo's storage folder. say(fraction, text) for each step."""
    say = say or (lambda f, t: None)
    say(0.96, "Asset patches: reading the list...")
    with urllib.request.urlopen(urllib.request.Request(PATCH_MANIFEST_URL, headers=UA), timeout=30) as r:
        text = r.read().decode("utf-8", "replace")
    base = re.search(r"^#\s*Base URL:\s*(\S+)", text, re.M)
    target = re.search(r"^#\s*Target:\s*(\S+)", text, re.M)
    if not base or not target or target.group(1).rstrip("/") != MEDIA:
        raise IOError("the asset patch list isn't in the expected format")
    base = base.group(1).rstrip("/")
    lines = [l.split() for l in text.splitlines() if len(l.split()) >= 2 and not l.startswith("#")]
    for n, parts in enumerate(lines, 1):
        op, path = parts[0].lower(), parts[1]
        step = f"Asset patch {n} of {len(lines)}"
        if not SAFE_PATH.match(path) or ".." in path:
            raise IOError(f"unexpected path in the asset patch list: {path}")
        if op == "del":
            say(0.96, f"{step}: removing")
            store.remove(path)
        elif op == "add" and len(parts) >= 3:
            want = parts[2].lower()
            say(0.96, f"{step}: checking")
            if store.sha256(path) == want:
                continue
            local = os.path.join(cache, "patch-" + want)
            fetch(f"{base}/{path}", local, lambda d, t: say(0.96, f"{step}: downloading {d / 1e6:.1f} of {t / 1e6:.1f} MB"))
            with open(local, "rb") as f:
                ok = hashlib.sha256(f.read()).hexdigest() == want
            try:
                if not ok:
                    raise IOError(f"asset patch {path} didn't match its checksum")
                say(0.97, f"{step}: copying to the headset")
                store.put(local, path, lambda b: say(0.97, f"{step}: copying to the headset ({b / 1e6:.1f} MB)"))
            finally:
                os.remove(local)
            log(f"  patched {path}")
