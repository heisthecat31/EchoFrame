# EchoQuestXR

Running the **Echo VR Quest APK on OpenXR**: on Quest through Meta's OpenXR runtime, and
aimed at standalone Steam Frame (through Valve's Lepton Android container) and other
OpenXR headsets.

Echo's Quest build talks to the headset through Meta's **VrApi** (`libvrapi.so`, loader
1.1.40), which only works on Meta's own runtime. EchoQuestXR replaces that one library
with its own `libvrapi.so` that implements the same functions on OpenXR. The game engine
(`libr15.so`) calls only 35 of VrApi's 114 functions, rendering is Vulkan, and nothing
else in the APK changes.

This repository ships **no game files, no APKs and no logs**. You supply your own Echo VR APK.

## Status

| Step | State |
| --- | --- |
| 1. VrApi logger: record every call Echo makes on a Quest 3 | ✅ [docs/vrapi-calls.md](docs/vrapi-calls.md) |
| 2. Button layout (press each button once) | ✅ same document |
| 3. `libvrapi.so` on OpenXR, on Quest 3 | ✅ plays: view, head tracking, hands, every button, throwing |
| 4. Package for Lepton and test on Steam Frame | next |

Tested on a Quest 3 (Horizon OS, Meta OpenXR runtime 85) with Echo VR build 4987566.

## Patching your APK (`patcher/`)

A small window: choose your Echo VR APK, and it writes a patched copy next to it.

```bash
pip install -r patcher/requirements.txt
pythonw patcher/gui.pyw            # or: python patcher/patch.py <echo.apk>
```

It makes three changes: `lib/arm64-v8a/libvrapi.so` becomes the EchoQuestXR runtime,
`lib/arm64-v8a/libopenxr_loader.so` (the Khronos OpenXR loader) is added, and the manifest
gets a `LAUNCHER` entry plus the OpenXR permissions (Lepton on Steam Frame needs the first;
both are harmless on Quest). The result is
signed with a **new random key every time** (saved next to it as `.signing-key.pem`; keep
it private), so no two people share a signing key.

Step 3 in the window does the rest, over USB or (Steam Frame) **Connect...** with the adb
address Frame Control shows:
- **Install**: the patched APK (if Echo VR on the headset was signed with another key, it asks
  before uninstalling it; that removes Echo's app data on the headset), then, ticked by
  default, **Echo's game data**: `_data.zip` from the same mirrors as the
  [Echo VR installer app](https://github.com/heisthecat31/EchoVR-Installer) (about 900 MB,
  downloaded once, resumable), copied to `/sdcard/Android/media/com.readyatdawn.r15/files/`
  where Echo reads it, plus the installer's asset patches (sha256-checked). A headset that
  already has game data is left as it is (Echo updates it itself; the zip could be older),
  and `config.json` (the community-server login) is never touched.
- **Launch**, and **Save logs** (everything Echo's process logged, its own log included, plus
  the crash log and the OpenXR runtime's lines). If the headset's shell
  can't run `logcat`, the saved file says what that shell is instead.

adb (Google's platform-tools) installs itself when the window opens, into
`%LOCALAPPDATA%\EchoQuestXR` for the `.exe`. By hand:

```bash
adb install <your-echo>_openxr.apk
```

The patcher needs the two runtime libraries: in `patcher/runtime/` (a release puts them
there), or built from source into `build/` (below).

## Windows release

```bash
python tools/make_release.py        # build/release/EchoQuestXR-Patcher-v<VERSION>.exe and .zip
```

A standalone `.exe` (PyInstaller): the patcher window with the runtime libraries inside, so
users need nothing else. The zip adds README.txt and THIRD_PARTY_NOTICES.txt. Needs
everything the runtime build needs, plus `cryptography`, Pillow and PyInstaller. The version
is in `VERSION`.

## Building the runtime (`runtime/`)

Needs the Android NDK (default `J:\AndroidSDK`, or set `ANDROID_HOME`), CMake, Ninja and the
OpenXR SDK source (`OPENXR_SDK`, default: EchoXR's `xr/OpenXR-SDK` checkout).

```bash
python runtime/build.py --lib-only                              # build/libvrapi.so, build/libopenxr_loader.so
python runtime/build.py --apk <echo.apk> --ks <keystore>        # also a test APK signed with your key
```

`runtime/vrapi_openxr.cpp` maps VrApi onto OpenXR; `runtime/vrapi_layouts.h` holds the
VrApi structures as recorded from the real thing, with every offset checked at compile
time. Things worth knowing:

- **Foveation maps.** Echo always renders with a fragment density map for each swapchain
  image, so the runtime makes real ones (full density) and enables `VK_EXT_fragment_density_map`.
  Without them the GPU silently drops Echo's rendering.
- **Image orientation.** VrApi's images are bottom-up; the runtime flips them by swapping
  each eye's up and down field-of-view angles.
- **Controllers** use the OpenXR aim pose, which matches VrApi's.
- **Swapchain images.** Echo picks its image round-robin itself; the runtime keeps OpenXR's
  acquire order in step with it.

Properties (read when Echo starts; `adb shell setprop <name> <value>`):

| Property | Effect |
| --- | --- |
| `debug.echoquestxr.pose grip` | controllers from the grip pose instead of aim |
| `debug.echoquestxr.flip 0` | don't flip the images |
| `debug.echoquestxr.test red` / `probe` / `redprobe` | display-path diagnostics |

Logs: `adb logcat -s EchoQuestXR`.

## The logger (`logger/`)

A stand-in `libvrapi.so` that logs each VrApi call and forwards it to Meta's original
(renamed `libvrapi_real.so` in the APK). It's how `docs/vrapi-calls.md` was recorded.

```bash
python logger/build.py --apk <your-echo.apk> --ks <keystore>
```

Sign it with the key of the copy installed on the headset so it installs as an update.

## Steam Frame

Ready on the EchoQuestXR side, untested on a Frame:
- Lepton packaging: `LAUNCHER` entry and OpenXR permissions in the manifest (`patcher/axml.py`);
  OpenXR 1.0 only; refresh-rate requests are optional.
- Vulkan: extensions the driver lacks are left out (logged), so device creation can't fail
  on them. Echo needs `VK_EXT_fragment_density_map` to draw; without it, expect a black view.
- Controllers: Steam Frame (`XR_VALVE_frame_controller_interaction`: right A/B as A/B, left
  d-pad down/up as X/Y, View as menu), Valve Index and the generic simple controller, as
  well as Touch.

How it fits Lepton (from [Frame Control](https://github.com/saphid/frame-control)'s
`docs/apks.md` and what the Frame reported):
- Frame Control sets Echo up as its own Lepton instance: `~/Applications/Android/com.readyatdawn.r15/`
  holds `app.apk`, `launch.sh`, `instance.id` and `shortcut.id`, plus a Steam shortcut.
  `launch.sh` needs **Lepton Development** (Steam app 3056000) installed in the main Steam library.
- Echo runs in podman container `lepton-steamlaunch-<instance.id>`. Only `~/.local/share/Steam`
  is mounted in it. Lepton's `/sdcard` is rebuilt with its Android snapshot, but Echo's private
  folder `/data/data/com.readyatdawn.r15` is kept, as
  `~/.local/share/Steam/steamapps/compatdata/<instance.id>/internal/com.readyatdawn.r15`.
- The Frame's adb (Frame Control's address) is a SteamOS shell, not Android's.

So the Frame build (**For Steam Frame** in the window, `patch.py --frame`) changes every
`/sdcard/Android/media/com.readyatdawn.r15` in `libr15.so` and `libassetpatch.so` to
`/data/data/com.readyatdawn.r15`, in place (the new path is shorter). It also changes two
instructions in Echo's `CGS::Initialize` for the Frame's Mesa driver
(`FRAME_CODE_PATCHES` in `patcher/patch.py`):
- Echo asks for Vulkan apiVersion 1 (that is 0.0.1), which Quest's driver accepts but Mesa
  doesn't (no core functions, then `VK_ERROR_INCOMPATIBLE_DRIVER`); the Frame build asks for 1.0.
- Echo reads only the first 128 device extensions and leaves out any VrApi extension it
  didn't see ("Required extension ... does not exist"). Mesa lists far more, so the ones
  SteamVR needs (Android hardware buffers, foreign queue family) were left out and Echo
  crashed. The Frame build enables every extension the runtime asks for; the runtime only
  asks for ones the driver has. Over the Frame's adb the
window then: **Install** replaces `app.apk` and copies the game data into that private folder
(through `podman unshare`, owned by Echo's user); **Launch** starts the Steam shortcut;
**Save logs** runs `logcat` inside Echo's container. Echo has to have started once so its
private folder exists.

1. Install Lepton Development, and send the Frame build's APK with Frame Control once.
2. Start Echo once from the Steam library (it closes without data).
3. In the window: Connect... (Frame Control's address), Install, Launch, Save logs. The
   log's EchoQuestXR lines list the OpenXR extensions, the Vulkan extensions left out, and
   any swapchain-format problem.

## Not done yet

- Hand-tracking devices aren't offered to Echo (it only reads the controllers).
- Haptics follow VrApi's buffer format as best understood; untested in detail.
- Some community builds of Echo print the EchoVRCE login (with its password) in Echo's own
  log, which Save logs keeps: check a log before sharing it if yours does.
