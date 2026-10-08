#!/bin/bash
set -e
cd /root; rm -rf redroid-modules
git clone -q --depth 1 -b fix/modern-kernel-6.17-compat https://github.com/skunpoj/redroid-modules
cd redroid-modules/ashmem
make -C /root/wslk M=$PWD modules > /tmp/am.log 2>&1 || { tail /tmp/am.log; exit 1; }
mkdir -p /lib/modules/$(uname -r)/extra && cp ashmem_linux.ko /lib/modules/$(uname -r)/extra/ && depmod -a
sync
echo built $(md5sum /lib/modules/$(uname -r)/extra/ashmem_linux.ko)
