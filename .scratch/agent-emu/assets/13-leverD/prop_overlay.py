# Append a legacy-LZ4 framed cpio overlay that replaces first_stage_ramdisk/adb_debug.prop (debug ramdisk:
# force_debuggable makes second-stage init load it last, overriding other props).
# usage: prop_overlay.py <initrd in/out> <base adb_debug.prop> KEY=VAL...
import struct, sys, lz4.block
def newc(name, mode, data, ino):
    nb = name.encode() + b"\0"
    hdr = "070701" + "".join(f"{v:08x}" for v in (ino, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(nb), 0))
    rec = hdr.encode() + nb; rec += b"\0" * (-len(rec) % 4) + data
    return rec + b"\0" * (-len(rec) % 4)
prop = open(sys.argv[2], "rb").read().rstrip(b"\n") + b"\n# agent-emu lever D\n" + "".join(a + "\n" for a in sys.argv[3:]).encode()
cp = newc("first_stage_ramdisk/adb_debug.prop", 0o100644, prop, 910000) + newc("TRAILER!!!", 0, b"", 0)
cp += b"\0" * (-len(cp) % 512)
blk = lz4.block.compress(cp, store_size=False, mode="high_compression")
open(sys.argv[1], "ab").write(struct.pack("<I", 0x184C2102) + struct.pack("<I", len(blk)) + blk)
print(prop.decode())
