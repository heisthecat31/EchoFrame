# EchoQuestXR

Running the **Echo VR Quest APK on OpenXR**, aimed at standalone Steam Frame (through
Valve's Lepton Android container) and any other OpenXR headset.

Echo's Quest build talks to the headset through Meta's **VrApi** (`libvrapi.so`, loader
1.1.40), which only works on Meta's own runtime. The plan is to replace that one library
with our own `libvrapi.so` that implements the same functions on OpenXR. The game engine
(`libr15.so`) calls only **35** of VrApi's 114 functions, rendering is Vulkan, and nothing
else in the APK changes.

This repository ships **no game or Meta files**. You supply your own Echo VR APK.

## Status

| Step | State |
| --- | --- |
| 1. VrApi logger: record every call Echo makes on a Quest 3 | ✅ done, see [docs/vrapi-calls.md](docs/vrapi-calls.md) |
| 2. Log the button layout (press each button once) | ✅ done, same document |
| 3. `libvrapi.so` on OpenXR, tested on Quest 3's own OpenXR runtime | not started |
| 4. Package for Lepton and test on Steam Frame | not started |

## The logger (`logger/`)

A stand-in `libvrapi.so` that logs each VrApi call and forwards it to Meta's original,
which is renamed `libvrapi_real.so` inside the APK. Every export is a small assembly stub,
so calls pass through untouched without VrApi's headers. The first calls of each function
are logged with their arguments and results, setup calls are decoded, and a summary of
call counts is printed every 5 seconds.

Needs: Android NDK and build-tools (default `J:\AndroidSDK`, or set `ANDROID_HOME`),
Python 3, Java.

```bash
python logger/build.py --apk <your-echo.apk> --ks <keystore> [--ks-pass android] [--ks-type PKCS12]
adb install -r logger/build/<your-echo>_vrapilog.apk
adb logcat -s EchoQuestXR
```

Sign with **the same key as the copy installed on the headset**, so it installs as an
update and the game data in `/sdcard/Android/media/com.readyatdawn.r15` stays. Pull the
installed APK first (`adb shell pm path com.readyatdawn.r15`) and keep it: reinstalling it
undoes the logger.
