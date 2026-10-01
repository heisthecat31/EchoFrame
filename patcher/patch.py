"""EchoQuestXR patcher: turns your Echo VR Quest APK into one that runs on OpenXR.

    python patch.py <echo.apk> [-o <out.apk>] [--frame]   (or use gui.pyw)

The changes (changes() below; runtime/build.py makes the same ones):
  lib/arm64-v8a/libvrapi.so         replaced by the EchoQuestXR runtime (VrApi on OpenXR)
  lib/arm64-v8a/libopenxr_loader.so added (the Khronos OpenXR loader)
  AndroidManifest.xml               Echo's activity becomes a LAUNCHER entry (Lepton on
                                    Steam Frame needs it) and the OpenXR permissions are
                                    declared (axml.py)
  libr15.so, libassetpatch.so       Steam Frame build only (--frame): the game data path
                                    becomes Echo's private folder (relocate()), and
                                    Echo asks for Vulkan 1.0 (vulkan_1_0())
Everything else in the APK is copied unchanged.

The result is signed with a NEW random key every time (RSA-2048, self-signed), so no two
people share a signing key. The key is saved next to the APK as <name>.signing-key.pem;
keep it private. Android only updates an app signed with the same key, so uninstall the
existing Echo VR before installing a patched APK.

Signing is JAR (v1) signing in pure Python: Echo targets Android 10 (SDK 29), which
accepts it, and nothing beyond Python and the `cryptography` package is needed.
"""
import argparse
import base64
import datetime
import hashlib
import os
import secrets
import sys
import zipfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import axml  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = "lib/arm64-v8a/"
RUNTIME_FILES = ("libvrapi.so", "libopenxr_loader.so")
# Meta's VrApi loader 1.1.40 as shipped in Echo VR build 4987566
META_VRAPI_SHA256 = "43bec081aae29781389823f690b5f63932905f76cb96452474a65ef8c16fd3e6"
SIGNATURE_FILES = (".SF", ".RSA", ".DSA", ".EC")
# Where community-patched Echo reads its game data, spelled out in these libraries. On Steam
# Frame, Lepton's /sdcard can't be written from SteamOS and is rebuilt with its Android
# snapshot, so the Frame build reads from Echo's private folder, which Lepton keeps (on the
# Frame: ~/.local/share/Steam/steamapps/compatdata/<instance>/internal/<package>).
MEDIA_DIR = "/sdcard/Android/media/com.readyatdawn.r15"
FRAME_DATA_DIR = "/data/data/com.readyatdawn.r15"
DATA_DIR_LIBS = ("libr15.so", "libassetpatch.so")
# Echo's CGS::Initialize sets VkApplicationInfo engineVersion and apiVersion with one
# `movi v0.2s, #0x1`: apiVersion 1 is version 0.0.1. Quest's driver takes it; Mesa (Steam
# Frame) treats it as older than 1.0 and exposes no core functions, so vkCreateInstance
# fails with VK_ERROR_INCOMPATIBLE_DRIVER. `movi v0.2s, #0x40, lsl #16` makes both 0x400000,
# Vulkan 1.0. Matched on that instruction and the three after it, which must occur once.
VK_API_OLD = bytes.fromhex("2004000f" "283300b9" "e8a32e91" "00001e91")
VK_API_NEW = bytes.fromhex("0044020f")


class PatchError(Exception):
    pass


def find_runtime():
    """The runtime libraries: patcher/runtime/ (release layout) or ../build/ (built from source)."""
    for d in (os.path.join(HERE, "runtime"), os.path.join(HERE, "..", "build")):
        if all(os.path.isfile(os.path.join(d, f)) for f in RUNTIME_FILES):
            return os.path.abspath(d)
    raise PatchError("The EchoQuestXR runtime isn't here. Put libvrapi.so and libopenxr_loader.so in "
                     "patcher/runtime/, or build them: python runtime/build.py --lib-only")


def inspect(apk):
    """Checks this is an Echo VR Quest APK. Returns a short description."""
    try:
        z = zipfile.ZipFile(apk)
    except (zipfile.BadZipFile, OSError) as e:
        raise PatchError(f"Not an APK (zip): {e}")
    with z:
        names = set(z.namelist())
        for need in ("AndroidManifest.xml", LIB + "libr15.so", LIB + "libvrapi.so"):
            if need not in names:
                raise PatchError(f"This isn't an Echo VR Quest APK: {need} is missing.")
        if LIB + "libvrapi_real.so" in names:
            raise PatchError("This APK has the EchoQuestXR logger in it. Start from an original Echo VR APK.")
        vrapi = hashlib.sha256(z.read(LIB + "libvrapi.so")).hexdigest()
        if LIB + "libopenxr_loader.so" in names:
            return "already patched for OpenXR: it will be patched again with this runtime"
        if vrapi != META_VRAPI_SHA256:
            return "Echo VR, but with a libvrapi.so this patcher hasn't seen (it was made for build 4987566)"
        return "Echo VR Quest APK with Meta's VrApi 1.1.40 (build 4987566 or compatible)"


# ---------------------------------------------------------------------------
# signing
# ---------------------------------------------------------------------------
def new_key():
    """A fresh RSA-2048 key and a self-signed certificate (30 years) with a random name."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tag = secrets.token_hex(4)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"EchoQuestXR {tag}"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "EchoQuestXR user key")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=365 * 30))
            .sign(key, hashes.SHA256()))
    return key, cert


def _b64_digest(data):
    return base64.b64encode(hashlib.sha384(data).digest()).decode()


def _line(name, value):
    """A manifest line, wrapped at 72 bytes with continuation lines (JAR format)."""
    raw = f"{name}: {value}".encode()
    out = raw[:70] + b"\r\n"
    for i in range(70, len(raw), 69):
        out += b" " + raw[i:i + 69] + b"\r\n"
    return out


def jar_signature(entries, key, cert):
    """MANIFEST.MF, CERT.SF and CERT.RSA for [(name, data)], SHA-384 (as the Quest test builds use)."""
    main = b"Manifest-Version: 1.0\r\n" + _line("Created-By", "EchoQuestXR patcher") + b"\r\n"
    sections = [(n, _line("Name", n) + _line("SHA-384-Digest", _b64_digest(d)) + b"\r\n") for n, d in entries]
    manifest = main + b"".join(s for _, s in sections)
    sf = (b"Signature-Version: 1.0\r\n" + _line("Created-By", "EchoQuestXR patcher") +
          _line("SHA-384-Digest-Manifest", _b64_digest(manifest)) +
          _line("SHA-384-Digest-Manifest-Main-Attributes", _b64_digest(main)) + b"\r\n")
    for n, s in sections:
        sf += _line("Name", n) + _line("SHA-384-Digest", _b64_digest(s)) + b"\r\n"
    rsa_block = (pkcs7.PKCS7SignatureBuilder().set_data(sf).add_signer(cert, key, hashes.SHA384())
                 .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary, pkcs7.PKCS7Options.DetachedSignature,
                                                   pkcs7.PKCS7Options.NoCapabilities]))
    return manifest, sf, rsa_block


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------
def _aligned(zout, info, data, align):
    """Writes a STORED entry with its data aligned (zipalign's padding extra field, 0xD935)."""
    offset = zout.fp.tell()
    header = 30 + len(info.filename.encode()) + 6
    pad = (-(offset + header)) % align
    info.extra = (0xD935).to_bytes(2, "little") + (2 + pad).to_bytes(2, "little") + align.to_bytes(2, "little") + b"\0" * pad
    zout.writestr(info, data)


def relocate(data, new_dir):
    """Every MEDIA_DIR in a library's bytes, pointed at new_dir (shorter, so each string keeps
    its place: what follows the path moves up and the freed bytes become zeros). Returns
    (new bytes, count)."""
    old, new = MEDIA_DIR.encode(), new_dir.encode()
    if len(new) > len(old):
        raise PatchError(f"{new_dir} is longer than {MEDIA_DIR}, so it can't replace it in place")
    data = bytearray(data)
    count, i = 0, data.find(old)
    while i >= 0:
        end = data.index(0, i)   # the end of this string
        rest = bytes(data[i + len(old):end])
        data[i:end] = new + rest + b"\0" * (len(old) - len(new))
        count += 1
        i = data.find(old, i + len(new) + len(rest))
    return bytes(data), count


def vulkan_1_0(data):
    """libr15.so asking for Vulkan 1.0 instead of 0.0.1 (VK_API_OLD above)."""
    i = data.find(VK_API_OLD)
    if i < 0 or data.find(VK_API_OLD, i + 1) >= 0:
        raise PatchError("Couldn't find Echo's Vulkan version setting in libr15.so (made for build 4987566)")
    return data[:i] + VK_API_NEW + data[i + len(VK_API_NEW):]


def changes(apk, runtime, log=print, data_dir=None, vk_1_0=False):
    """{entry name: new bytes}: every change EchoQuestXR makes to an Echo VR APK. data_dir:
    where Echo reads its game data instead of /sdcard/Android/media/<package>; vk_1_0: Echo
    asks for Vulkan 1.0 (VK_API_OLD). The Steam Frame build does both (FRAME_DATA_DIR)."""
    out = {LIB + f: open(os.path.join(runtime, f), "rb").read() for f in RUNTIME_FILES}
    with zipfile.ZipFile(apk) as z:
        out["AndroidManifest.xml"] = axml.patch_manifest(z.read("AndroidManifest.xml"), log)
        if vk_1_0:
            out[LIB + "libr15.so"] = vulkan_1_0(z.read(LIB + "libr15.so"))
            log("  libr15.so: asks for Vulkan 1.0 (was 0.0.1, which Mesa refuses)")
        if data_dir:
            names = set(z.namelist())
            total = 0
            for lib in DATA_DIR_LIBS:
                if LIB + lib in names:
                    out[LIB + lib], n = relocate(out.get(LIB + lib) or z.read(LIB + lib), data_dir)
                    log(f"  {lib}: {n} game data path(s) now {data_dir}")
                    total += n
            if not total:
                raise PatchError(f"No {MEDIA_DIR} paths found to change: is this a community-patched Echo VR APK?")
    return out


def patch(apk, out, log=print, data_dir=None, frame=False):
    """frame: the Steam Frame build (game data in FRAME_DATA_DIR unless data_dir says otherwise,
    Vulkan 1.0)."""
    log(f"Reading {apk}")
    log(f"  {inspect(apk)}")
    runtime = find_runtime()
    log(f"Runtime: {runtime}")
    try:
        replace = changes(apk, runtime, log, data_dir or (FRAME_DATA_DIR if frame else None), vk_1_0=frame)
    except axml.AxmlError as e:
        raise PatchError(f"Couldn't update AndroidManifest.xml: {e}")

    entries = []   # (ZipInfo, data), signature files dropped
    with zipfile.ZipFile(apk) as zin:
        template = zin.getinfo(LIB + "libvrapi.so")
        for info in zin.infolist():
            n = info.filename
            if n.startswith("META-INF/") and (n.upper().endswith(SIGNATURE_FILES) or n.upper() == "META-INF/MANIFEST.MF"):
                continue
            new = zipfile.ZipInfo(n, info.date_time)
            new.compress_type, new.external_attr = info.compress_type, info.external_attr
            if n in replace and n != LIB + "libopenxr_loader.so":
                log(f"  replaced {n}")
            entries.append((new, replace.pop(n) if n in replace else zin.read(n)))
    for n, data in replace.items():   # libopenxr_loader.so (new)
        info = zipfile.ZipInfo(n, template.date_time)
        info.compress_type, info.external_attr = template.compress_type, template.external_attr
        entries.append((info, data))
        log(f"  added {n}")

    log("Generating a new random signing key...")
    key, cert = new_key()
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()
    manifest, sf, rsa_block = jar_signature([(i.filename, d) for i, d in entries if not i.is_dir()], key, cert)

    tmp = out + ".partial"
    with zipfile.ZipFile(tmp, "w") as zout:
        stamp = entries[0][0].date_time if entries else (2026, 1, 1, 0, 0, 0)
        for name, data in (("META-INF/MANIFEST.MF", manifest), ("META-INF/CERT.SF", sf), ("META-INF/CERT.RSA", rsa_block)):
            info = zipfile.ZipInfo(name, stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(info, data)
        for info, data in entries:
            if info.compress_type == zipfile.ZIP_STORED:
                _aligned(zout, info, data, 4096 if info.filename.endswith(".so") else 4)
            else:
                zout.writestr(info, data)
    os.replace(tmp, out)

    key_path = os.path.splitext(out)[0] + ".signing-key.pem"
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    log(f"Wrote {out} ({os.path.getsize(out) // (1024 * 1024)} MB)")
    log(f"Signing key: {key_path}")
    log(f"Key fingerprint (SHA-256): {fingerprint}")
    return out, key_path, fingerprint


def main():
    ap = argparse.ArgumentParser(description="Patch an Echo VR Quest APK to run on OpenXR.")
    ap.add_argument("apk")
    ap.add_argument("-o", "--out")
    ap.add_argument("--frame", action="store_true",
                    help=f"Steam Frame build: game data in {FRAME_DATA_DIR}, Vulkan 1.0")
    ap.add_argument("--data-dir", help="game data folder to use instead (testing)")
    a = ap.parse_args()
    out = a.out or os.path.splitext(a.apk)[0] + ("_openxr_frame.apk" if a.frame else "_openxr.apk")
    try:
        patch(a.apk, out, data_dir=a.data_dir, frame=a.frame)
    except PatchError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
