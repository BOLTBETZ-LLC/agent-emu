# Build an initrd with a cpio overlay that drops AVB from the /system fstab lines (spec.md §8:
# no dm-verity on the DAX-ready system image). The overlay is appended after the existing
# ramdisks; the kernel unpacks cpio archives in order, so the edited fstab wins.
# usage: python fstab_overlay_a17.py <vendor_ramdisk00> <in initrd> <out initrd>
import lz4.block, os, struct, sys

SRC = sys.argv.pop(1)  # A17: vendor ramdisk passed in
NAMES = ("first_stage_ramdisk/system/etc/fstab.cf.f2fs.hctr2",
         "first_stage_ramdisk/system/etc/fstab.cf.ext4.hctr2")

def lz4_legacy(d):
    i, out = 4, b""
    while i < len(d):
        n = struct.unpack("<I", d[i:i + 4])[0]
        if n == 0x184C2102:
            i += 4
            continue
        i += 4
        out += lz4.block.decompress(d[i:i + n], uncompressed_size=8 << 20)
        i += n
    return out

def entries(cpio):
    p = 0
    while cpio[p:p + 6] == b"070701":
        f = [int(cpio[p + 6 + 8 * k:p + 14 + 8 * k], 16) for k in range(13)]
        ns, fs = f[11], f[6]
        name = cpio[p + 110:p + 110 + ns - 1].decode()
        q = (p + 110 + ns + 3) & ~3
        yield name, f, cpio[q:q + fs]
        if name == "TRAILER!!!":
            return
        p = (q + fs + 3) & ~3

def newc(name, mode, data, ino):
    nb = name.encode() + b"\0"
    hdr = "070701" + "".join(f"{v:08x}" for v in (ino, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(nb), 0))
    rec = hdr.encode() + nb
    rec += b"\0" * (-len(rec) % 4) + data
    return rec + b"\0" * (-len(rec) % 4)

def edit(text):
    out = []
    for line in text.splitlines():
        if line.startswith("system /system "):
            line = line.replace(",avb=vbmeta_system,avb_keys=/avb", "").replace(",avb=vbmeta_system", "")
            if os.environ.get("AE_SYSTEM_PMEM") and " erofs " in line:
                # agent-emu: /system from the shared read-only pmem image, mapped with DAX.
                line = "/dev/block/pmem0 /system erofs ro,dax=always wait,first_stage_mount"
        out.append(line)
    return ("\n".join(out) + "\n").encode()

files = {n: (f, d) for n, f, d in entries(lz4_legacy(open(SRC, "rb").read())) if n in NAMES}
overlay = b"".join(newc(n, f[1], edit(d.decode()), 900000 + k) for k, (n, (f, d)) in enumerate(files.items()))
overlay += newc("TRAILER!!!", 0, b"", 0)
overlay += b"\0" * (-len(overlay) % 512)
# The vendor ramdisk is legacy LZ4 and the kernel's LZ4 reader keeps consuming chunks to the end of
# the buffer, so the overlay must be legacy-LZ4 framed too or it is read as a corrupt chunk.
blk = lz4.block.compress(overlay, store_size=False, mode="high_compression")
overlay = struct.pack("<I", 0x184C2102) + struct.pack("<I", len(blk)) + blk

old = open(sys.argv[1], "rb").read()
assert old.endswith(b"#BOOTCONFIG\n")
size = struct.unpack("<I", old[-20:-16])[0]
bc, ramdisks = old[-20 - size:-20], old[:-20 - size]
with open(sys.argv[2], "wb") as f:
    f.write(ramdisks + overlay + bc + struct.pack("<II", len(bc), sum(bc) & 0xFFFFFFFF) + b"#BOOTCONFIG\n")
print("overlay files:", list(files), "bytes", len(overlay))
for n in files:
    print([l for l in edit(files[n][1].decode()).decode().splitlines() if l.startswith("system /system ")][0])
