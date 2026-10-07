# crosvm Windows boot recipe

Type: research
Status: claimed
Blocked by: 12, 13

## Question

What exact crosvm (Windows, WHPX) command line boots the Cuttlefish `aosp_cf_x86_64_only_phone` API 36 prebuilt 15581820 headless, using `aosp_cf_x86_64_only_phone-img-15581820.zip`? Answer these:

- Which args does Cuttlefish's `launch_cvd`/`assemble_cvd` pass to crosvm for x86_64 (crosvm_manager.cpp in google/android-cuttlefish)? List each disk (`os_composite`/super, vbmeta, misc, metadata, userdata), the kernel or bootloader, bootconfig, the cmdline, gpu, vsock, input and console. Can direct kernel boot replace the bootloader?
- Which of those args and devices exist in crosvm on Windows: composite disk, `--gpu`, vsock over named pipes, input event sources?
- How do you get `bzImage` and `initramfs` out of boot.img and vendor_boot.img (otatools `unpack_bootimg`), and which androidboot.* params must go on the kernel cmdline or into bootconfig?
- What's the minimal device set to reach `sys.boot_completed=1` with adb reachable from the host?
- Give a step-by-step recipe, critical path only.
