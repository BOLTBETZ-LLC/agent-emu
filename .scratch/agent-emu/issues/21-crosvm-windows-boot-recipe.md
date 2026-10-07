# crosvm Windows boot recipe

Type: research
Status: resolved
Blocked by: 12, 13

## Question

What exact crosvm (Windows, WHPX) command line boots the Cuttlefish `aosp_cf_x86_64_only_phone` API 36 prebuilt 15581820 headless, using `aosp_cf_x86_64_only_phone-img-15581820.zip`? Answer these:

- Which args does Cuttlefish's `launch_cvd`/`assemble_cvd` pass to crosvm for x86_64 (crosvm_manager.cpp in google/android-cuttlefish)? List each disk (`os_composite`/super, vbmeta, misc, metadata, userdata), the kernel or bootloader, bootconfig, the cmdline, gpu, vsock, input and console. Can direct kernel boot replace the bootloader?
- Which of those args and devices exist in crosvm on Windows: composite disk, `--gpu`, vsock over named pipes, input event sources?
- How do you get `bzImage` and `initramfs` out of boot.img and vendor_boot.img (otatools `unpack_bootimg`), and which androidboot.* params must go on the kernel cmdline or into bootconfig?
- What's the minimal device set to reach `sys.boot_completed=1` with adb reachable from the host?
- Give a step-by-step recipe, critical path only.

## Answer

Reconciled 2026-10-07 from Pass A (Claude, `research/boot-recipe`) and Pass B (GPT-6.1 Sol, `*.codex.md`), checked against primary sources and against what was measured on this PC today. Sources pinned: google/android-cuttlefish `182ab1830a932ce541a640e5601a0b713834d50a`, google/crosvm `1ca5899813e21674d63b7888252a2ffd3c5f2c93` (the local checkout in `C:\dev\agent-emu-work\crosvm`), AOSP `android-16.0.0_r4`.

Measured on this PC today (beats either pass):
- crosvm builds with `--features all-msvc64,whpx,composite-disk` (needs `protoc` on PATH).
- img.zip: boot.img v4 (kernel only, empty cmdline), init_boot.img (ramdisk only), vendor_boot.img v4 with one fragment `vendor_ramdisk00`. Vendor cmdline: `printk.devkmsg=on audit=1 panic=-1 8250.nr_uarts=1 binder.impl=rust cma=0 firmware_class.path=/vendor/etc/ loop.max_part=7 init=/init bootconfig` (it already ends in `bootconfig`; add nothing). Vendor bootconfig: `androidboot.hardware=cutf_cvm`, the vsock pkt-buf key and the camera provider key.
- Kernel ikconfig (read from `unpack/boot/kernel`): `CONFIG_BOOT_CONFIG=y`, `CONFIG_CRYPTO_HCTR2=y`. So bootconfig works and the fstab suffix is `cf.f2fs.hctr2` (Cuttlefish picks hctr2 from exactly this check, `commands/assemble_cvd/guest_config.cc` L404-411). virtio_blk, virtio_console, virtio_pci, virtio_net and virtio-gpu are modules in the vendor ramdisk (`lib/modules/modules.load`).
- Vendor ramdisk contains `first_stage_ramdisk/system/etc/fstab.cf.f2fs.hctr2` and a recovery `/init -> /system/bin/init` symlink. init_boot ramdisk holds the real first-stage `/init`.
- `android-info.txt` has no `vulkan_swiftshader_apex` or `supports_bgra_framebuffers`, so Cuttlefish would set no `com.google.cf.vulkan` apex key and would use `display_framebuffer_format=rgba`.
- A stage-1 run already happened (`C:\dev\agent-emu-work\run`, 16:18, Pass A recipe, `AGENT_EMU_NO_NET=1`). Read from its logs, not re-run: first-stage mount worked with the over-listed `boot_devices`, fs_mgr formatted the blank metadata (vda18) and userdata (vda17), SurfaceFlinger and system_server started (14.9 s). Boot then stalled: `android.hardware.threadnetwork-service: Check failed: node_id > 0`, and the Cuttlefish light HAL aborts, so system_server loops on `Waited one second for android.hardware.light.ILights/default`. The bootconfig below adds the three keys Cuttlefish always sets for these (`openthread_node_id`, `vsock_lights_port/cid`, `vsock_tombstone_port`). That they unblock boot is **unverified** until the next run.

### Recipe (critical path)

Critical path: unpack, then initrd, then composite, then boot, then the console check. adb needs a ~15-line fork patch and is off the boot path.

Paths: `W=C:\dev\agent-emu-work`, `R=$W\run`, unpacked images in `$W\stage1`.

1. **Unpack** (`unpack_bootimg.py` from `system/tools/mkbootimg` r4, pure Python):
   ```
   python unpack_bootimg.py --boot_img img\boot.img        --out unpack\boot
   python unpack_bootimg.py --boot_img img\init_boot.img   --out unpack\init_boot
   python unpack_bootimg.py --boot_img img\vendor_boot.img --out unpack\vendor_boot
   ```
   Kernel = `unpack\boot\kernel` (bzImage, crosvm loads it directly). super.img is sparse: convert to an 8 GiB raw `$R\super.img` (done today with `simg2img.py`).
2. **initrd** = vendor ramdisk, then generic ramdisk **last**, then bootconfig and trailer. This is the order U-Boot uses (`boot/image-android.c`: vendor ramdisk memcpy, then boot ramdisk, then bootconfig) and the order AOSP requires ("the generic ramdisk is loaded last", source.android.com vendor-boot-partitions). `C:\dev\agent-emu-work\prep-stage1.py` has the two ramdisks the other way round; swap them there.
   ```python
   import struct
   S, R = r"C:\dev\agent-emu-work\stage1", r"C:\dev\agent-emu-work\run"
   EXTRA = open(R + r"\extra-bootconfig.txt", "rb").read()   # the block in step 3
   bc = open(S + r"\unpack\vendor_boot\bootconfig", "rb").read().rstrip(b"\0")
   bc = (bc if bc.endswith(b"\n") else bc + b"\n") + EXTRA
   bc += b"\0" * (-len(bc) % 4)
   with open(R + r"\initrd.img", "wb") as f:
       for p in (r"\unpack\vendor_boot\vendor_ramdisk00", r"\unpack\init_boot\ramdisk"):
           f.write(open(S + p, "rb").read())
       f.write(bc + struct.pack("<II", len(bc), sum(bc) & 0xFFFFFFFF) + b"#BOOTCONFIG\n")
   ```
   No key may repeat one in the vendor bootconfig (the kernel rejects a redefined value), so the block below leaves out `androidboot.hardware`, the vsock pkt-buf key and the camera key.
3. **Bootconfig lines to append** (full, one per line, from `bootconfig_args.cpp` `BootconfigArgsFromConfig`, crosvm `ConfigureGraphics` for guest_swiftshader, and the Gem5 bootloader stand-in in `boot_image_utils.cc` `RepackGem5BootImage`):
   ```
   androidboot.slot_suffix=_a
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
   androidboot.openthread_node_id=1
   androidboot.vsock_tombstone_port=6600
   androidboot.vsock_lights_port=6900
   androidboot.vsock_lights_cid=3
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
   androidboot.hardware.hwcomposer.display_framebuffer_format=rgba
   androidboot.hardware.egl=angle
   androidboot.hardware.vulkan=pastel
   androidboot.opengles.version=196609
   ```
   Deliberate deviations from Cuttlefish: `enable_confirmationui=0` (Cuttlefish sets 1 on crosvm, but its host peer on hvc8 is a sink here) and `selinux=permissive`. `4206592` = `VK_API_VERSION_1_3`. Ports: Cuttlefish uses `calc_vsock_port(6600/6900)` (flags.cc L1156-1161), which is the base for CID 3; the light HAL reads `ro.boot.vsock_lights_port` and defaults to 0 (`guest/hals/light/lights.rs` L57-67, r4).
4. **Blank disks** in `$R`: `misc.img` 1 MiB, `metadata.img` 64 MiB, `userdata.img` 8 GiB (plain truncate). Do not use the zip's 2.5 MB userdata.img. The fstab marks both `formattable`, and the run today showed fs_mgr formatting them on first boot.
5. **Composite disk**, in Cuttlefish order. Cuttlefish iterates a `std::map`, so partitions come out alphabetical (`disk/android_composite_disk_config.cc`, `primary_paths` map then the loop); A/B images appear as `_a` then `_b` pointing at the same file. Run from `$R` with relative paths (`create_composite` splits on `:`):
   ```
   crosvm.exe create_composite os_composite.img boot_a:boot.img boot_b:boot.img init_boot_a:init_boot.img init_boot_b:init_boot.img metadata:metadata.img:writable misc:misc.img:writable super:super.img:writable userdata:userdata.img:writable vbmeta_a:vbmeta.img vbmeta_b:vbmeta.img vbmeta_system_a:vbmeta_system.img vbmeta_system_b:vbmeta_system.img vbmeta_system_dlkm_a:vbmeta_system_dlkm.img vbmeta_system_dlkm_b:vbmeta_system_dlkm.img vbmeta_vendor_dlkm_a:vbmeta_vendor_dlkm.img vbmeta_vendor_dlkm_b:vbmeta_vendor_dlkm.img vendor_boot_a:vendor_boot.img vendor_boot_b:vendor_boot.img
   ```
   Partition order does not matter to Android (it finds partitions by name), but this order matches Cuttlefish. `super` is writable because Cuttlefish attaches os_composite through a writable qcow2 overlay.
6. **Boot** (PowerShell, from `$R`). Same shape as the run that reached system_server today. Changes: earlycon on COM1 to a file, and hvc1 on a named pipe so the console takes input:
   ```
   $s=@(); 4..20 | % { $s += "--serial"; $s += "hardware=virtio-console,num=$_,type=sink" }
   & "$W\crosvm\target\release\crosvm.exe" --log-level info run-mp `
     --hypervisor whpx --disable-sandbox --cpus 4 --mem 4096 `
     --block "path=$R\os_composite.img" `
     --serial "hardware=serial,num=1,type=file,path=$R\early.log,earlycon=true" `
     --serial "hardware=virtio-console,num=1,type=file,path=$R\kernel.log,console=true" `
     --serial "hardware=virtio-console,num=2,type=namedpipe,path=\\.\pipe\agentemu-console" `
     --serial "hardware=virtio-console,num=3,type=file,path=$R\logcat.log" `
     @s `
     --gpu "backend=2D,displays=[[mode=windowed[720,1280],dpi=[320,320],refresh-rate=60]]" `
     --initrd "$R\initrd.img" `
     --params "printk.devkmsg=on audit=1 panic=-1 8250.nr_uarts=1 binder.impl=rust cma=0 firmware_class.path=/vendor/etc/ loop.max_part=7 init=/init bootconfig" `
     "$R\kernel"
   ```
   `num=N` maps to `hvc(N-1)` (crosvm `arch/src/serial.rs` L212-214). The COM1 earlycon plus a virtio-console `console=true` pair is a combination crosvm tests (`get_serial_cmdline_virtio_console_serial_earlycon`). `namedpipe` is the Windows name of the system serial type (`devices/src/serial_device.rs` L86-88) and virtio-console implements it (`devices/device_virtio_console/src/sys/windows.rs` L47). The GPU still opens a desktop window; a headless display is fork work (ticket 01). Omit `AGENT_EMU_NO_NET` when you need adb (step 8).
7. **Console-only boot check** (no adb needed):
   - Passive: `Select-String -Path $R\kernel.log -Pattern 'processing action \(sys\.boot_completed=1'`. init logs every action it runs to kmsg with no condition (`init/action_manager.cpp` L88-91, r4), and `printk.devkmsg=on` turns off the rate limit.
   - Active, over the hvc1 pipe:
     ```python
     import time; p = open(r"\\.\pipe\agentemu-console", "r+b", buffering=0)
     p.write(b"getprop sys.boot_completed; getprop service.adb.listen_addrs\n"); time.sleep(1); print(p.read(4096).decode(errors="replace"))
     ```
8. **adb** (fork patch, **unverified**). Windows has no host-to-guest vsock (crosvm `device_virtio_vsock/src/sys/windows/vsock.rs` TODOs) and Windows adb cannot dial vsock (`adb/socket_spec.cpp`), so the route is slirp TCP:
   - Patch `net_util/src/slirp/sys/windows/handler.rs`: after `Context::new(...)` (L718), call `libslirp_sys::slirp_add_hostfwd(slirp, 0, 127.0.0.1, 6520, 10.0.2.15, 5555)`. The FFI exists in libslirp-sys 4.2.1 (`src/lib.rs` L188); the `Context` wrapper does not expose it, so add a small `add_hostfwd` method.
   - Guest (hvc1): `ip addr add 10.0.2.15/24 dev buried_eth0; ip link set buried_eth0 up; ip rule add from 10.0.2.15 lookup main pref 100`. If `service.adb.listen_addrs` is not empty, also `setprop service.adb.listen_addrs tcp:5555; stop adbd; start adbd`. Cuttlefish sets it to vsock only when `RELEASE_ADBD_OPEN_VSOCK_PORT` is set (`shared/device.mk` L52-54, r4); `persist.adb.tcp.port=5555` is in the image.
   - Host: `adb connect 127.0.0.1:6520`, then `adb -s 127.0.0.1:6520 shell getprop sys.boot_completed`. Expect `1`. The build is userdebug; if adb says `unauthorized`, write the host's `adbkey.pub` to `/data/misc/adb/adb_keys` from the console.

Minimal device set (the run today plus the three keys): kernel + initrd, one composite block disk, COM1, 20 virtio-consoles (hvc0 kernel log, hvc1 shell, hvc2 logcat, the rest sinks), `--gpu backend=2D` with one display (vendor init waits for `/dev/dri/card0`), and slirp net only when adb is wanted. Not needed: persistent_composite, pflash, `--bios`, vsock, input, pmem, pstore, audio. If the light HAL still aborts with the port set, add `--vsock cid=3` and re-check.

### Disagreements and verdicts (8)

1. **Can an exact command be given?** A gave one. B said no exact command is possible yet. **A wins.** The run today using A's command reached system_server, and the remaining stall is the three missing bootconfig keys, not the command shape.
2. **Ramdisk order.** A: generic (init_boot) ramdisk first, then vendor. B: vendor first, generic last. **B wins.** U-Boot `boot/image-android.c` copies the vendor ramdisk, then the boot ramdisk. AOSP says the generic ramdisk loads last. Cuttlefish's Gem5 path writes boot ramdisk first, which is where A's order came from. Measured: the vendor ramdisk carries a recovery `/init` symlink that, with A's order, replaces the real first-stage `/init`. A's order still got through first stage today, but B's order is the spec.
3. **`androidboot.boot_devices`.** A: crosvm on Windows ignores `pci-address=` for disks, so list the auto slots from `00:01.0` up. B: `00:13.0,00:14.0` as Cuttlefish sets them. **A wins.** Windows turns every disk into a vhost-user block device with no address, sorted first (`src/sys/windows.rs` L432-445, L587-601), and bus 0 hands out addresses from `00:01.0` (`resources/src/system_allocator.rs` L278-293). Measured: the over-listed `01..08` value found `/dev/block/by-name/*` today.
4. **persistent_composite disk.** A: drop it under direct boot. B: keep it for the first reproduction. **A wins.** Only U-Boot reads it (uboot_env and bootconfig), and the run today reached system_server without it.
5. **Composite disk on Windows.** A: rebuild with `composite-disk`. B: unverified, prefer a flat raw GPT image. **A wins.** Measured: the build with `--features all-msvc64,whpx,composite-disk` works, and `create_composite` made the os_composite the run booted from.
6. **`verifiedbootstate=orange`.** A: it makes AVB permissive, so a missing `vbmeta.digest` is tolerated. B: it is not a universal "verity off" switch. **A wins for this setup.** `IsDeviceUnlocked()` is just `verifiedbootstate == "orange"` (`fs_mgr/libfs_avb/util.cpp` L110-115). `IsAvbPermissive()` returns true unless `/metadata/avb_enforce` exists (`fs_avb.cpp` L280-290), and a digest error is then allowed (L503-509). B's caveat applies only to that file, which a blank metadata partition never has.
7. **How to append bootconfig.** A: Python writing data, `le32 size`, `le32 checksum` and `#BOOTCONFIG\n`. B: the Linux `bootconfig -a` tool. **A wins.** The bytes are the same as U-Boot's `add_trailer` (`image-android.c` L36-58), and the Linux tool does not exist on Windows.
8. **adb route.** A: patch slirp to forward host 127.0.0.1:6520 to guest 10.0.2.15:5555 and set the guest IP by hand. B: undecided between a virtio-net route and host-initiated vsock. **A wins.** Host-initiated vsock is a TODO on Windows, Windows adb cannot dial vsock, and libslirp already exports `slirp_add_hostfwd`. Not yet run.

Both passes missed six things, fixed above:
- The composite order is alphabetical in Cuttlefish. A's list was not.
- `openthread_node_id`, `vsock_lights_port/cid` and `vsock_tombstone_port`. Measured: the threadnetwork and light HALs crash without them, and the light crash stalls system_server.
- `hwcomposer.display_framebuffer_format=rgba`.
- The vendor cmdline already ends in `bootconfig`, so do not append it again.
- hctr2 and BOOT_CONFIG are confirmed from the kernel's own ikconfig. They are no longer an open question.
- `prep-stage1.py` puts the ramdisks in the wrong order.

Still unverified: boot reaching `sys.boot_completed=1` with the three added keys, and the adb slirp patch.

Sources (beyond the two passes): android-cuttlefish@182ab18 `base/cvd/cuttlefish/host/commands/assemble_cvd/{bootconfig_args.cpp,boot_image_utils.cc,flags.cc,guest_config.cc,disk/android_composite_disk_config.cc}`, `libs/vm_manager/{crosvm_manager.cpp,vm_manager.cpp}`; crosvm@1ca5899 `src/sys/windows.rs`, `resources/src/system_allocator.rs`, `arch/src/serial.rs`, `devices/src/serial_device.rs`, `src/main.rs` L348-378, `net_util/src/slirp/sys/windows/handler.rs`; https://github.com/u-boot/u-boot/blob/master/boot/image-android.c ; https://source.android.com/docs/core/architecture/partitions/vendor-boot-partitions ; https://android.googlesource.com/platform/system/core/+/refs/tags/android-16.0.0_r4/fs_mgr/libfs_avb/ (fs_avb.cpp, util.cpp) and `init/action_manager.cpp`; https://android.googlesource.com/device/google/cuttlefish/+/refs/tags/android-16.0.0_r4/shared/device.mk and `guest/hals/light/lights.rs`; run logs `C:\dev\agent-emu-work\run\{kernel.log,logcat.log,boot-args.txt}`.
