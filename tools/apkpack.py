"""Shared helpers: find the Android SDK tools, and repack + sign an Echo VR APK.

Used by logger/build.py and runtime/build.py.
"""
import os
import subprocess
import sys
import zipfile

SDK = os.environ.get("ANDROID_HOME", r"J:\AndroidSDK")
API = 26   # libr15.so is built for API 26 (its .note.android.ident)
LIB = "lib/arm64-v8a/"


def newest(path):
    return os.path.join(path, sorted(os.listdir(path), key=lambda v: [int(p) for p in v.split(".") if p.isdigit()])[-1])


def ndk_bin():
    return os.path.join(newest(os.path.join(SDK, "ndk")), "toolchains", "llvm", "prebuilt", "windows-x86_64", "bin")


def run(cmd, **kw):
    print(">", " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
    subprocess.check_call(cmd, **kw)


def exported_functions(lib):
    syms = subprocess.check_output([os.path.join(ndk_bin(), "llvm-readelf.exe"), "--dyn-syms", "-W", lib], text=True)
    return sorted({l.split()[7] for l in syms.splitlines()
                   if len(l.split()) >= 8 and l.split()[3] == "FUNC" and l.split()[6] != "UND"})


def check_exports(lib, exports_txt):
    got = exported_functions(lib)
    want = sorted(l.strip() for l in open(exports_txt) if l.strip())
    if got != want:
        sys.exit(f"export mismatch in {lib}: missing {sorted(set(want) - set(got))[:8]}, extra {sorted(set(got) - set(want))[:8]}")
    return len(got)


def repack(src, out, files, ks, ks_pass="android", ks_type="PKCS12"):
    """Copies src to out with `files` ({entry name: bytes}) replaced or added, then
    zipaligns and signs (v1+v2+v3). Added entries copy the compression of libvrapi.so."""
    tools = newest(os.path.join(SDK, "build-tools"))
    tmp = out + ".unsigned.apk"
    pending = dict(files)
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w") as zout:
        template = zin.getinfo(LIB + "libvrapi.so")
        for info in zin.infolist():
            n = info.filename
            if n.startswith("META-INF/") and n.upper().endswith((".SF", ".RSA", ".DSA", ".EC", "MANIFEST.MF")):
                continue
            zout.writestr(info, pending.pop(n) if n in pending else zin.read(n))
        for n, data in pending.items():
            info = zipfile.ZipInfo(n, template.date_time)
            info.compress_type, info.external_attr = template.compress_type, template.external_attr
            zout.writestr(info, data)
    aligned = out + ".aligned.apk"
    run([os.path.join(tools, "zipalign.exe"), "-f", "-p", "4", tmp, aligned])
    run([os.path.join(tools, "apksigner.bat"), "sign", "--ks", ks, "--ks-type", ks_type,
         "--ks-pass", f"pass:{ks_pass}", "--out", out, aligned])
    subprocess.check_call([os.path.join(tools, "apksigner.bat"), "verify", out])
    for p in (tmp, aligned, out + ".idsig"):
        if os.path.exists(p):
            os.remove(p)
    print(f"wrote {out} ({os.path.getsize(out) // 1024} KB)")
