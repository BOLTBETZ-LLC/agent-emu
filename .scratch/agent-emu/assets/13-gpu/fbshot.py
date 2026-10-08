# Save the current fb.bin frame as PNG (asks crosvm to refresh first via the Device's fb pipe).
# usage: python fbshot.py <device id> <out.png>
import struct, sys
from PIL import Image
dev, out = sys.argv[1], sys.argv[2]
fb = rf"C:\dev\agent-emu-work\gpu\d{dev}\fb.bin"
try:
    with open(rf"\\.\pipe\ae-fb-{dev}", "r+b", buffering=0) as p:
        p.write(b"r"); p.read(8)
except OSError as e:
    print("refresh pipe:", e)
d = open(fb, "rb").read()
seq, w, h, stride, fourcc, us, flushes = struct.unpack("<QIIIIQQ", d[:40])
img = Image.frombuffer("RGBA", (w, h), d[64:64 + stride * h], "raw", "RGBA", stride, 1).convert("RGB")
img.save(out)
print(f"seq={seq} {w}x{h} stride={stride} fourcc={fourcc:#x} flushes={flushes}")
