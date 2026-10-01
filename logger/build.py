"""Builds the VrApi logger and an Echo VR Quest APK that uses it.

    python build.py --apk <echo.apk> --ks <keystore> [--ks-pass android] [--ks-type PKCS12] [--out <out.apk>]
    python build.py --lib-only

1. gen_stubs.py -> stubs.S, names.h
2. NDK clang -> build/libvrapi.so (SONAME libvrapi.so, exports exactly exports.txt)
3. APK: lib/arm64-v8a/libvrapi.so becomes the logger, Meta's original moves to
   lib/arm64-v8a/libvrapi_real.so, the old signature is dropped, then zipalign and
   apksigner (v1+v2+v3) with your key. Use the key the installed copy was signed
   with, or Android refuses the update.

Tools come from ANDROID_HOME (default J:\\AndroidSDK): the newest NDK and build-tools.
"""
import argparse
import os
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import apkpack  # noqa: E402


def build_lib():
    subprocess.check_call([sys.executable, os.path.join(HERE, "gen_stubs.py")])
    bin_ = apkpack.ndk_bin()
    out = os.path.join(HERE, "build")
    os.makedirs(out, exist_ok=True)
    lib = os.path.join(out, "libvrapi.so")
    apkpack.run([os.path.join(bin_, "clang.exe"), f"--target=aarch64-linux-android{apkpack.API}", "-shared", "-fPIC", "-O2",
                 "-Wall", "-Wextra", "-Wno-unused-parameter", "-fvisibility=hidden",
                 "-Wl,-soname,libvrapi.so", "-Wl,--no-undefined", "-Wl,-z,max-page-size=16384",
                 os.path.join(HERE, "vrapi_logger.c"), os.path.join(HERE, "stubs.S"),
                 "-llog", "-ldl", "-o", lib])
    apkpack.run([os.path.join(bin_, "llvm-strip.exe"), "--strip-unneeded", lib])
    n = apkpack.check_exports(lib, os.path.join(HERE, "exports.txt"))
    print(f"built {lib} ({os.path.getsize(lib)} bytes, {n} exports match libvrapi.so)")
    return lib


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apk")
    ap.add_argument("--out")
    ap.add_argument("--ks", help="keystore the installed APK was signed with")
    ap.add_argument("--ks-pass", default="android")
    ap.add_argument("--ks-type", default="PKCS12", help="PKCS12 or JKS")
    ap.add_argument("--lib-only", action="store_true")
    a = ap.parse_args()
    lib = build_lib()
    if a.lib_only:
        return
    if not a.apk or not a.ks:
        sys.exit("--apk and --ks are needed to build the APK (or use --lib-only)")
    with zipfile.ZipFile(a.apk) as z:
        if apkpack.LIB + "libvrapi_real.so" in z.namelist():
            sys.exit("this APK already has the logger (libvrapi_real.so) -- use the original APK")
        real = z.read(apkpack.LIB + "libvrapi.so")
    out = a.out or os.path.join(HERE, "build", os.path.splitext(os.path.basename(a.apk))[0] + "_vrapilog.apk")
    apkpack.repack(a.apk, out, {apkpack.LIB + "libvrapi.so": open(lib, "rb").read(),
                                apkpack.LIB + "libvrapi_real.so": real}, a.ks, a.ks_pass, a.ks_type)


if __name__ == "__main__":
    main()
