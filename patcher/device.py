"""Headset side of the patcher: find adb, install the patched APK, copy Echo's game data
over, start Echo, save logs.

Works with any Android headset adb can see (Quest; Steam Frame's Android container where
it exposes adb). Logs are filtered to EchoQuestXR's and the OpenXR runtime's own tags:
Echo's RAD log is left out on purpose, because it prints the community-server login
(including a password) in plain text.

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
import shutil
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


def install(adb, serial, apk):
    """('ok' | 'signature' | 'error', message). 'signature': a copy signed with another key
    is installed, so it has to be uninstalled first (the caller asks the player)."""
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
    tool(adb, serial, "am", "force-stop", PACKAGE, timeout=20)
    clear_logs(adb, serial)
    code, out = tool(adb, serial, "am", "start", "-n", f"{PACKAGE}/{ACTIVITY}", timeout=30)
    return code == 0 and "Error" not in out and not not_found(out), out


def save_logs(adb, serial, path):
    """Writes what's in the device log for EchoQuestXR and OpenXR (no RAD lines) to `path`.
    If the headset's shell can't run logcat at all, writes what that shell is instead, so the
    log says why. Returns the number of lines."""
    code, out = tool(adb, serial, "logcat", "-d", "-v", "time", *LOG_TAGS, timeout=60)
    if not_found(out):   # Steam Frame: adb is on SteamOS; Android runs in Echo's Lepton container
        tags = " ".join(f"'{t}'" for t in LOG_TAGS)
        code, got = run(adb, "-s", serial, "shell", f"podman exec {LEPTON_CONTAINER} logcat -d -v time {tags} 2>&1",
                        timeout=60)
        if code == 0 and got.strip() and not not_found(got) and "Error:" not in got:
            out = "(logcat from Echo's Lepton container)\n" + got
    if not_found(out):
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
    lines = [l for l in out.splitlines() if "/RAD" not in l]
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


def remote_sizes(adb, serial, folder):
    """{path: size} of the files under `folder` on the headset."""
    code, out = shell(adb, serial, f"find '{folder}' -type f -exec stat -c '%s %n' {{}} + 2>/dev/null", timeout=120)
    sizes = {}
    for line in out.splitlines():
        size, _, name = line.partition(" ")
        if size.isdigit():
            sizes[name] = int(size)
    return sizes


def push(adb, serial, local, remote, progress=None):
    """adb push one file, reporting progress(bytes on the headset) about once a second."""
    p = subprocess.Popen([adb, "-s", serial, "push", local, remote], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
    while p.poll() is None:
        try:
            p.wait(1.5)
        except subprocess.TimeoutExpired:
            if progress:
                code, out = shell(adb, serial, f"stat -c %s '{remote}' 2>/dev/null", timeout=15)
                if out.strip().isdigit():
                    progress(int(out.strip()))
    out = p.stdout.read().strip()
    if p.returncode != 0:
        raise IOError(f"copying {os.path.basename(local)} failed: {out.splitlines()[-1] if out else p.returncode}")


def game_data_on_headset(adb, serial):
    """True when the headset has the game data the installer checks for (two folders, 2+ files each)."""
    base = f"{MEDIA}/files/_data/5932408047/rad15/android"
    for sub in ("manifests", "packages"):
        code, out = shell(adb, serial, f"ls '{base}/{sub}' 2>/dev/null", timeout=20)
        if len(out.split()) < 2:
            return False
    return True


def install_game_data(adb, serial, log=print, progress=None):
    """Downloads Echo's game data once (kept in <DATA>/cache until it's on a headset) and copies
    over what the headset doesn't have yet, then the asset patches. progress(fraction, text)."""
    say = progress or (lambda f, t: None)
    cache = os.path.join(DATA, "cache")
    os.makedirs(cache, exist_ok=True)
    zpath = os.path.join(cache, "_data.zip")
    if game_data_on_headset(adb, serial):
        # Never copy over an existing install: Echo updates its data itself (a headset can
        # hold newer manifests and extra packages than the zip), so the zip could downgrade it
        say(0.96, "Game data is already on the headset. Checking the asset patches...")
        install_asset_patches(adb, serial, cache, log)
        say(1.0, "Game data is already on the headset; asset patches up to date.")
        return

    # Before 900 MB of downloading: can this adb connection write to Echo's folder at all?
    # (Steam Frame's can be a Linux shell that sees Android storage read-only.)
    code, out = shell(adb, serial, f"mkdir -p '{MEDIA}/files/.echoquestxr-copy' && echo writable")
    if "writable" not in out:
        raise IOError("this adb connection can't write to Echo's storage on the headset "
                      f"({out.strip().splitlines()[-1] if out.strip() else 'no answer'}). "
                      "Press Save logs and send the file: it records what the connection can reach.")

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
        there = remote_sizes(adb, serial, f"{MEDIA}/files/_data")
        total = sum(i.file_size for i in files) or 1
        done = 0
        tmpdir = os.path.join(cache, "push")
        for n, i in enumerate(files, 1):
            remote = f"{MEDIA}/files/{i.filename}"
            label = f"Copying game data to the headset (file {n} of {len(files)})"
            if there.get(remote) != i.file_size:   # already there with the right size: skip
                say(0.5 + 0.45 * done / total, label + ": unpacking...")
                local = z.extract(i, tmpdir)
                # into a side folder first, then moved: a cut-off copy never looks installed
                part = f"{MEDIA}/files/.echoquestxr-copy/{os.path.basename(i.filename)}"
                try:
                    push(adb, serial, local, part,
                         lambda b, d=done: say(0.5 + 0.45 * (d + b) / total,
                                               f"{label}: {(d + b) / 1e6:.0f} of {total / 1e6:.0f} MB"))
                finally:
                    os.remove(local)
                code, out = shell(adb, serial, f"mkdir -p '{remote.rsplit('/', 1)[0]}' && mv -f '{part}' '{remote}'")
                if code != 0:
                    raise IOError(f"couldn't move {i.filename} into place on the headset: {out}")
            done += i.file_size
            say(0.5 + 0.45 * done / total, label)
        shutil.rmtree(tmpdir, ignore_errors=True)
        shell(adb, serial, f"rm -rf '{MEDIA}/files/.echoquestxr-copy'")
    if not game_data_on_headset(adb, serial):
        raise IOError("the game data was copied, but the headset doesn't show all of it")

    say(0.96, "Applying asset patches...")
    install_asset_patches(adb, serial, cache, log)
    os.remove(zpath)   # on the headset now: give the PC its 900 MB back
    say(1.0, "Game data installed.")


def install_asset_patches(adb, serial, cache, log=print):
    """The Echo VR installer's mod patches: files listed in its update manifest, sha256-checked."""
    with urllib.request.urlopen(urllib.request.Request(PATCH_MANIFEST_URL, headers=UA), timeout=30) as r:
        text = r.read().decode("utf-8", "replace")
    base = re.search(r"^#\s*Base URL:\s*(\S+)", text, re.M)
    target = re.search(r"^#\s*Target:\s*(\S+)", text, re.M)
    if not base or not target or target.group(1).rstrip("/") != MEDIA:
        raise IOError("the asset patch list isn't in the expected format")
    base = base.group(1).rstrip("/")
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or line.startswith("#"):
            continue
        op, path = parts[0].lower(), parts[1]
        if not SAFE_PATH.match(path) or ".." in path:
            raise IOError(f"unexpected path in the asset patch list: {path}")
        remote = f"{MEDIA}/{path}"
        if op == "del":
            shell(adb, serial, f"rm -f '{remote}'")
        elif op == "add" and len(parts) >= 3:
            want = parts[2].lower()
            code, out = shell(adb, serial, f"sha256sum '{remote}' 2>/dev/null")
            if out.split()[:1] == [want]:
                continue
            local = os.path.join(cache, "patch-" + want)
            fetch(f"{base}/{path}", local)
            with open(local, "rb") as f:
                ok = hashlib.sha256(f.read()).hexdigest() == want
            try:
                if not ok:
                    raise IOError(f"asset patch {path} didn't match its checksum")
                push(adb, serial, local, remote)
            finally:
                os.remove(local)
            log(f"  patched {path}")
