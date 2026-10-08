#!/bin/bash
# Android 17 (aosp-android-latest-release 16373615) slim repack, step 1: unpack otatools + target_files in WSL.
set -euo pipefail
S=/mnt/c/dev/agent-emu-work/leverD/aosp-android-latest-release-16373615; R=/root/slim17
mkdir -p $R && cd $R
[ -d ota ] || unzip -q $S/otatools.zip -d ota
rm -rf tf && unzip -q $S/aosp_cf_x86_64_only_phone-target_files-16373615.zip -d tf
ls tf; grep -E '^(erofs_|avb_system_hashtree)' tf/META/misc_info.txt
