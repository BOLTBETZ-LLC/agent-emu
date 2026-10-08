agent-emu slim low-RAM repack of aosp_cf_x86_64_only_phone-userdebug 15581820 (API 36)
Built 2026-10-07 in WSL Ubuntu (/root/slim), from target_files + otatools of the same build. No AOSP build.

OUTPUTS
  super.img                 raw (non-sparse) super, 8589934592 bytes (8 GiB, same size/layout as stock)
  vbmeta.img                regenerated top-level vbmeta (chains vbmeta_system with the new descriptors)
  vbmeta_system.img         regenerated (system = hash descriptor, product/system_ext = hashtree)
  vbmeta_system_dlkm.img    unchanged copy from target_files
  vbmeta_vendor_dlkm.img    unchanged copy from target_files
  removed-apks.txt          package, dir, size of every APK dir removed

INPUTS
  images/15581820/aosp_cf_x86_64_only_phone-target_files-15581820.zip (already joined)
  images/15581820/otatools.zip  -- was NOT downloaded before; fetched now (502530087 bytes,
    15 x 32 MiB parts in otatools.zip.parts/). The Build API artifact list is paginated;
    otatools.zip is on page 2, which is why the first fetch missed it.

EDITS
1. tf/VENDOR/build.prop (becomes /vendor/build.prop in vendor.img; verified inside the image with dump.erofs --cat)
     dalvik.vm.heapgrowthlimit 192m -> 128m
     dalvik.vm.heapsize        512m -> 256m
     added: ro.config.low_ram=true, ro.lmk.critical_upgrade=true, ro.lmk.upgrade_pressure=40,
            ro.lmk.downgrade_pressure=60, ro.lmk.kill_heaviest_task=false
     debug.hwui.drawing_enabled is not set in any *.prop (checked); left unset.
     No other partition's build.prop sets any of these keys (checked).
2. tf/META/misc_info.txt
     erofs_default_compressor=lz4hc,9 -> none
     avb_system_hashtree_enable=true  -> false
     erofs_sparse_flag=-s             -> removed
     erofs_share_dup_blocks=true      -> added (mkfs.erofs gets --chunksize 4096)
     avb_vbmeta_system: unchanged in the end (product system system_ext)
   Note: compressor=none applies to every erofs partition rebuilt (system, system_ext, product, vendor).
3. tf/IMAGES/system.img: avbtool add_hash_footer (sha256, --dynamic_partition_size, same os_version/
   fingerprint/security_patch props as stock). Needed because add_img_to_target_files builds
   vbmeta_system with --include_descriptors_from_image system.img, and with the hashtree off the image
   had no AVB footer, so avbtool failed: "Given image does not look like a vbmeta image."
   Result: no dm-verity hashtree for system; a whole-image hash descriptor in vbmeta_system instead.
   The footer sits after the erofs data (image grew 935907328 -> 935976960 bytes); erofs ignores it.
   First tried dropping system from avb_vbmeta_system: it then lands in the top-level vbmeta and fails
   the same way, so that was reverted.
   fstab avb=/avb_keys= removal (spec 8) lives in vendor_boot/the ramdisk and was NOT done here.

REMOVED APK DIRS (33), from trim.txt via aapt2 dump packagename
  SYSTEM/app:        Traceur PrintSpooler WallpaperBackup EasterEgg HTMLViewer
  SYSTEM/priv-app:   DeviceDiagnostics CellBroadcastLegacyApp ManagedProvisioning
                     DynamicSystemInstallationService DocumentsUI LiveWallpapersPicker
                     BuiltInPrintService MusicFX VpnDialogs CallLogBackup BackupRestoreConfirmation
                     SharedStorageBackup SoundPicker MtpService
  SYSTEM_EXT/priv-app: EmergencyInfo StorageManager WallpaperCropper
  PRODUCT/app:       messaging Calendar Camera2 QuickSearchBox DeskClock Gallery2 Music
  PRODUCT/priv-app:  Contacts ImsServiceEntitlement StatementService Dialer
KEPT although in trim.txt
  com.android.settings (Settings, protected), com.android.providers.calendar (CalendarProvider, core provider)
NOT REMOVABLE AT APK LEVEL (inside APEX, or not in this image)
  cellbroadcastreceiver.module, devicelockcontroller, email (absent), adservices.api, rkpdapp,
  google.gce.gceservice (absent from system/system_ext/product), ondevicepersonalization.services,
  federatedcompute.services, healthconnect.controller, safetycenter.resources

COMMANDS (WSL root, PATH=/root/slim/ota/bin:$PATH LD_LIBRARY_PATH=/root/slim/ota/lib64)
  unzip otatools.zip -d /root/slim/ota ; unzip target_files.zip -d /root/slim/tf
  aapt2 dump packagename <apk>   (map trim.txt -> dirs) ; rm -rf <dirs>
  rm IMAGES/{system,system_ext,product,vendor}.{img,map} IMAGES/vbmeta.img IMAGES/vbmeta_system.img
  add_img_to_target_files -a -v /root/slim/tf      (failed at vbmeta_system, see edit 3)
  avbtool add_hash_footer --image IMAGES/system.img --partition_name system --dynamic_partition_size --hash_algorithm sha256 --prop ...
  add_img_to_target_files -a -v /root/slim/tf      (OK)
  build_super_image /root/slim/tf /root/slim/super.img   (output was sparse, magic ed26ff3a)
  simg2img super.img super.raw.img && mv super.raw.img super.img   (raw; LP geometry magic "gDla" at 4096)
  lpunpack -p system_a / -p vendor_a super.img -> cmp exit 0 against IMAGES/system.img / vendor.img (slots are _a-suffixed)
  sha256 super.img = b173d458f6a12b5eb2c3ff900bdc39bc86f97b409eeaeecdde19442e93730974 (WSL copy and C: copy match)

EVIDENCE: system.img erofs (dump.erofs -s, Ubuntu erofs-utils)
  Filesystem incompatible features: chunked_file      (no lz4_0padding, no compr_cfgs)
  superblock feature_incompat @0x450 = 0x00000004 (CHUNKED_FILE only), lz4_max_distance 0
  fsck.erofs (otatools) exit 0

SIZES (IMAGES/)
  system.img 935976960 (incl. AVB footer), system_ext.img 323203072, product.img 317648896, vendor.img 214028288

NOT VERIFIED
  Booting this super.img on crosvm; the stock vbmeta.img in run/ will not match it, use this vbmeta.img.
  RAM floor at 512-768 MB is not measured.
