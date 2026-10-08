#!/bin/bash
# WSL2 kernel 6.18.33.2 (the running one) + binder/binderfs for redroid. KSM already on.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
L=/mnt/c/dev/agent-emu-work/redroid/build-wslk.log
exec >>"$L" 2>&1
echo "== start $(date -Is)"
apt-get update -q && apt-get install -y -q flex bison libelf-dev libssl-dev dwarves cpio bc python3 docker.io adb android-sdk-libsparse-utils
cd /root
[ -d wslk ] || git clone --depth 1 -b linux-msft-wsl-6.18.33.2 https://github.com/microsoft/WSL2-Linux-Kernel wslk
cd wslk
zcat /proc/config.gz > .config
scripts/config -e ANDROID_BINDER_IPC -e ANDROID_BINDERFS --set-str ANDROID_BINDER_DEVICES "" -e KSM -e MEMFD_CREATE -e PSI -e CHECKPOINT_RESTORE
scripts/config --set-str LOCALVERSION "-redroid"
make olddefconfig
grep -E "BINDER|CONFIG_KSM|LOCALVERSION=" .config
echo "== build $(date -Is)"
make -j6 bzImage
cp arch/x86/boot/bzImage /mnt/c/dev/agent-emu-work/redroid/bzImage-binder
echo "== EXIT 0 $(date -Is)"
