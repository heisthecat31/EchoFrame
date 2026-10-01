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

It makes exactly two changes: `lib/arm64-v8a/libvrapi.so` becomes the EchoQuestXR runtime,
and `lib/arm64-v8a/libopenxr_loader.so` (the Khronos OpenXR loader) is added. The result is
signed with a **new random key every time** (saved next to it as `.signing-key.pem`; keep
it private), so no two people share a signing key.

Android only updates an app signed with the same key, so **uninstall Echo VR first**, then:

```bash
adb install <your-echo>_openxr.apk
```

The patcher needs the two runtime libraries: in `patcher/runtime/` (a release puts them
there), or built from source into `build/` (below).

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

## Not done yet

- Steam Frame: Lepton packaging (launcher entry, OpenXR 1.0, fixed refresh rate).
- Hand-tracking devices aren't offered to Echo (it only reads the controllers).
- Haptics follow VrApi's buffer format as best understood; untested in detail.
- Echo's own log prints the EchoVRCE login URL with its password; that's the game, not
  EchoQuestXR, but anyone with adb access to the headset can read it.
