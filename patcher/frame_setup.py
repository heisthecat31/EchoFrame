"""Sets Echo VR up on a Steam Frame, the way Frame Control (github.com/saphid/frame-control, MIT,
Copyright (c) 2026 saphid) sets up an Android app, so the patcher needs nothing else:

- Lepton (Steam app 3056000, "Lepton Development") must be installed: it's queued through
  steam://install and Steam's own "continue" if it isn't, then waited for.
- ~/Applications/Android/<package>/ gets app.apk, launch.sh (Frame Control's
  frame/android/lepton-app.sh), instance.id (Frame Control's number for the package, so an Echo
  it set up keeps its data) and meta.json.
- A non-Steam shortcut to launch.sh, marked VR, added through the Steam client's DevTools
  (127.0.0.1:8080, SharedJSContext; the Frame's Steam runs with -cef-enable-debugging), with its
  id in shortcut.id.
- Echo is started once, so Lepton makes Echo's data folder (where the game data goes), then
  stopped.

Everything runs through the Frame's adb, which is a SteamOS shell as the user.
"""
import json
import os
import tempfile
import time
import zlib

import device

LEPTON_APP = 3056000
LEPTON_BIN = "$HOME/.local/share/Steam/steamapps/common/Lepton/lepton"
NAME = "Echo VR"

# Frame Control's frame/android/lepton-app.sh, unchanged (MIT, Copyright (c) 2026 saphid).
LAUNCH_SH = r'''#!/bin/bash
# Frame-side: run one Android app in its own persistent Lepton instance.
# Copied into ~/Applications/Android/<package>/launch.sh by the Mac-side
# installer, next to app.apk, instance.id and (for 2D apps) the empty
# lepton-show-flatscreen marker. A non-Steam shortcut points at this file.
#
# Why not Lepton Development: it wipes every app it installed when it exits.
# A "steamlaunch" context (SteamAppId set) keeps app data in
# compatdata/<id>/internal across restarts and APK updates. Pattern from
# frame/t3code/launch.sh; see docs/apks.md.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
LEPTON="$HOME/.local/share/Steam/steamapps/common/Lepton/lepton"
[[ -x "$LEPTON" ]] || { echo "Lepton isn't installed (Steam app 3056000)" >&2; exit 1; }
for need in "$DIR/app.apk" "$DIR/instance.id"; do
  [[ -f "$need" ]] || { echo "launch.sh: missing $need" >&2; exit 1; }
done

# A number that isn't a real Steam app; it names this app's Lepton context.
export SteamAppId="$(cat "$DIR/instance.id")"
[[ "$SteamAppId" =~ ^[0-9]+$ ]] || { echo "invalid instance.id" >&2; exit 1; }
# Keep the stable Lepton context, but identify the Android VR client as its
# actual Steam shortcut. Lepton applies LEPTON_ENV_* after its own passthrough.
if [[ -f "$DIR/shortcut.id" ]]; then
  shortcut="$(cat "$DIR/shortcut.id")"
  [[ "$shortcut" =~ ^[0-9]+$ ]] || { echo "invalid shortcut.id" >&2; exit 1; }
  export LEPTON_ENV_SteamAppId="$shortcut"
fi
exec 9>"$DIR/launch.lock"
flock -n 9 || { echo "Android app is already running" >&2; exit 1; }
CONTAINER="lepton-steamlaunch-$SteamAppId"
# Holding the lock means no launcher owns a running container: it was orphaned
# (this script SIGKILLed), so stop it rather than refuse every later Play. The
# name is this app's alone. A Lepton host process whose launcher was killed
# before it made the container may linger briefly; nothing else is killed.
if [[ "$(podman inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)" == true ]]; then
  echo "Stopping orphaned $CONTAINER" >&2
  podman stop -t 5 "$CONTAINER" >/dev/null 2>&1 || true
fi
export STEAM_COMPAT_INSTALL_PATH="$DIR"
# Must be under ~/.local/share/Steam: only that tree is mounted in the container.
export STEAM_COMPAT_DATA_PATH="$HOME/.local/share/Steam/steamapps/compatdata/$SteamAppId"
export STEAM_COMPAT_SHADER_PATH="$HOME/.local/share/Steam/steamapps/shadercache/$SteamAppId"
export STEAM_FOSSILIZE_DUMP_PATH="$STEAM_COMPAT_SHADER_PATH/fozpipelinesv6/steamapp_pipeline_cache"
mkdir -p "$STEAM_COMPAT_DATA_PATH" "$STEAM_FOSSILIZE_DUMP_PATH"

# Lepton's setpgid --foreground re-exec needs a terminal that Steam shortcuts
# and SSH don't have; give it its own session instead.
export IS_PARENT=true
# Keep this shell in Steam's process tree; setsid alone has no container cleanup.
child=""
cleanup() {
  trap '' TERM INT HUP
  if [[ -n "$child" ]]; then
    kill -TERM -- "-$child" 2>/dev/null || true
    kill -TERM "$child" 2>/dev/null || true
  fi
  podman stop -t 5 "$CONTAINER" >/dev/null 2>&1 || true
  if [[ -n "$child" ]]; then
    kill -KILL -- "-$child" 2>/dev/null || true
    kill -KILL "$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
trap 'exit 129' HUP
# 9>&-: the lock is this launcher's alone; Lepton's tree mustn't keep it held.
setsid --wait "$LEPTON" waitforexitandrun -- "$DIR/app.apk" 9>&- &
child=$!
rc=0
wait "$child" || rc=$?
child=""
exit "$rc"
'''

# Frame-side (python3, stdlib only): the Steam client's DevTools, after Frame Control's
# frame/android/steam_shortcuts.py (MIT, Copyright (c) 2026 saphid).
STEAM_PY = r'''
import base64, json, os, socket, struct, sys, urllib.request

def target_ws():
    for t in json.load(urllib.request.urlopen("http://127.0.0.1:8080/json", timeout=5)):
        if t.get("title") == "SharedJSContext":
            return t["webSocketDebuggerUrl"]
    sys.exit("SharedJSContext not found: is the Steam client running?")

class WS:
    def __init__(self, url, timeout=20):
        host_port, path = url[len("ws://"):].split("/", 1)
        host, port = host_port.split(":")
        self.s = socket.create_connection((host, int(port)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET /{path} HTTP/1.1\r\nHost: {host_port}\r\nUpgrade: websocket\r\n"
                        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                        "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.s.recv(4096)
        if b" 101 " not in buf.split(b"\r\n", 1)[0]:
            sys.exit("websocket handshake failed")
        self.rest = buf.split(b"\r\n\r\n", 1)[1]

    def _read(self, n):
        while len(self.rest) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise EOFError
            self.rest += chunk
        out, self.rest = self.rest[:n], self.rest[n:]
        return out

    def send(self, text):
        data = text.encode()
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else
                                bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else
                                bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        msg = b""
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7f
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            msg += self._read(n)
            if b0 & 0x80:
                return msg.decode()

def evaluate(js, timeout=20):
    ws = WS(target_ws(), timeout)
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
        "expression": js, "awaitPromise": True, "returnByValue": True}}))
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == 1:
            break
    res = r.get("result", {})
    if "exceptionDetails" in res:
        sys.exit("JS error: " + json.dumps(res["exceptionDetails"])[:500])
    return res.get("result", {}).get("value")

cmd, args = sys.argv[1], sys.argv[2:]
if cmd == "add":   # NAME EXE START_DIR -> the shortcut's app id
    name, exe, start = (json.dumps(a) for a in args[:3])
    print(evaluate(f"""(async () => {{
      const id = await SteamClient.Apps.AddShortcut({name}, {exe}, "", "");
      SteamClient.Apps.SetShortcutName(id, {name});
      SteamClient.Apps.SetShortcutStartDir(id, {start});
      if (typeof SteamClient.Apps.SetShortcutIsVR === "function") SteamClient.Apps.SetShortcutIsVR(id, true);
      return id;
    }})()"""))
elif cmd == "has":   # APPID -> yes / no
    print("yes" if evaluate(f"!!appStore.GetAppOverviewByAppID({int(args[0])})") else "no")
elif cmd == "continue-install":   # accepts Steam's install dialog (state 7), if it's showing
    print(evaluate("(() => { try { SteamClient.Installs.ContinueInstall(); return 'ok'; } "
                   "catch (e) { return String(e); } })()"))
'''


def instance_id(package=device.PACKAGE):
    """Frame Control's Lepton context for a package (so an Echo it set up keeps its data)."""
    return 2800000000 + zlib.crc32(package.encode()) % 70000000


def _push_text(adb, serial, text, remote, mode=None):
    with tempfile.NamedTemporaryFile("w", delete=False, newline="\n", encoding="utf-8") as f:
        f.write(text)
        local = f.name
    try:
        code, out = device.run(adb, "-s", serial, "push", local, remote, timeout=60)
    finally:
        os.remove(local)
    if code != 0:
        raise IOError(f"couldn't copy {os.path.basename(remote)} to the Frame: {out.strip()[-200:]}")
    if mode:
        device.shell(adb, serial, f"chmod {mode} '{remote}'")


def _steam(adb, serial, home, *args, timeout=60):
    """Runs STEAM_PY on the Frame; its last output line."""
    script = f"{home}/.cache/echoquestxr-steam.py"
    device.shell(adb, serial, f"mkdir -p '{home}/.cache'")
    _push_text(adb, serial, STEAM_PY, script)
    quoted = " ".join("'" + a.replace("'", "'\\''") + "'" for a in args)
    code, out = device.shell(adb, serial, f"python3 '{script}' {quoted} 2>&1", timeout=timeout)
    lines = out.strip().splitlines()
    return lines[-1].strip() if lines else ""


def lepton_installed(adb, serial):
    code, out = device.shell(adb, serial, f"[ -x {LEPTON_BIN} ] && echo yes")
    return "yes" in out


def install_lepton(adb, serial, home, say, wait=1800):
    """Queues Lepton in Steam, accepts Steam's install dialog, and waits for it."""
    say(None, "Installing Lepton (Valve's Android layer) through Steam on the Frame...")
    device.shell(adb, serial, f"nohup steam steam://install/{LEPTON_APP} >/dev/null 2>&1 &")
    start = time.time()
    accepted = 0
    while time.time() - start < wait:
        if lepton_installed(adb, serial):
            return
        if accepted < 6:   # the dialog can take a few seconds to show
            time.sleep(4)
            _steam(adb, serial, home, "continue-install")
            accepted += 1
            continue
        mins = int(time.time() - start) // 60
        say(None, f"Waiting for Steam to download Lepton on the Frame ({mins} min so far). If the Frame shows an "
                  "install dialog, choose Install.")
        time.sleep(8)
    raise IOError("Lepton didn't finish installing. On the Frame, install \"Lepton Development\" from the Steam "
                  "library (it's free), then press Install again.")


def set_up(adb, serial, apk, say=lambda f, t: None, log=print):
    """Sets Echo up on this Frame (anything missing), installs apk as its app.apk, and makes sure
    Echo's data folder exists. Returns the Frame (device.frame_info) afterwards."""
    fr = device.frame_info(adb, serial)
    if not fr:
        raise IOError("this isn't a Steam Frame")
    home, pkg = fr.home, device.PACKAGE
    d = f"{home}/Applications/Android/{pkg}"
    if not lepton_installed(adb, serial):
        install_lepton(adb, serial, home, say)
    log("Lepton is installed.")

    say(None, "Setting Echo VR up on the Frame...")
    device.shell(adb, serial, f"mkdir -p '{d}'")
    iid = int(fr.instance) if fr.instance else instance_id(pkg)
    _push_text(adb, serial, LAUNCH_SH, f"{d}/launch.sh", mode="755")
    if apk:
        say(None, f"Copying {os.path.basename(apk)} to the Frame...")
        code, out = device.run(adb, "-s", serial, "push", apk, f"{d}/app.apk.part", timeout=900)
        if code != 0:
            raise IOError(f"couldn't copy the APK to the Frame: {out.strip()[-200:]}")
        device.shell(adb, serial, f"mv -f '{d}/app.apk.part' '{d}/app.apk'")
    code, out = device.shell(adb, serial, f"[ -f '{d}/app.apk' ] && echo yes")
    if "yes" not in out:
        raise IOError("make the OpenXR version first (step 3): the Frame has no Echo APK yet")
    device.shell(adb, serial, f"echo {iid} > '{d}/instance.id'; rm -f '{d}/lepton-show-flatscreen'")
    log(f"Lepton instance {iid}; launcher in {d}")

    shortcut = int(fr.shortcut) if fr.shortcut else None
    if shortcut and _steam(adb, serial, home, "has", str(shortcut)) != "yes":
        shortcut = None
    if not shortcut:
        say(None, "Adding Echo VR to the Frame's Steam library...")
        reply = _steam(adb, serial, home, "add", NAME, f"{d}/launch.sh", d)
        if not reply.isdigit():
            raise IOError(f"Steam on the Frame didn't add the shortcut ({reply[:160] or 'no answer'}). Make sure "
                          "the Frame is on its Steam home screen, then press Install again.")
        shortcut = int(reply)
        log(f"Steam shortcut {shortcut} added")
    device.shell(adb, serial, f"echo {shortcut} > '{d}/shortcut.id'")
    meta = {"package": pkg, "label": NAME, "instance": iid, "shortcut": shortcut,
            "game_id": (shortcut << 32) | 0x02000000, "vr": True, "flatscreen": False,
            "installed": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": "EchoQuestXR", "library_version": 2}
    _push_text(adb, serial, json.dumps(meta, indent=1), f"{d}/meta.json")

    fr = device.frame_info(adb, serial)
    code, out = device.shell(adb, serial, f"podman unshare test -d '{fr.data}' && echo yes")
    if "yes" not in out:   # Echo's data folder is made by Lepton on Echo's first start
        say(None, "Starting Echo once so the Frame makes its data folder (keep the headset on or awake)...")
        ok, msg = device.launch(adb, serial)
        if not ok:
            raise IOError(f"couldn't start Echo on the Frame: {msg}")
        start = time.time()
        while time.time() - start < 240:
            time.sleep(5)
            code, out = device.shell(adb, serial, f"podman unshare test -d '{fr.data}' && echo yes")
            if "yes" in out:
                break
        else:
            raise IOError("Echo didn't start on the Frame. Put the headset on, start Echo VR from the Steam library "
                          "once, then press Install again.")
        time.sleep(5)
        device.shell(adb, serial, f"podman stop -t 5 lepton-steamlaunch-{iid} >/dev/null 2>&1", timeout=60)
        log("Echo's data folder is there.")
    return fr
