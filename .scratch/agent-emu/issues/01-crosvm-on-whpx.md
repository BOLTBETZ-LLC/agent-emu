# crosvm on Windows hypervisor

Type: research
Status: resolved
Blocked by:

## Question

Can the open-source crosvm build and run on Windows 11 with WHPX today and boot an Android x86_64 guest (API 36, Cuttlefish-style or similar)? Answer these:

- What is the state of crosvm's public Windows support: build docs, CI, last commits, and which features are missing on Windows (virtio-gpu, gfxstream, vsock, balloon, snapshot)?
- Does Google Play Games for PC's `crosvm.exe` come from public source, or from a private fork?
- If crosvm on Windows is not viable, which other Rust VMMs run on WHPX (libkrun, Cloud Hypervisor, Hyperlight, others), and what are their gaps?

The route lock depends on this answer.

## Answer

Reconciled 2026-10-07. Source anchor: crosvm `1ca5899813e21674d63b7888252a2ffd3c5f2c93` (2026-10-07), https://github.com/google/crosvm/tree/1ca5899813e21674d63b7888252a2ffd3c5f2c93

- Public crosvm has a real Windows/WHPX VMM, including the launch path and broker. Build recipe: `cargo build --features all-msvc64,whpx` (`docs/book/src/building_crosvm/windows.md`). Nobody built it in this ticket.
- Upstream does not test WHPX: `hypervisor/README.md` says "Tested upstream: no" (checked by reconciler at 1ca5899813).
- CI: the native Windows builder has been commented out since commit `bf3cc85d8b` (2025-03-13, b/396467061). Only the Linux-hosted mingw64 clippy and unit tests under wine64 are left. Windows e2e tests are excluded. No CI job boots a guest (`infra/config/main.star`, `tools/presubmit`, `tools/impl/test_config.py`).
- Windows code is maintained but gets no new features. Newest `src/sys/windows.rs` commit is 2026-09-04, "vm_control: introduce DeviceControlRequest" (checked by reconciler via `gh api`). Recent commits are cross-platform refactors (Pass A inference from titles).
- virtio-gpu is present, but its display is hard-wired to `vec![virtio::DisplayBackend::WinApi]`, with no headless backend (`devices/src/virtio/vhost_user_backend/gpu/sys/windows.rs` ~L293, checked by reconciler).
- gfxstream is not in `all-msvc64`. rutabaga_gfx links an external `gfxstream_backend` that you build yourself (Cargo.toml; rutabaga_gfx 0.1.80 `build.rs`). Whether that builds and runs on Windows is unverified.
- vsock is present over named pipes, not AF_VSOCK (`devices/device_virtio_vsock/src/sys/windows/vsock.rs`). Balloon is present, and WHPX inflate calls `OfferVirtualMemory` (`hypervisor/src/whpx/vm.rs`).
- Snapshot code exists: WHPX xsave/timekeeping state (`hypervisor/src/whpx/vcpu.rs`) and a Windows restore path. The docs call it "highly experimental" and "100% not supported" (`docs/book/src/architecture/snapshotting.md` L3-4, checked by reconciler). Fork cannot rely on it.
- Missing on Windows: virtio-fs, 9p, pmem, wl, USB (a no-op), PCI hotplug and vmm-swap (`src/sys/windows.rs` device list; Cargo.toml comments).
- Booting AOSP API 36 x86_64 on public crosvm + WHPX is unverified by both passes. No public doc, test or report exists. Cuttlefish's own guide needs KVM on a Debian host (https://source.android.com/docs/devices/cuttlefish/get-started, updated 2026-09-22).
- Google Play Games for PC runs on a private downstream of crosvm, codename "Kiwi". Upstream evidence: `is_kiwi_repo()` in `tools/impl/util.py` L180; `KiwiEmulator_*` crash names in `vendor/generic/crash_report/src/lib.rs` L38-46; `gvm` feature that "doesn't build upstream" (commit `aef7f5f5a6`, 2024-10-01); Cargo "Windows-future" features "only functional in future builds" (Cargo.toml L465-475). All checked by reconciler. What exactly the shipped binary contains is unverified.
- GPG needs Windows Hypervisor Platform (https://support.google.com/googleplay/answer/11358888). GPG docs mention crosvm by name (https://developer.android.com/games/playgames/pg-emulator, updated 2026-10-06).
- Alternatives: OpenVMM (Microsoft, Rust) runs officially on WHP, with Linux direct boot and virtio blk/net/fs/9p/pmem/vsock/console/rng. It has no virtio-gpu and no balloon (reconciler checked the full tree, not truncated). libkrun added a WHP backend in PR #858, merged 2026-09-21 (https://github.com/libkrun/libkrun/pull/858). CI there is clippy only, with no boot test. Hyperlight runs on WHP but has no guest kernel, so it cannot run Android. Cloud Hypervisor runs on KVM/MSHV only (v53.0 factory). Firecracker is Linux KVM only.
- Verdict: crosvm on WHPX works as a base to fork, not as a ready binary. Plan to own a headless display backend, the Windows gfxstream build, and a Windows CI boot test. Before the route lock, gate on a local spike: build `all-msvc64,whpx,gfxstream` and boot an x86_64 AOSP kernel+image to the console. If that fails, OpenVMM is the fallback, and it needs a GPU path added.

### Disagreements

- **Is GPG's crosvm a private fork?** Pass A says yes, a private "Kiwi" downstream. Pass B says unverified. Verdict: A wins on whether a private downstream exists. The upstream code names it (kiwi repo check, KiwiEmulator crash names, the gvm backend that "doesn't build upstream"); reconciler checked all three at source. B is right that the binary's exact contents are unverified. Settled by either: a GPG install's version strings, or Google publishing the source.
- **Headless virtio-gpu on Windows.** Pass A says none: WinApi is hard-wired. Pass B says headless capture is unverified. Verdict: A wins. The source line `let display_backends = vec![virtio::DisplayBackend::WinApi];` is the only backend chosen (reconciler checked).
- **Does OpenVMM have virtio-gpu and balloon?** Pass A says no, from a tree search. Pass B says its device list is not exhaustive, so absence is unproven. Verdict: A wins. The full recursive tree of `microsoft/openvmm` main (not truncated) has no virtio-gpu or balloon path. `vm/devices/virtio/` holds only blk, console, net, p9, pmem, rng, vsock, virtiofs and vhost-user.
- **Newest Windows commit.** Pass A gives 2026-09-04. Pass B gives 2026-08-24 as the newest it verified. Verdict: A wins. `gh api commits?path=src/sys/windows.rs` returns 2026-09-04 first.
- **GPG emulator page date.** Pass A gives 2026-06-09. Pass B gives 2026-10-06. Verdict: both are right. These are two different pages: `/games/playgames/emulator` (2026-06-09, "requires Hyper-V") and `/games/playgames/pg-emulator` (2026-10-06, mentions crosvm). Reconciler fetched both.
- **libkrun repo and date.** Pass A gives `containers/libkrun` PR #858 on 2026-09-21. Pass B gives `libkrun/libkrun` with no date. Verdict: both are right. The repo now lives at `libkrun/libkrun`, and the old path redirects. PR #858 merged 2026-09-21T14:21:20Z (checked by reconciler via `gh api`).

### Context

Pass A (Claude): `C:/dev/worktrees/agent-emu--research-crosvm-on-whpx/.scratch/agent-emu/research/01-crosvm-on-whpx.md`, branch `research/crosvm-on-whpx`. Pass B (GPT-6.1 Sol): `C:/dev/worktrees/agent-emu--codex-crosvm-on-whpx/.scratch/agent-emu/research/01-crosvm-on-whpx.codex.md`, branch `research/codex-crosvm-on-whpx` (not yet committed).
