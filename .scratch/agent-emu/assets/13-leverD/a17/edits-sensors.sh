#!/bin/bash
# A17 delta: drop the Cuttlefish sensors multi-HAL. Its sub-HAL waits for the host sensor simulator on a console
# port that only has a sink here, so ISensors/default never registers and system_server restarts every ~3.5 min.
set -uo pipefail
cd /root/slim17/tf
rm -fv VENDOR/etc/init/android.hardware.sensors-service-multihal.rc VENDOR/etc/vintf/manifest/android.hardware.sensors-multihal.xml \
  VENDOR/bin/hw/android.hardware.sensors-service.multihal VENDOR/lib64/hw/android.hardware.sensors@2.1-impl.cuttlefish.so
rm -fv VENDOR/etc/permissions/android.hardware.sensor.*.xml
grep -n -i sensor VENDOR/etc/init/*.rc VENDOR/etc/init/hw/*.rc | head
echo "EDIT: sensors HAL removed"
