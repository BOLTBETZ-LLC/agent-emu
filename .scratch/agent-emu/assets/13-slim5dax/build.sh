#!/bin/bash
# slim5dax = slim5 (tf5) + the clone-track DAX layout, ported to the live phone image:
#   appdax (app base.apk + oat on pmem1, stage2/appdax), every /system/apex/*.capex shipped as .apex (slim4dax2),
#   vendor init: zram zstd, vm.watermark_scale_factor 10, vm.min_free_kbytes 2048 (slim4dax3).
# system_ext/product/vendor go on pmem2/3/4 through the first-stage fstab in slim4dax2's initrd-dax-pmem2.img.
# Run: wsl -d Ubuntu -u root bash /mnt/c/dev/agent-emu-work/stage2/slim5dax/build.sh   (log: stage2/slim5dax/build.log)
set -uo pipefail
export PATH=/root/slim/ota/bin:$PATH LD_LIBRARY_PATH=/root/slim/ota/lib64
S=/mnt/c/dev/agent-emu-work/stage2; T=/root/slim/tf5dax; OUT=$S/slim5dax; L=$OUT/build.log
exec >>"$L" 2>&1
echo "== start $(date -Is)"
set -e
rm -rf $T && cp -a /root/slim/tf5 $T
bash $S/appdax/add-to-tree.sh $T
cd $T
for c in SYSTEM/apex/*.capex; do
  a=${c%.capex}.apex; unzip -p $c original_apex > $a; [ -s $a ] || { echo "no original_apex in $c"; exit 1; }; rm $c
  n=${c#SYSTEM/}; sed -i "s#^system/${n} #system/${n%.capex}.apex #" META/filesystem_config.txt
  grep -q "^system/${n%.capex}.apex " META/filesystem_config.txt || echo "system/${n%.capex}.apex 0 0 644 capabilities=0x0" >> META/filesystem_config.txt
  echo "EDIT: $c -> $a"
done
R=VENDOR/etc/init/init.cutf_cvm.rc
sed -i 's#^    write /sys/block/zram0/comp_algorithm lz4$#    write /sys/block/zram0/comp_algorithm zstd#' $R
grep -q 'comp_algorithm zstd' $R
python3 - $R <<'PY'
import sys; p = sys.argv[1]; s = open(p).read()
old = "on post-fs-data\n    swapon_all\n"
assert old in s, "post-fs-data swapon_all not found"
s = s.replace(old, "on post-fs-data\n    # agent-emu slim5dax (= slim4dax3): low swap churn\n    write /proc/sys/vm/watermark_scale_factor 10\n    write /proc/sys/vm/min_free_kbytes 2048\n    swapon_all\n")
s += """
# agent-emu slim5dax: extra_free_kbytes.sh rewrites watermark_scale_factor after ActivityManager sets the prop. Put 10 back.
on property:sys.sysctl.extra_free_kbytes=*
    exec_background -- /system/bin/sh -c "sleep 3; echo 10 > /proc/sys/vm/watermark_scale_factor"

on property:sys.boot_completed=1
    write /proc/sys/vm/watermark_scale_factor 10
"""
open(p, "w").write(s)
PY
set +e
rm -f IMAGES/{system,system_ext,product,vendor}.{img,map} IMAGES/vbmeta.img IMAGES/vbmeta_system.img
add_img_to_target_files -a -v $T > /root/slim/add_img_slim5dax.log 2>&1 || echo "first add_img failed (expected at vbmeta_system): $(tail -2 /root/slim/add_img_slim5dax.log)"
if [ ! -s IMAGES/vbmeta_system.img ]; then
  ARGS=$(grep '^avb_system_add_hashtree_footer_args=' META/misc_info.txt | cut -d= -f2-)
  avbtool add_hash_footer --image IMAGES/system.img --partition_name system --dynamic_partition_size $ARGS
  rm -f IMAGES/vbmeta.img IMAGES/vbmeta_system.img
  add_img_to_target_files -a -v $T > /root/slim/add_img_slim5daxb.log 2>&1 || { echo "add_img FAILED"; tail -20 /root/slim/add_img_slim5daxb.log; exit 1; }
fi
build_super_image $T /root/slim/super_slim5dax.img > /root/slim/super_slim5dax.log 2>&1 || { echo "super FAILED"; tail /root/slim/super_slim5dax.log; exit 1; }
simg2img /root/slim/super_slim5dax.img /root/slim/super_slim5dax.raw && mv /root/slim/super_slim5dax.raw /root/slim/super_slim5dax.img
cp /root/slim/super_slim5dax.img $OUT/super.img
cp IMAGES/vbmeta.img IMAGES/vbmeta_system.img IMAGES/vbmeta_system_dlkm.img IMAGES/vbmeta_vendor_dlkm.img $OUT/
for p in system system_ext product vendor; do cp IMAGES/$p.img $OUT/$p-pmem.img; Z=$(stat -c %s $OUT/$p-pmem.img); truncate -s $(( (Z + 2097151) / 2097152 * 2097152 )) $OUT/$p-pmem.img; done
grep -n 'watermark_scale_factor\|zstd' $R; ls -la $OUT; sha256sum $OUT/super.img $OUT/*-pmem.img
echo "== EXIT 0 $(date -Is)"
