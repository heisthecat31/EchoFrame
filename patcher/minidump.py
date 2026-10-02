"""Reads the crash out of a Breakpad minidump (the .dmp files Echo writes on a crash): the
signal, the faulting address, the crashing thread's pc/lr/sp as library + offset, and the
return addresses found on its stack. Offsets in a library are its ELF addresses, so they
can be looked up in a disassembly (libr15.so's first segment loads at 0)."""
import struct

STREAM_THREADS, STREAM_MODULES, STREAM_EXCEPTION = 3, 4, 6
SIGNALS = {4: "SIGILL", 5: "SIGTRAP", 6: "SIGABRT", 7: "SIGBUS", 8: "SIGFPE", 11: "SIGSEGV"}


class MinidumpError(Exception):
    pass


def _string(d, rva):
    n, = struct.unpack_from("<I", d, rva)
    return d[rva + 4:rva + 4 + n].decode("utf-16-le", "replace")


def parse(d):
    """{'signal', 'code', 'address', 'thread', 'context': (size, rva), 'modules': [(base, size, name)],
    'stacks': {thread id: (start, rva, size)}}"""
    if d[:4] != b"MDMP":
        raise MinidumpError("not a minidump")
    count, dir_rva = struct.unpack_from("<II", d, 8)
    out = {"modules": [], "stacks": {}}
    for i in range(count):
        kind, size, rva = struct.unpack_from("<III", d, dir_rva + 12 * i)
        if kind == STREAM_MODULES:
            n, = struct.unpack_from("<I", d, rva)
            for j in range(n):
                m = rva + 4 + 108 * j
                base, msize, _, _, name = struct.unpack_from("<QIIII", d, m)
                out["modules"].append((base, msize, _string(d, name)))
        elif kind == STREAM_THREADS:
            n, = struct.unpack_from("<I", d, rva)
            for j in range(n):
                t = rva + 4 + 48 * j
                tid, = struct.unpack_from("<I", d, t)
                start, ssize, srva = struct.unpack_from("<QII", d, t + 24)
                out["stacks"][tid] = (start, srva, ssize)
        elif kind == STREAM_EXCEPTION:
            tid, = struct.unpack_from("<I", d, rva)
            code, flags, _, addr = struct.unpack_from("<IIQQ", d, rva + 8)
            out.update(thread=tid, signal=code, code=flags, address=addr,
                       context=struct.unpack_from("<II", d, rva + 160))
    if "context" not in out:
        raise MinidumpError("no exception in this minidump")
    return out


def where(modules, a):
    for base, size, name in modules:
        if base <= a < base + size:
            return f"{name.rsplit('/', 1)[-1]}+{a - base:#x}"
    return None


def summary(d, max_frames=40):
    """The crash as text lines."""
    m = parse(d)
    mods = m["modules"]
    csize, crva = m["context"]
    ctx = d[crva:crva + csize]
    # ARM64 context: flags/cpsr, x0..x28, fp, lr, sp at 8..264; pc at 264 (current Breakpad)
    # or 272 (older Breakpad, 64-bit flags). Take the one inside a library.
    lr, sp = struct.unpack_from("<QQ", ctx, 248)
    pcs = [struct.unpack_from("<Q", ctx, o)[0] for o in (264, 272) if o + 8 <= len(ctx)]
    pc = next((p for p in pcs if where(mods, p)), pcs[0] if pcs else 0)
    sig = SIGNALS.get(m["signal"], f"signal {m['signal']}")
    lines = [f"{sig} (code {m['code']}) at address {m['address']:#x}, thread {m['thread']}",
             f"pc {pc:#x}  {where(mods, pc) or '?'}",
             f"lr {lr:#x}  {where(mods, lr) or '?'}",
             f"sp {sp:#x}"]
    for i, r in enumerate(struct.unpack_from("<29Q", ctx, 8)):
        if where(mods, r):
            lines.append(f"x{i} {r:#x}  {where(mods, r)}")
    stack = m["stacks"].get(m["thread"])
    if stack:
        start, srva, ssize = stack
        lines.append("stack (values that point into a library, from sp up):")
        frames = 0
        first = max(0, sp - start) if start <= sp < start + ssize else 0
        for off in range(first - first % 8, ssize - 7, 8):
            v, = struct.unpack_from("<Q", d, srva + off)
            w = where(mods, v)
            if w and not w.startswith(("[", "app_process")):
                lines.append(f"  sp+{start + off - sp:#x}: {w}")
                frames += 1
                if frames >= max_frames:
                    break
    lines.append("libraries:")
    lines += [f"  {b:#x}-{b + s:#x} {n}" for b, s, n in mods if n.endswith(".so")]
    return lines


if __name__ == "__main__":
    import sys
    print("\n".join(summary(open(sys.argv[1], "rb").read())))
