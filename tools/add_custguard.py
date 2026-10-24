"""Puts the customization guard (build/libcustgd.so) into an Echo VR APK whose libvrapi.so isn't
this runtime (a mod's own build of it), without touching that library's code.

libvrapi.so's dynamic section has no free entry for another DT_NEEDED, so the guard takes the
place of libvrapi.so's libandroid.so entry: the name is rewritten in place (libcustgd.so is
shorter). Nothing is lost: libr15.so, which loads libvrapi.so, needs libandroid.so itself, and
libvrapi.so's symbols resolve across that whole load group. Refused when libandroid.so has a
symbol-version requirement or its name is shared with anything else in the string table.

The output is unsigned (the old signature files are dropped): sign it, or let an installer that
signs on the headset (the Echo VR installer's tester bundle install) do it.

    python tools/add_custguard.py <in.apk> <out.apk> [--guard build/libcustgd.so]
"""
import argparse
import os
import struct
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = "lib/arm64-v8a/"
OLD, NEW = b"libandroid.so", b"libcustgd.so"
DT_NULL, DT_NEEDED, DT_STRTAB, DT_STRSZ, DT_VERNEED, DT_VERNEEDNUM = 0, 1, 5, 10, 0x6ffffffe, 0x6fffffff


def vaddr_to_offset(phdrs, vaddr):
    for p_type, p_offset, p_vaddr, p_filesz in phdrs:
        if p_type == 1 and p_vaddr <= vaddr < p_vaddr + p_filesz:   # PT_LOAD
            return vaddr - p_vaddr + p_offset
    sys.exit(f"address {vaddr:#x} isn't in a loaded segment")


def patch_needed(elf):
    """libvrapi.so with its libandroid.so DT_NEEDED renamed to libcustgd.so (or as it is, if done)."""
    elf = bytearray(elf)
    if elf[:4] != b"\x7fELF" or elf[4] != 2:
        sys.exit("libvrapi.so isn't a 64-bit ELF")
    e_phoff, = struct.unpack_from("<Q", elf, 0x20)
    e_phentsize, e_phnum = struct.unpack_from("<HH", elf, 0x36)
    phdrs, dyn_off = [], None
    for i in range(e_phnum):
        p_type, _, p_offset, p_vaddr, _, p_filesz = struct.unpack_from("<IIQQQQ", elf, e_phoff + i * e_phentsize)
        phdrs.append((p_type, p_offset, p_vaddr, p_filesz))
        if p_type == 2:   # PT_DYNAMIC
            dyn_off = p_offset
    if dyn_off is None:
        sys.exit("libvrapi.so has no dynamic section")
    dyn, i = {}, 0
    needed = []
    while True:
        tag, val = struct.unpack_from("<qQ", elf, dyn_off + 16 * i)
        if tag == DT_NULL:
            break
        if tag == DT_NEEDED:
            needed.append(val)
        dyn[tag] = val
        i += 1
    strtab = vaddr_to_offset(phdrs, dyn[DT_STRTAB])
    strsz = dyn[DT_STRSZ]
    name = lambda off: bytes(elf[strtab + off:elf.index(b"\0", strtab + off)])
    names = [name(v) for v in needed]
    if NEW in names:
        print("libvrapi.so already loads libcustgd.so")
        return bytes(elf)
    if OLD not in names:
        sys.exit(f"libvrapi.so doesn't need {OLD.decode()} (needs {[n.decode() for n in names]})")
    at = needed[names.index(OLD)]

    # Nothing else may point into the string: version requirements, symbol names.
    if DT_VERNEED in dyn:
        vn = vaddr_to_offset(phdrs, dyn[DT_VERNEED])
        for _ in range(dyn.get(DT_VERNEEDNUM, 0)):
            _, cnt, vn_file, vn_aux, vn_next = struct.unpack_from("<HHIII", elf, vn)
            if at <= vn_file < at + len(OLD) + 1:
                sys.exit(f"{OLD.decode()} has a symbol-version requirement; renaming it would break loading")
            aux = vn + vn_aux
            for _ in range(cnt):
                _, _, _, vna_name, vna_next = struct.unpack_from("<IHHII", elf, aux)
                if at <= vna_name < at + len(OLD) + 1:
                    sys.exit("a version name shares the libandroid.so string")
                aux += vna_next
            vn += vn_next
    if bytes(elf[strtab:strtab + strsz]).count(OLD[3:]) != 1:   # "android.so" used once: no suffix sharing
        sys.exit("the libandroid.so string is shared with another name")
    elf[strtab + at:strtab + at + len(OLD) + 1] = NEW + b"\0" * (len(OLD) + 1 - len(NEW))
    print(f"libvrapi.so: DT_NEEDED {OLD.decode()} -> {NEW.decode()} (dynstr+{at:#x})")
    return bytes(elf)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("apk")
    ap.add_argument("out")
    ap.add_argument("--guard", default=os.path.join(ROOT, "build", "libcustgd.so"))
    a = ap.parse_args()
    guard = open(a.guard, "rb").read()
    with zipfile.ZipFile(a.apk) as src, zipfile.ZipFile(a.out, "w", zipfile.ZIP_DEFLATED) as dst:
        names = src.namelist()
        if LIB + "libvrapi.so" not in names:
            sys.exit("no lib/arm64-v8a/libvrapi.so in this APK")
        for info in src.infolist():
            if info.filename.startswith("META-INF/") and info.filename.rsplit(".", 1)[-1].upper() in ("MF", "SF", "RSA", "DSA", "EC"):
                continue   # the old signature: it no longer matches
            if info.filename == LIB + "libcustgd.so":
                continue
            data = src.read(info)
            if info.filename == LIB + "libvrapi.so":
                data = patch_needed(data)
            out = zipfile.ZipInfo(info.filename, info.date_time)
            out.compress_type = info.compress_type
            out.external_attr = info.external_attr
            if out.compress_type == zipfile.ZIP_STORED:
                # Stored entries start on a boundary (as zipalign does): 4 bytes, a page for .so.
                align = 16384 if info.filename.endswith(".so") else 4
                header = 30 + len(info.filename.encode("utf-8"))
                pad = (-(dst.fp.tell() + header + 4)) % align   # 4: the padding field's own header
                out.extra = struct.pack("<HH", 0xd935, pad) + b"\0" * pad   # zipalign's padding id
            dst.writestr(out, data)
        guard_info = zipfile.ZipInfo(LIB + "libcustgd.so", (2026, 1, 1, 0, 0, 0))
        guard_info.compress_type = zipfile.ZIP_DEFLATED
        dst.writestr(guard_info, guard)
    print(f"wrote {a.out} (unsigned; libcustgd.so added)")


if __name__ == "__main__":
    main()
