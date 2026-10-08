# Insert a cpio archive into an initrd just before its bootconfig trailer (later archives win).
# usage: python addcpio.py <in initrd> <cpio> <out initrd>
import struct, sys
src, cp, dst = sys.argv[1:]
d = open(src, "rb").read()
assert d.endswith(b"#BOOTCONFIG\n")
size = struct.unpack("<I", d[-20:-16])[0]
head, tail = d[:-20 - size], d[-20 - size:]
c = open(cp, "rb").read()
pad = lambda b: b + b"\0" * (-len(b) % 512)
open(dst, "wb").write(pad(head) + pad(c) + tail)
print("ok", len(d), "->", len(pad(head) + pad(c) + tail))
