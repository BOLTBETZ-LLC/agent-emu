# Rewrite the bootconfig trailer of an initrd for gfxstream (Cuttlefish gpu_mode=gfxstream values).
# usage: python mkinitrd.py <in initrd> <out initrd> key=value ...
import struct, sys
src, dst, *kv = sys.argv[1:]
d = open(src, "rb").read()
assert d.endswith(b"#BOOTCONFIG\n"), "no bootconfig trailer"
size, csum = struct.unpack("<II", d[-20:-12])
body = d[-20 - size:-20]
assert sum(body) & 0xFFFFFFFF == csum, "checksum mismatch"
lines = [l for l in body.rstrip(b"\0").decode().split("\n") if l]
want = dict(x.split("=", 1) for x in kv)
out = []
for l in lines:
    k = l.split("=", 1)[0].strip()
    if k not in want:
        out.append(l)
out += [f"{k}={v}" for k, v in want.items()]
bc = ("\n".join(out) + "\n").encode()
bc += b"\0" * (-len(bc) % 4)
with open(dst, "wb") as f:
    f.write(d[:-20 - size] + bc + struct.pack("<II", len(bc), sum(bc) & 0xFFFFFFFF) + b"#BOOTCONFIG\n")
print("\n".join(o for o in out if "hardware" in o or "density" in o or "cpuvulkan" in o or "opengles" in o))
