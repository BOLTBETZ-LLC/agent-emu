#!/bin/bash
printf "xt_addrtype\nxt_conntrack\nxt_MASQUERADE\nnft_compat\nbr_netfilter\nbridge\niptable_nat\nashmem_linux\n" > /etc/modules-load.d/redroid.conf
echo 'KERNEL=="ashmem", MODE="0666"' > /etc/udev/rules.d/99-ashmem.rules
mkdir -p /etc/systemd/system/containerd.service.d
printf '[Service]\nExecStart=\nExecStart=/usr/local/bin/ksmwrap /usr/bin/containerd\n' > /etc/systemd/system/containerd.service.d/ksm.conf
[ -s /usr/local/bin/ksmwrap ] || gcc -O2 -o /usr/local/bin/ksmwrap /mnt/c/dev/agent-emu-work/redroid/ksmwrap.c
sync
