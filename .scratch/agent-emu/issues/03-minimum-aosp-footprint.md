# Minimum AOSP API 36 footprint

Type: research
Status: resolved
Blocked by:

## Question

What is the smallest known RAM footprint of an AOSP API 36 x86_64 system that can still install and run a third-party React Native app? Answer these:

- What does each part cost: kernel, init and native daemons, zygote and boot image, system_server, SystemUI, launcher?
- Which known configurations save RAM, and by how much: Android Go / `ro.config.low_ram`, Microdroid, Cuttlefish slim targets, ATD images, a headless build with no SystemUI?
- Which services can be removed while a normal APK still installs and launches?
- Any published memory numbers for these configurations, with citations.

## Answer

Reconciled 2026-10-07 from two blind passes. Primary sources for disputed points re-fetched on that date.

- **No published measurement exists** of an API 36 x86_64 system installing and running a third-party APK. Both passes agree. agent-emu must measure it.
- **Best starting point: Cuttlefish `aosp_cf_x86_64_slim`.** It keeps zygote64, system_server and the package manager, and drops SystemUI, Launcher3, Settings and all bundled apps. Default guest RAM 2048 MB (configured, not used). Source: [config_slim.json r1](https://android.googlesource.com/device/google/cuttlefish/+/refs/tags/android-16.0.0_r1/shared/slim/config_slim.json); the product list is the same at r1 and `android16-qpr2-release` (re-checked).
- **Slim and ATD do not draw by default.** Slim sets `debug.hwui.drawing_enabled=0` ([device_vendor.mk r1](https://android.googlesource.com/device/google/cuttlefish/+/refs/tags/android-16.0.0_r1/shared/slim/device_vendor.mk), line 50). agent-emu must turn drawing back on for screenshots.
- **No home app needed.** `FakeSystemApp` supplies `FallbackHome` and `EmptyHomeActivity` (HOME category) ([manifest, goldfish security r9](https://android.googlesource.com/device/generic/goldfish/+/refs/tags/android-security-16.0.0_r9/slim/FakeSystemApp/AndroidManifest.xml), lines 45-68). Apps launch with `am start`.
- **SystemUI is safe to drop.** Removed by ATD and slim; `SystemServer` catches a failed SystemUI start with `reportWtf` (Pass A).
- **Microdroid and minidroid cannot run an APK** (no zygote, system_server or Java framework; [Microdroid doc](https://source.android.com/docs/core/virtualization/microdroid), 2026-06-17). They only bound the native floor: Microdroid boots in **35 MiB RSS** (non-protected) / 42 MiB (protected), measured ([Virtualization bbbf926c0](https://android.googlesource.com/platform/packages/modules/Virtualization/+/bbbf926c0), 2025-09-09; device `hwrev_a0`, arch unverified).
- **One published RAM saving:** dropping 32-bit support saves "up to 150MB" (Pixel 7, ARM, [Google blog 2022-10](https://android-developers.googleblog.com/2022/10/64-bit-only-devices.html)). Slim is already 64-bit-only, so do not subtract it again.
- **Go / `ro.config.low_ram`:** sets heap limits (128m/256m), lmkd policy and `speed-profile` for system_server ([go_defaults_common.prop](https://android.googlesource.com/platform/build/+/refs/heads/main/target/board/go_defaults_common.prop)). No published MB saving. No x86_64 Go Cuttlefish target exists (only `aosp_cf_x86_go_phone`, re-checked). Risk: the RN app may change behavior under `isLowRamDevice()` (unverified).
- **CDD floor:** 816 MB kernel+userspace for a 64-bit handheld at qHD ([Android 16 CDD 7.6.1](https://source.android.com/docs/compatibility/16/android-16-cdd)). A compliance minimum, not a measured working set.
- **ATD images for API 36 exist:** `system-images;android-36;aosp_atd;x86_64` is in Google's SDK repo ([sys-img2-4.xml](https://dl.google.com/android/repository/sys-img/aosp_atd/sys-img2-4.xml), fetched 2026-10-07), and this machine has `android-36/google_atd` x86_64 installed. No published RAM delta for ATD.
- **Local data point (not a research source):** `C:/dev/CLAUDE.md` records a warm API 36 `google_atd` stream (2 GB guest, goldfish emulator) at about 1.6 GB host RAM, measured 2026-10-06. That is the current baseline to beat, with GMS-style Google APIs present.
- **Cross-Device sharing:** `boot.art` heap is an anonymous, mostly dirty mapping (per-VM only). Boot `.jar/.oat/.vdex` and `.so` are file-backed, mostly clean, and could be shared across VMs only if the guest maps them by DAX ([ART MEMORY_MAPPING_GUIDE 4e35a8f689](https://android.googlesource.com/platform/art/+/4e35a8f689/MEMORY_MAPPING_GUIDE.md), 2026-03-16). DAX on WHPX is unverified and belongs to another ticket.
- **Estimate (Pass A, not measured):** headless slim with RN app in front is about 450-650 MB RSS per VM; about 300-450 MB unique if 150-250 MB of framework code is DAX-shared. **400 MB unique is plausible, unproven.**
- **How to settle it:** boot slim with drawing on and low_ram on, install the APK, `am start`, capture `dumpsys meminfo`, `showmap` for zygote64/system_server, guest `/proc/meminfo` and host VMM RSS; step guest RAM 2048 to 512 MB until the first screen fails (Pass A method, same as Microdroid's `testMinimumRequiredRAM`).

### Component table

All MB values are estimates unless marked [S] (sourced measurement).

| Part | Typical MB | Minimal MB (headless, low_ram) | Shareable across Devices | Source |
|---|---|---|---|---|
| Kernel (text, slab, page tables) | 40-80 (est.) | 20-35 (est.) | No | Bounded by Microdroid 35 MiB RSS for kernel+init+native [S] ([bbbf926c0](https://android.googlesource.com/platform/packages/modules/Virtualization/+/bbbf926c0)) |
| init + native daemons | 50-100 (est.) | 25-50 (est.) | Code yes, with DAX; heaps no | Same bound; daemon list in [base_system.mk r1](https://android.googlesource.com/platform/build/+/refs/tags/android-16.0.0_r1/target/product/base_system.mk) |
| SurfaceFlinger + graphics HAL | 30-80 (est.) | 20-40 (est.) | Code yes, with DAX | No source measures it |
| zygote64 + boot image | 60-120 unique + 150-250 file-backed (est.) | 40-70 unique (est.) | `boot.art` no; boot `.jar/.oat/.vdex` yes, with DAX | ART guide: `.Boot art` Pss 3,031 kB / Rss 33,732 kB "Mostly dirty!" [S, example from system_server, device unnamed] |
| system_server | Pss 391 / Rss 751 / private dirty 191 [S, example] | 120-200 (est.) | Private dirty no; code yes, with DAX | ART guide `dumpsys meminfo -d system_server` example, not API 36 x86_64 |
| SystemUI | 100-300 (est.) | 0 (removed) | n/a | Removed in ATD and slim |
| Launcher3 | 50-150 (est.) | 0 (FakeSystemApp HOME stub, small, unmeasured) | n/a | ATD list; FakeSystemApp manifest |
| Bundled apps | 100-300 total (est.) | 0 (removed) | n/a | slim uses `media_product` (WebView only) |
| RN app (budget only) | 100-200 (est.) | 80-150 (est.) | Code yes, with DAX | Not measured |

### Disagreements

1. **ATD on API 36.** A: doc says ATD supports only API 30; API 36 unverified. B: that line is a stale code comment. **B wins.** The doc line is a comment in a Gradle sample (`// ATDs currently support only API level 30.`, page updated 2026-01-16); Google's SDK repo lists `aosp_atd` and `google_atd` for API 30-36, and an API 36 `google_atd` x86_64 image is installed here.
2. **Per-part MB numbers.** A gives estimates plus two sourced figures; B says every per-part number is unverified. **Both right, A more useful.** A's sourced figures check out (Microdroid 35/42 MiB in the bbbf926c0 commit message; system_server Pss 391,182 kB / Rss 750,724 kB at ART guide line 296). A labels everything else as estimate. **Unresolved** for real API 36 numbers: settled only by the measurement above.
3. **Boot image sharing.** A: `boot.art` is anon dirty, so per-VM; boot `.oat/.vdex/.jar` shareable across VMs with DAX. B: boot `.oat` and `.art` are shared among processes; cross-Device sharing unverified. **A wins on the split.** The ART guide shows `[anon:dalvik-/system/framework/boot.art]` (line 60), "shared across apps from the zygote" (line 140), `.Boot art` "Mostly dirty!" (line 316), `.Boot vdex` "Clean!" (line 312). B is right that within-VM sharing says nothing about cross-VM. Whether WHPX can do DAX is **unresolved** (settle with a virtio-pmem DAX test on WHPX).
4. **Home without a launcher.** A: device boots to an empty screen, fallback behavior unverified. B: FakeSystemApp supplies HOME activities. **B wins.** The manifest declares `FallbackHome` and `EmptyHomeActivity` with `category.HOME` (lines 45-68).
5. **Is 400 MB unique reachable?** A: plausible but unproven (estimate). B: no defensible sum, unverified. **Unresolved.** Both agree it is unproven; settle by measuring slim with drawing on, then with DAX sharing.

### Context

- Pass A (Claude): `C:/dev/worktrees/agent-emu--research-aosp-footprint/.scratch/agent-emu/research/03-minimum-aosp-footprint.md`, branch `research/aosp-footprint`.
- Pass B (GPT-6.1 Sol): `C:/dev/worktrees/agent-emu--codex-minimum-aosp-footprint/.scratch/agent-emu/research/03-minimum-aosp-footprint.codex.md`, branch `research/codex-minimum-aosp-footprint`, not yet committed.
- Only-in-A: Microdroid 35 MiB, ART guide numbers, Go 2 GB minimum for Android 13, no x86_64 Go target, slim drops Virtualization APEX, DAX/virtio-pmem path. Only-in-B: Pixel 7 150 MB 64-bit saving, goldfish slim + FakeSystemApp HOME stubs, platform R8 lead, feature-gated system_server services list (backup, print, MIDI, HDMI-CEC, TV).
