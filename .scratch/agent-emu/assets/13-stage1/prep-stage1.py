# Stage 1 boot prep for crosvm/WHPX: initrd (ramdisks + bootconfig), kernel, blank disks.
# Follows ticket 21 Pass A recipe (Cuttlefish RepackGem5BootImage shape).
import os, shutil, struct

S = "C:/dev/agent-emu-work/stage1"
R = "C:/dev/agent-emu-work/run"
os.makedirs(R, exist_ok=True)

EXTRA = """androidboot.slot_suffix=_a
androidboot.force_normal_boot=1
androidboot.verifiedbootstate=orange
androidboot.boot_devices=pci0000:00/0000:00:01.0,pci0000:00/0000:00:02.0,pci0000:00/0000:00:03.0,pci0000:00/0000:00:04.0,pci0000:00/0000:00:05.0,pci0000:00/0000:00:06.0,pci0000:00/0000:00:07.0,pci0000:00/0000:00:08.0
androidboot.console=hvc1
androidboot.serialconsole=1
androidboot.serialno=AGENTEMU0001
androidboot.ddr_size=4096MB
androidboot.lcd_density=320
androidboot.setupwizard_mode=DISABLED
androidboot.enable_bootanimation=0
androidboot.selinux=permissive
androidboot.enable_confirmationui=0
androidboot.audio.tinyalsa.ignore_output=true
androidboot.audio.tinyalsa.simulate_input=true
androidboot.fstab_suffix=cf.f2fs.hctr2
androidboot.wifi_mac_prefix=5554
androidboot.wifi_impl=virt_wifi
androidboot.hw_timeout_multiplier=3
androidboot.hypervisor.version=cf-crosvm
androidboot.hypervisor.vm.supported=1
androidboot.hypervisor.protected_vm.supported=0
androidboot.vendor.apex.com.android.hardware.keymint=com.android.hardware.keymint.rust_nonsecure
androidboot.vendor.apex.com.android.hardware.gatekeeper=com.android.hardware.gatekeeper.nonsecure
androidboot.vendor.apex.com.android.hardware.secure_element=com.android.hardware.secure_element
androidboot.vendor.apex.com.android.hardware.strongbox=none
androidboot.vendor.apex.com.android.hardware.weaver=none
androidboot.vendor.apex.com.android.hardware.graphics.composer=com.android.hardware.graphics.composer.ranchu
androidboot.cpuvulkan.version=4206592
androidboot.hardware.gralloc=minigbm
androidboot.hardware.hwcomposer=ranchu
androidboot.hardware.hwcomposer.display_finder_mode=drm
androidboot.hardware.egl=angle
androidboot.hardware.vulkan=pastel
androidboot.opengles.version=196609
androidboot.vsock_lights_port=6900
androidboot.vsock_lights_cid=2
androidboot.openthread_node_id=1
androidboot.adb.enabled=1
androidboot.vsock_tombstone_port=6600
androidboot.modem_simulator_ports=9600
"""

bc = open(f"{S}/unpack/vendor_boot/bootconfig", "rb").read().rstrip(b"\0")
if bc and not bc.endswith(b"\n"):
    bc += b"\n"
bc += EXTRA.encode()
bc += b"\0" * (-len(bc) % 4)
with open(f"{R}/initrd.img", "wb") as f:
    for p in (f"{S}/unpack/init_boot/ramdisk", f"{S}/unpack/vendor_boot/vendor_ramdisk00"):
        f.write(open(p, "rb").read())
    f.write(bc + struct.pack("<II", len(bc), sum(bc) & 0xFFFFFFFF) + b"#BOOTCONFIG\n")
shutil.copy(f"{S}/unpack/boot/kernel", f"{R}/kernel")

# Disk components live next to os_composite.img; create_composite needs relative paths.
for name in ("boot", "init_boot", "vendor_boot", "vbmeta", "vbmeta_system", "vbmeta_system_dlkm", "vbmeta_vendor_dlkm"):
    dst = f"{R}/{name}.img"
    if not os.path.exists(dst):
        shutil.copy(f"{S}/img/{name}.img", dst)
if not os.path.exists(f"{R}/super.img"):
    os.replace(f"{S}/super.raw.img", f"{R}/super.img")
for name, size in (("misc", 1 << 20), ("metadata", 64 << 20), ("userdata", 8 << 30)):
    with open(f"{R}/{name}.img", "wb") as f:
        f.truncate(size)
print("initrd", os.path.getsize(f"{R}/initrd.img"), "bootconfig bytes", len(bc))
print(sorted(os.listdir(R)))
