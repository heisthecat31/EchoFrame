"""EchoQuestXR patcher: turns your Echo VR Quest APK into one that runs on OpenXR.

    python patch.py <echo.apk> [-o <out.apk>]      (or use gui.pyw)

The same two changes runtime/build.py makes:
  lib/arm64-v8a/libvrapi.so         replaced by the EchoQuestXR runtime (VrApi on OpenXR)
  lib/arm64-v8a/libopenxr_loader.so added (the Khronos OpenXR loader)
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

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = "lib/arm64-v8a/"
RUNTIME_FILES = ("libvrapi.so", "libopenxr_loader.so")
# Meta's VrApi loader 1.1.40 as shipped in Echo VR build 4987566
META_VRAPI_SHA256 = "43bec081aae29781389823f690b5f63932905f76cb96452474a65ef8c16fd3e6"
SIGNATURE_FILES = (".SF", ".RSA", ".DSA", ".EC")


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


def patch(apk, out, log=print):
    log(f"Reading {apk}")
    log(f"  {inspect(apk)}")
    runtime = find_runtime()
    log(f"Runtime: {runtime}")
    replace = {LIB + f: open(os.path.join(runtime, f), "rb").read() for f in RUNTIME_FILES}

    entries = []   # (ZipInfo, data), signature files dropped
    with zipfile.ZipFile(apk) as zin:
        template = zin.getinfo(LIB + "libvrapi.so")
        for info in zin.infolist():
            n = info.filename
            if n.startswith("META-INF/") and (n.upper().endswith(SIGNATURE_FILES) or n.upper() == "META-INF/MANIFEST.MF"):
                continue
            new = zipfile.ZipInfo(n, info.date_time)
            new.compress_type, new.external_attr = info.compress_type, info.external_attr
            entries.append((new, replace.pop(n) if n in replace else zin.read(n)))
    for n, data in replace.items():   # libopenxr_loader.so (new)
        info = zipfile.ZipInfo(n, template.date_time)
        info.compress_type, info.external_attr = template.compress_type, template.external_attr
        entries.append((info, data))
        log(f"  added {n}")
    log(f"  replaced {LIB}libvrapi.so")

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
    a = ap.parse_args()
    out = a.out or os.path.splitext(a.apk)[0] + "_openxr.apk"
    try:
        patch(a.apk, out)
    except PatchError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
