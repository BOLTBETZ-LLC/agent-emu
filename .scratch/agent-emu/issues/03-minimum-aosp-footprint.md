# Minimum AOSP API 36 footprint

Type: research
Status: open
Blocked by:

## Question

What is the smallest known RAM footprint of an AOSP API 36 x86_64 system that can still install and run a third-party React Native app? Answer these:

- What does each part cost: kernel, init and native daemons, zygote and boot image, system_server, SystemUI, launcher?
- Which known configurations save RAM, and by how much: Android Go / `ro.config.low_ram`, Microdroid, Cuttlefish slim targets, ATD images, a headless build with no SystemUI?
- Which services can be removed while a normal APK still installs and launches?
- Any published memory numbers for these configurations, with citations.
