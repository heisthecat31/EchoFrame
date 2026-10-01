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
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SDK = os.environ.get("ANDROID_HOME", r"J:\AndroidSDK")
API = 26   # libr15.so is built for API 26 (its .note.android.ident)


def newest(path):
    return os.path.join(path, sorted(os.listdir(path), key=lambda v: [int(p) for p in v.split(".") if p.isdigit()])[-1])


def run(cmd):
    print(">", " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
    subprocess.check_call(cmd)


def build_lib():
    subprocess.check_call([sys.executable, os.path.join(HERE, "gen_stubs.py")])
    ndk = newest(os.path.join(SDK, "ndk"))
    bin_ = os.path.join(ndk, "toolchains", "llvm", "prebuilt", "windows-x86_64", "bin")
    out = os.path.join(HERE, "build")
    os.makedirs(out, exist_ok=True)
    lib = os.path.join(out, "libvrapi.so")
    run([os.path.join(bin_, "clang.exe"), f"--target=aarch64-linux-android{API}", "-shared", "-fPIC", "-O2",
         "-Wall", "-Wextra", "-Wno-unused-parameter", "-fvisibility=hidden",
         "-Wl,-soname,libvrapi.so", "-Wl,--no-undefined", "-Wl,-z,max-page-size=16384",
         os.path.join(HERE, "vrapi_logger.c"), os.path.join(HERE, "stubs.S"),
         "-llog", "-ldl", "-o", lib])
    run([os.path.join(bin_, "llvm-strip.exe"), "--strip-unneeded", lib])
    # the exports must be exactly Meta's
    syms = subprocess.check_output([os.path.join(bin_, "llvm-readelf.exe"), "--dyn-syms", "-W", lib], text=True)
    got = sorted({l.split()[7] for l in syms.splitlines()
                  if len(l.split()) >= 8 and l.split()[3] == "FUNC" and l.split()[6] != "UND"})
    want = sorted(l.strip() for l in open(os.path.join(HERE, "exports.txt")) if l.strip())
    if got != want:
        sys.exit(f"export mismatch: missing {sorted(set(want) - set(got))[:5]}, extra {sorted(set(got) - set(want))[:5]}")
    print(f"built {lib} ({os.path.getsize(lib)} bytes, {len(got)} exports match libvrapi.so)")
    return lib


def build_apk(src, lib, out, ks, ks_pass, ks_type):
    tools = newest(os.path.join(SDK, "build-tools"))
    tmp = out + ".unsigned.apk"
    logger = open(lib, "rb").read()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w") as zout:
        names = zin.namelist()
        if "lib/arm64-v8a/libvrapi_real.so" in names:
            sys.exit("this APK already has the logger (libvrapi_real.so) -- use the original APK")
        for info in zin.infolist():
            n = info.filename
            if n.startswith("META-INF/") and n.upper().endswith((".SF", ".RSA", ".DSA", ".EC", "MANIFEST.MF")):
                continue
            data = zin.read(n)
            if n == "lib/arm64-v8a/libvrapi.so":
                real = zipfile.ZipInfo("lib/arm64-v8a/libvrapi_real.so", info.date_time)
                real.compress_type, real.external_attr = info.compress_type, info.external_attr
                zout.writestr(real, data)
                data = logger
            zout.writestr(info, data)
    aligned = out + ".aligned.apk"
    run([os.path.join(tools, "zipalign.exe"), "-f", "-p", "4", tmp, aligned])
    run([os.path.join(tools, "apksigner.bat"), "sign", "--ks", ks, "--ks-type", ks_type,
         "--ks-pass", f"pass:{ks_pass}", "--out", out, aligned])
    run([os.path.join(tools, "apksigner.bat"), "verify", "--print-certs", out])
    for p in (tmp, aligned, out + ".idsig"):
        if os.path.exists(p):
            os.remove(p)
    print(f"wrote {out} ({os.path.getsize(out) // 1024} KB)")


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
    out = a.out or os.path.join(HERE, "build", os.path.splitext(os.path.basename(a.apk))[0] + "_vrapilog.apk")
    build_apk(a.apk, lib, out, a.ks, a.ks_pass, a.ks_type)


if __name__ == "__main__":
    main()
