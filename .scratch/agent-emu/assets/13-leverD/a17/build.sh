#!/bin/bash
# Build Android 17 slim5 images from /root/slim17/tf (after edits.sh). Same recipe as slim/slim2/slim3 (stage2/slim/README.txt).
set -uo pipefail
export PATH=/root/slim17/ota/bin:$PATH LD_LIBRARY_PATH=/root/slim17/ota/lib64
T=/root/slim17/tf; OUT=/mnt/c/dev/agent-emu-work/leverD/a17/out; L=$OUT/build.log
mkdir -p $OUT
exec >>"$L" 2>&1
echo "== start $(date -Is)"
cd $T && rm -f IMAGES/{system,system_ext,product,vendor}.{img,map} IMAGES/vbmeta.img IMAGES/vbmeta_system.img
add_img_to_target_files -a -v $T > /root/slim17/add_img_a17.log 2>&1 || echo "first add_img failed (expected at vbmeta_system): $(tail -2 /root/slim17/add_img_a17.log)"
if [ ! -s IMAGES/vbmeta_system.img ]; then
  ARGS=$(grep '^avb_system_add_hashtree_footer_args=' META/misc_info.txt | cut -d= -f2-)
  avbtool add_hash_footer --image IMAGES/system.img --partition_name system --dynamic_partition_size $ARGS
  rm -f IMAGES/vbmeta.img IMAGES/vbmeta_system.img
  add_img_to_target_files -a -v $T > /root/slim17/add_img_a17b.log 2>&1 || { echo "add_img FAILED"; tail -20 /root/slim17/add_img_a17b.log; exit 1; }
fi
build_super_image $T /root/slim17/super.img > /root/slim17/super.log 2>&1 || { echo "super FAILED"; tail /root/slim17/super.log; exit 1; }
simg2img /root/slim17/super.img /root/slim17/super.raw && mv /root/slim17/super.raw /root/slim17/super.img
cp /root/slim17/super.img $OUT/super.img
cp IMAGES/vbmeta.img IMAGES/vbmeta_system.img IMAGES/vbmeta_system_dlkm.img IMAGES/vbmeta_vendor_dlkm.img $OUT/
cp IMAGES/system.img $OUT/system-pmem.img
S=$(stat -c %s $OUT/system-pmem.img); truncate -s $(( (S + 2097151) / 2097152 * 2097152 )) $OUT/system-pmem.img
ls -la $OUT; sha256sum $OUT/super.img $OUT/system-pmem.img
echo "== EXIT 0 $(date -Is)"
