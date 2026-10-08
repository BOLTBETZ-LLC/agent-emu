# Lever D: run dir for Android 11 (API 30) Cuttlefish only_phone 16396005 on crosvm/WHPX, direct kernel boot.
# initrd = boot ramdisk + vendor ramdisk (both legacy LZ4; kernel 5.4 unpacks them in order). No bootconfig:
# Android 11 init reads androidboot.* from the kernel cmdline, so they go in cmdline.txt.
import os, shutil, subprocess, sys
W = "C:/dev/agent-emu-work"; L = f"{W}/leverD/aosp-android11-gsi"; U = f"{L}/unpack"; I = f"{L}/img"
D = sys.argv[1] if len(sys.argv) > 1 else f"{L}/d30"; os.makedirs(D, exist_ok=True)
CROSVM = f"{W}/crosvm-pmem/target/release/crosvm.exe"
with open(f"{D}/initrd.img", "wb") as f:
    # ramdisk-debug.img (force_debuggable) so adb_debug.prop overrides props; then vendor ramdisk; then the prop overlay
    for p in (f"{L}/ramdisk-debug.img", f"{U}/vendor_boot/vendor_ramdisk"): f.write(open(p, "rb").read())
PROPS = ["ro.vendor.hwcomposer.display_finder_mode=drm"] + os.environ.get("AE_PROPS", "").split()
subprocess.run([sys.executable, f"{W}/leverD/tools/prop_overlay.py", f"{D}/initrd.img", f"{U}/dbg/first_stage_ramdisk/adb_debug.prop"] + PROPS, check=True)
shutil.copy(f"{U}/boot/kernel", f"{D}/kernel")
for n in ("vbmeta", "vbmeta_system"):  # Cuttlefish pads vbmeta partitions to 64 KiB; libfs_avb reads 64 KiB
    if os.path.exists(f"{D}/{n}.img"): os.remove(f"{D}/{n}.img")
    d = open(f"{I}/{n}.img", "rb").read(); open(f"{D}/{n}.img", "wb").write(d + bytes(65536 - len(d)))
for n in ("boot", "vendor_boot", "super", "cache"):
    if not os.path.exists(f"{D}/{n}.img"): os.link(f"{I}/{n}.img", f"{D}/{n}.img")
# userdata: ext4, formatted once from the guest (mke2fs -t ext4 -b 4096 -O encrypt,verity); fstab.f2fs needs metadata encryption that fails on first boot here
if not os.path.exists(f"{D}/userdata.img"): raise SystemExit("format userdata.img as ext4 first")
if not os.path.exists(f"{D}/apk.img"): os.link(f"{W}/run/apk.img", f"{D}/apk.img")
for n, sz in (("misc", 1 << 20), ("frp", 1 << 20), ("metadata", 64 << 20), ("out", 64 << 20)):
    open(f"{D}/{n}.img", "wb").truncate(sz)
for f in ("os_composite.img", "os_composite.img.filler", "os_composite.img.footer", "os_composite.img.header"):
    if os.path.exists(f"{D}/{f}"): os.remove(f"{D}/{f}")
parts = ("misc:misc.img:writable frp:frp.img:writable boot_a:boot.img boot_b:boot.img vendor_boot_a:vendor_boot.img "
         "vendor_boot_b:vendor_boot.img vbmeta_a:vbmeta.img vbmeta_b:vbmeta.img vbmeta_system_a:vbmeta_system.img "
         "vbmeta_system_b:vbmeta_system.img super:super.img userdata:userdata.img:writable cache:cache.img:writable "
         "metadata:metadata.img:writable").split()
subprocess.run([CROSVM, "create_composite", "os_composite.img"] + parts, cwd=D, check=True, capture_output=True)
vendor = open(f"{U}/vendor_boot.txt").read().split("vendor command line args: ")[1].split("\n")[0].strip().replace("fstab_suffix=f2fs", "fstab_suffix=ext4")
extra = os.environ.get("AE_EXTRA_CMDLINE", "")
cmd = (vendor + " androidboot.slot_suffix=_a androidboot.force_normal_boot=1 androidboot.verifiedbootstate=orange "
       "androidboot.boot_devices=pci0000:00/0000:00:01.0,pci0000:00/0000:00:02.0,pci0000:00/0000:00:03.0 "
       "androidboot.console=hvc1 androidboot.serialconsole=1 androidboot.serialno=AGENTEMU0030 androidboot.lcd_density=320 "
       "androidboot.setupwizard_mode=DISABLED androidboot.selinux=permissive androidboot.hardware.gralloc=minigbm "
       "androidboot.hardware.hwcomposer=ranchu androidboot.hardware.hwcomposer.display_finder_mode=drm androidboot.hardware.egl=angle androidboot.hardware.vulkan=pastel androidboot.opengles.version=196608 "
       "androidboot.cpuvulkan.version=4202496 androidboot.vsock_tombstone_port=6600 androidboot.modem_simulator_ports=9600 "
       "androidboot.wifi_mac_prefix=5554 androidboot.adb.enabled=1 loop.max_part=7 " + extra).strip()
open(f"{D}/cmdline.txt", "w").write(cmd)
print(D, len(cmd)); print(cmd)
