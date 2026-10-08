#!/bin/bash
set -euo pipefail
exec >>/mnt/c/dev/agent-emu-work/redroid/build-wslk.log 2>&1
echo "== modules $(date -Is)"
cd /root/wslk && make -j6 modules && make modules_install && depmod -a 6.18.33.2-redroid+
echo "== MODS EXIT 0 $(date -Is)"
