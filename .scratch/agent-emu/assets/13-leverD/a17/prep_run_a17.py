# Android 17 run dir for fleet_a17.py (same file names fleet_diet.py expects).
# initrd-dax-pmem.img = A17 init_boot ramdisk + A17 vendor_ramdisk00 + DAX initramfs.img (6.12.93 modules win, last)
#                       + pmem fstab overlay (A17 first-stage fstabs) + bootconfig (A17 vendor bootconfig + stage1 EXTRA).
# Repack outputs (super.img, vbmeta*, system-pmem.img) come from a17/out after build.sh; run with --images-only-boot to skip them.
import os, re, shutil, struct, subprocess, sys
W = "C:/dev/agent-emu-work"; L = f"{W}/leverD"; A = f"{L}/aosp-android-latest-release-16373615"; U = f"{A}/unpack"
R = f"{L}/a17/run-a17"; OUT = f"{L}/a17/out"; os.makedirs(R, exist_ok=True)
def link(src, dst):
    if os.path.exists(dst): os.remove(dst)
    os.link(src, dst)
# boot images from the stock A17 img zip
for n in ("boot", "init_boot", "vendor_boot"):
    p = f"{A}/img/{n}.img"
    if not os.path.exists(p): subprocess.run(["unzip", "-o", "-q", f"{A}/aosp_cf_x86_64_only_phone-img-16373615.zip", f"{n}.img", "-d", f"{A}/img"], check=True)
    link(p, f"{R}/{n}.img")
link(f"{W}/run-slim4/kernel-dax", f"{R}/kernel-dax")
link(f"{W}/run/apk.img", f"{R}/apk.img"); shutil.copy(f"{W}/run/apk.size", f"{R}/apk.size")
# bootconfig: A17 vendor bootconfig + the stage1 EXTRA keys (same as every API 36 run)
extra = re.search(r'EXTRA = """(.*?)"""', open(f"{W}/prep-stage1.py").read(), re.S).group(1)
bc = open(f"{U}/vendor_boot/bootconfig", "rb").read().rstrip(b"\0")
if bc and not bc.endswith(b"\n"): bc += b"\n"
bc += extra.encode()
# A17 vendor bootconfig dropped this key, but the image ships two camera provider APEXes: apexd-bootstrap aborts without it.
if b"emulated.camera.provider.hal=" not in bc: bc += b"androidboot.vendor.apex.com.google.emulated.camera.provider.hal=com.google.emulated.camera.provider.hal" + bytes([10])
bc += b"\0" * (-len(bc) % 4)
tmp = f"{R}/initrd-dax.img"
with open(tmp, "wb") as f:
    for p in (f"{U}/init_boot/ramdisk", f"{U}/vendor_boot/vendor_ramdisk00", f"{W}/stage2/kernel/initramfs.img"): f.write(open(p, "rb").read())
    f.write(bc + struct.pack("<II", len(bc), sum(bc) & 0xFFFFFFFF) + b"#BOOTCONFIG\n")
env = dict(os.environ, AE_SYSTEM_PMEM="1")
subprocess.run([sys.executable, f"{L}/a17/fstab_overlay_a17.py", f"{U}/vendor_boot/vendor_ramdisk00", tmp, f"{R}/initrd-dax-pmem.img"], env=env, check=True)
if "--images-only-boot" not in sys.argv:
    for n in ("super", "vbmeta", "vbmeta_system", "vbmeta_system_dlkm", "vbmeta_vendor_dlkm", "system-pmem"):
        link(f"{OUT}/{n}.img", f"{R}/{n}.img")
print(sorted(os.listdir(R)))
