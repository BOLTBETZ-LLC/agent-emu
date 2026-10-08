#!/bin/bash
# Stage 2: GKI android16-6.12 + virtual_device_x86_64 with DAX support (ZONE_DEVICE, FS_DAX, VMGENID).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
L=/mnt/c/dev/agent-emu-work/logs/kernel-build.log
exec >>"$L" 2>&1
echo "== start $(date -Is) nproc=$(nproc) mem=$(free -g | awk '/Mem/{print $2}')G"
apt-get update -q && apt-get install -y -q git curl python3 python-is-python3 build-essential rsync zip unzip
mkdir -p /root/bin /root/kernel
[ -x /root/bin/repo ] || curl -s https://storage.googleapis.com/git-repo-downloads/repo -o /root/bin/repo && chmod +x /root/bin/repo
export PATH=/root/bin:$PATH
git config --global user.name "Aaron Lilla"; git config --global user.email "aaronlillaclaude@gmail.com"; git config --global color.ui false
cd /root/kernel
[ -d .repo ] || repo init -u https://android.googlesource.com/kernel/manifest -b common-android16-6.12 --depth=1 </dev/null
[ -f /root/kernel/.synced ] || repo sync -c -j8 --no-tags --no-clone-bundle --optimized-fetch && touch /root/kernel/.synced
cat > common/agent_emu_dax.fragment <<'FRAG'
CONFIG_ZONE_DEVICE=y
CONFIG_FS_DAX=y
CONFIG_VIRT_DRIVERS=y
CONFIG_VMGENID=y
FRAG
echo "== build $(date -Is)"
tools/bazel run --defconfig_fragment=//common:agent_emu_dax.fragment //common-modules/virtual-device:virtual_device_x86_64_dist -- --destdir=/root/kernel/out-dax
ls -la /root/kernel/out-dax | head -40
echo "== EXIT 0 $(date -Is)"
