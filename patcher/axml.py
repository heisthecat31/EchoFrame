"""Edits Android binary XML (AndroidManifest.xml inside an APK), just enough for EchoQuestXR.

Binary XML is a file header, a string pool, a resource-id map for attribute names, then a
stream of element records that refer to strings by index. Supported edits:
  replace_string(old, new)      change a string's text everywhere it's used
  add_uses_permission(name)     add <uses-permission android:name="..."/> after the last one
Strings are only appended to the pool, so every existing index stays valid.
"""
import struct

RES_XML = 0x0003
RES_STRING_POOL = 0x0001
RES_RESOURCE_MAP = 0x0180
RES_START_ELEMENT = 0x0102
RES_END_ELEMENT = 0x0103
UTF8_FLAG = 0x100
SORTED_FLAG = 0x1


class AxmlError(Exception):
    pass


def _len_utf8(b, o):
    n = b[o]
    return (((n & 0x7F) << 8) | b[o + 1], 2) if n & 0x80 else (n, 1)


def _len_utf16(b, o):
    n = struct.unpack_from("<H", b, o)[0]
    return ((((n & 0x7FFF) << 16) | struct.unpack_from("<H", b, o + 2)[0]), 4) if n & 0x8000 else (n, 2)


class Manifest:
    def __init__(self, data):
        t, hs, size = struct.unpack_from("<HHI", data, 0)
        if t != RES_XML:
            raise AxmlError("not binary XML")
        o = hs
        t, hs, size = struct.unpack_from("<HHI", data, o)
        if t != RES_STRING_POOL:
            raise AxmlError("no string pool")
        count, styles, self.flags, start, _ = struct.unpack_from("<IIIII", data, o + 8)
        if styles:
            raise AxmlError("styled strings aren't supported")
        offsets = struct.unpack_from(f"<{count}I", data, o + hs)
        self.strings = []
        for off in offsets:
            p = o + start + off
            if self.flags & UTF8_FLAG:
                _, a = _len_utf8(data, p)            # length in UTF-16 units, then in bytes
                n, c = _len_utf8(data, p + a)
                self.strings.append(data[p + a + c:p + a + c + n].decode("utf-8"))
            else:
                n, a = _len_utf16(data, p)
                self.strings.append(data[p + a:p + a + 2 * n].decode("utf-16-le"))
        self.rest = data[o + size:]                 # resource map and the element stream

    # ------------------------------------------------------------------ edits
    def index(self, s):
        return self.strings.index(s) if s in self.strings else -1

    def add_string(self, s):
        i = self.index(s)
        if i >= 0:
            return i
        self.strings.append(s)
        return len(self.strings) - 1

    def replace_string(self, old, new):
        i = self.index(old)
        if i < 0:
            return False
        if new in self.strings:
            raise AxmlError(f"'{new}' already exists; refusing to make two entries equal")
        self.strings[i] = new
        return True

    def _chunks(self):
        """(offset, type, size) of each chunk in self.rest."""
        o, out = 0, []
        while o + 8 <= len(self.rest):
            t, hs, size = struct.unpack_from("<HHI", self.rest, o)
            out.append((o, t, size))
            o += size
        return out

    def has_permission(self, name):
        i = self.index(name)
        if i < 0:
            return False
        for o, t, size in self._chunks():
            if t == RES_START_ELEMENT and self.strings[struct.unpack_from("<I", self.rest, o + 20)[0]] == "uses-permission":
                count = struct.unpack_from("<H", self.rest, o + 28)[0]
                for a in range(count):
                    if struct.unpack_from("<I", self.rest, o + 36 + 20 * a + 8)[0] == i:
                        return True
        return False

    def add_uses_permission(self, name):
        """Clones the last <uses-permission> (start and end records) with a new name."""
        if self.has_permission(name):
            return False
        last = None
        chunks = self._chunks()
        for k, (o, t, size) in enumerate(chunks):
            if t == RES_START_ELEMENT and self.strings[struct.unpack_from("<I", self.rest, o + 20)[0]] == "uses-permission":
                end = chunks[k + 1]
                if end[1] != RES_END_ELEMENT:
                    raise AxmlError("a <uses-permission> with children isn't supported")
                last = (o, size, end[0], end[2])
        if not last:
            raise AxmlError("the manifest has no <uses-permission> to copy")
        so, ssize, eo, esize = last
        start = bytearray(self.rest[so:so + ssize])
        if struct.unpack_from("<H", start, 28)[0] != 1:
            raise AxmlError("expected <uses-permission> with one attribute")
        idx = self.add_string(name)
        struct.pack_into("<I", start, 36 + 8, idx)        # raw value
        struct.pack_into("<I", start, 36 + 16, idx)       # typed value (a string index)
        end = self.rest[eo:eo + esize]
        at = eo + esize
        self.rest = self.rest[:at] + bytes(start) + end + self.rest[at:]
        return True

    # ------------------------------------------------------------------ writing
    def _pool(self):
        utf8 = bool(self.flags & UTF8_FLAG)
        data, offsets = bytearray(), []
        for s in self.strings:
            offsets.append(len(data))
            if utf8:
                b = s.encode("utf-8")
                for n in (len(s.encode("utf-16-le")) // 2, len(b)):
                    data += bytes([n]) if n < 0x80 else bytes([0x80 | (n >> 8), n & 0xFF])
                data += b + b"\0"
            else:
                u = s.encode("utf-16-le")
                n = len(u) // 2
                data += struct.pack("<H", n) if n < 0x8000 else struct.pack("<HH", 0x8000 | (n >> 16), n & 0xFFFF)
                data += u + b"\0\0"
        data += b"\0" * (-len(data) % 4)
        header = 28
        start = header + 4 * len(self.strings)
        flags = self.flags & ~SORTED_FLAG       # strings were appended: no longer sorted
        pool = struct.pack("<HHIIIIII", RES_STRING_POOL, header, start + len(data), len(self.strings), 0, flags, start, 0)
        return pool + struct.pack(f"<{len(offsets)}I", *offsets) + bytes(data)

    def to_bytes(self):
        body = self._pool() + self.rest
        return struct.pack("<HHI", RES_XML, 8, 8 + len(body)) + body


# The manifest changes EchoQuestXR makes: a LAUNCHER entry (Lepton on Steam Frame only
# starts activities with MAIN + LAUNCHER; Echo's has MAIN + INFO) and the OpenXR
# permissions the Khronos loader's runtime broker can require. All harmless on Quest.
OPENXR_PERMISSIONS = ("org.khronos.openxr.permission.OPENXR", "org.khronos.openxr.permission.OPENXR_SYSTEM")


def patch_manifest(data, log=print):
    m = Manifest(data)
    if m.replace_string("android.intent.category.INFO", "android.intent.category.LAUNCHER"):
        log("  manifest: Echo's activity is now a LAUNCHER entry (Lepton needs it)")
    elif m.index("android.intent.category.LAUNCHER") >= 0:
        log("  manifest: already a LAUNCHER entry")
    else:
        raise AxmlError("couldn't find Echo's activity category to make it a LAUNCHER entry")
    for p in OPENXR_PERMISSIONS:
        if m.add_uses_permission(p):
            log(f"  manifest: added uses-permission {p}")
    return m.to_bytes()
