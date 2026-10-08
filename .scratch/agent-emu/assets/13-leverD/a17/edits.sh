#!/bin/bash
# Android 17 slim5-equivalent edits in one pass on /root/slim17/tf (stock 16373615 target_files).
# = slim (misc_info + vendor props + 33 APKs) + slim2 (soft lmkd) + slim3 (no SystemUI) + slim4 (guest diet) + slim5 (HOME stub, lmkd spares TOP).
# Source recipes: stage2/slim/README.txt, issues/13 (slim2, slim3), stage2/slim4/edits.sh, stage2/slim5/edits.sh.
set -uo pipefail
cd /root/slim17/tf
log() { echo "EDIT: $*"; }
gone() { for p in "$@"; do if [ -e "$p" ]; then rm -rf "$p"; echo "  rm $p"; else echo "  MISSING $p"; fi; done; }

# slim: misc_info (uncompressed chunk erofs, no system hashtree) -------------------------------------------
M=META/misc_info.txt
sed -i 's/^erofs_default_compressor=.*/erofs_default_compressor=none/; /^erofs_sparse_flag=/d; /^erofs_default_compress_hints=/d; s/^avb_system_hashtree_enable=true/avb_system_hashtree_enable=false/' $M
grep -q '^erofs_share_dup_blocks=' $M || echo 'erofs_share_dup_blocks=true' >> $M
log "misc_info:"; grep -E '^(erofs_|avb_system_hashtree)' $M

# props (final values of slim -> slim5; vendor build.prop) ---------------------------------------------------
P=VENDOR/build.prop
sed -i 's/^dalvik.vm.heapgrowthlimit=.*/dalvik.vm.heapgrowthlimit=128m/; s/^dalvik.vm.heapsize=.*/dalvik.vm.heapsize=256m/; s/^dalvik.vm.heapstartsize=.*/dalvik.vm.heapstartsize=2m/; s/^dalvik.vm.heapmaxfree=.*/dalvik.vm.heapmaxfree=2m/' $P
sed -i '/^ro.lmk\./d; /^ro.config.low_ram=/d' $P
cat >> $P <<'PROPS'
# agent-emu slim5-a17
ro.config.low_ram=true
ro.lmk.critical_upgrade=false
ro.lmk.upgrade_pressure=40
ro.lmk.downgrade_pressure=60
ro.lmk.kill_heaviest_task=false
ro.lmk.thrashing_limit=1000
ro.lmk.psi_partial_stall_ms=200
ro.lmk.psi_complete_stall_ms=700
ro.lmk.swap_free_low_percentage=2
ro.zygote.disable_gl_preload=true
ro.config.max_starting_bg=2
config.disable_systemui=true
PROPS
grep -nE '^(dalvik.vm.heap|ro.lmk|ro.config|ro.zygote|config.disable)' $P
for f in SYSTEM/build.prop SYSTEM_EXT/etc/build.prop PRODUCT/etc/build.prop; do grep -HnE '^(ro.lmk|ro.config.low_ram|dalvik.vm.heap)' $f 2>/dev/null; done && echo "WARN: keys above also set outside vendor"

# slim: 33 APK dirs ----------------------------------------------------------------------------------------------
log "slim APKs"
gone SYSTEM/app/{Traceur,PrintSpooler,WallpaperBackup,EasterEgg,HTMLViewer} \
  SYSTEM/priv-app/{DeviceDiagnostics,CellBroadcastLegacyApp,ManagedProvisioning,DynamicSystemInstallationService,DocumentsUI,LiveWallpapersPicker,BuiltInPrintService,MusicFX,VpnDialogs,CallLogBackup,BackupRestoreConfirmation,SharedStorageBackup,SoundPicker,MtpService} \
  SYSTEM_EXT/priv-app/{EmergencyInfo,StorageManager,WallpaperCropper} \
  PRODUCT/app/{messaging,Calendar,Camera2,QuickSearchBox,DeskClock,Gallery2,Music} \
  PRODUCT/priv-app/{Contacts,ImsServiceEntitlement,StatementService,Dialer}

# slim3: SystemUI and its overlays ---------------------------------------------------------------------------------
log "SystemUI"
gone SYSTEM_EXT/priv-app/SystemUI
for o in $(find VENDOR/overlay PRODUCT/overlay SYSTEM_EXT/overlay -maxdepth 2 -iname '*systemui*' 2>/dev/null); do gone "$o"; done

# slim4: guest diet ---------------------------------------------------------------------------------------------------
sed -i 's/^on sys-boot-completed-set && property:persist.sys.zram_enabled=1$/on post-fs-data/' VENDOR/etc/init/init.cutf_cvm.rc
grep -n -A1 '^on post-fs-data$' VENDOR/etc/init/init.cutf_cvm.rc | grep -q swapon_all && log "swapon_all moved to post-fs-data" || echo "WARN: swapon_all NOT moved (rc changed in 17?)"
sed -i 's/zramsize=75%/zramsize=100%/' VENDOR/etc/fstab.cf.*; grep -h zram VENDOR/etc/fstab.cf.* | sort -u
rm -f VENDOR/etc/permissions/android.hardware.{wifi,wifi.direct,wifi.passpoint,uwb,biometrics.face,fingerprint}.xml
rm -f VENDOR/etc/permissions/android.hardware.camera.{concurrent,flash-autofocus,front,full,raw}.xml
rm -f SYSTEM/etc/permissions/android.software.{live_wallpaper,window_magnification}.xml
for f in android.hardware.bluetooth android.hardware.camera android.software.print android.software.backup \
         android.software.companion_device_setup android.software.app_widgets android.software.voice_recognizers \
         android.software.controls android.software.credentials android.software.picture_in_picture; do
  sed -i "/<feature name=\"$f\" /d" VENDOR/etc/permissions/handheld_core_hardware.xml
done
rm -f VENDOR/etc/permissions/android.software.credentials.xml SYSTEM/etc/permissions/android.software.credentials.xml
log "features removed"
log "vendor HAL apexes"
for a in com.google.cf.bt com.google.cf.nfc com.android.hardware.uwb com.android.hardware.threadnetwork \
         com.android.hardware.wifi com.google.cf.wifi com.google.cf.wpa_supplicant com.android.hardware.gnss \
         com.google.cf.ir com.android.hardware.contexthub com.android.hardware.cas com.android.hardware.drm.clearkey \
         com.android.hardware.neuralnetworks com.android.hardware.tetheroffload; do gone VENDOR/apex/$a.apex; done
rm -f VENDOR/etc/init/{face,fingerprint}-default.rc VENDOR/etc/vintf/manifest/{face,fingerprint}-default.xml
rm -f VENDOR/bin/hw/android.hardware.biometrics.{face,fingerprint}-service.default
log "slim4 APKs"
gone PRODUCT/app/{Browser2,LatinIME,PhotoTable} PRODUCT/priv-app/SettingsIntelligence \
  SYSTEM/app/{BasicDreams,BluetoothMidiService,BookmarkProvider,CameraExtensionsProxy,CarrierDefaultApp,PartnerBookmarksProvider,PrintRecommendationService,SecureElement,SimAppDialog,Stk} \
  SYSTEM/priv-app/{DeviceAsWebcam,Tag,E2eeContactKeysProvider,PrivateSpace,ONS} \
  SYSTEM_EXT/priv-app/{AvatarPicker,CFSatelliteService,ThemePicker,ThreadNetworkDemoApp,Launcher3QuickStep} \
  SYSTEM_EXT/app/AccessibilityMenu VENDOR/priv-app/CuttlefishService

# slim5: code-free HOME stub ---------------------------------------------------------------------------------------
mkdir -p SYSTEM/app/HomeStub && cp /mnt/c/dev/agent-emu-work/leverD/a17/HomeStub.apk SYSTEM/app/HomeStub/
printf 'system/app/HomeStub 0 0 755 capabilities=0x0\nsystem/app/HomeStub/HomeStub.apk 0 0 644 capabilities=0x0\n' >> META/filesystem_config.txt
log "HomeStub added"
log "DONE"

# A17: sensors multi-HAL blocks boot (see edits-sensors.sh); run it after this script.
