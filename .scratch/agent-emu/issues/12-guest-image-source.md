# Guest image source

Type: research
Status: claimed
Blocked by:

## Question

Where do Cuttlefish `aosp_cf_x86_64_slim` API 36 images come from for this project? Answer these:

- Does ci.android.com (or another Google channel) publish prebuilt slim images: `img.zip`, kernel and `cvd-host_package`? Which branch, how recent, and what is in them?
- Building from AOSP source: what does the build need (disk, RAM, OS)? Can it run on this PC (32 GB RAM, Windows 11) inside WSL2, or does it need a cloud Linux builder?
- How is a custom kernel (GKI android16-6.12 plus `FS_DAX`/`ZONE_DEVICE`) built and swapped into a prebuilt image?
- How is the image rebuilt as uncompressed, non-inline erofs without dm-verity?
