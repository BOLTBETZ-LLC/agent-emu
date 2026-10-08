# List / extract files from a legacy-LZ4 (or plain) newc cpio ramdisk. usage: rd.py <ramdisk> [outdir]
import sys, os, struct, lz4.block
def lz4_legacy(d):
    if d[:4] != b"\x02\x21\x4c\x18": return d
    i, out = 4, b""
    while i < len(d):
        n = struct.unpack("<I", d[i:i + 4])[0]; i += 4
        if n == 0x184C2102: continue
        out += lz4.block.decompress(d[i:i + n], uncompressed_size=8 << 20); i += n
    return out
def entries(c):
    p = 0
    while c[p:p + 6] == b"070701":
        f = [int(c[p + 6 + 8 * k:p + 14 + 8 * k], 16) for k in range(13)]
        ns, fs = f[11], f[6]; name = c[p + 110:p + 110 + ns - 1].decode(); q = (p + 110 + ns + 3) & ~3
        yield name, f[1], c[q:q + fs]
        if name == "TRAILER!!!": return
        p = (q + fs + 3) & ~3
c = lz4_legacy(open(sys.argv[1], "rb").read())
for n, m, d in entries(c):
    if len(sys.argv) > 2 and m & 0o170000 == 0o100000:
        p = os.path.join(sys.argv[2], n); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(d)
    elif len(sys.argv) == 2: print(oct(m), len(d), n)
