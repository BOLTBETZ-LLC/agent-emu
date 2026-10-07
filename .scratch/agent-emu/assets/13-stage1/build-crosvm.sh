#!/bin/bash
# Clone crosvm with submodules and build the Windows WHPX feature set.
L=C:/dev/agent-emu-work/logs/crosvm-build.log
export PATH="$HOME/.cargo/bin:$PATH"
export PATH="/c/dev/agent-emu-tools/protoc/bin:$PATH"
export PROTOC="C:/dev/agent-emu-tools/protoc/bin/protoc.exe"
cd C:/dev/agent-emu-work
if [ ! -d crosvm ]; then git clone --recurse-submodules --jobs 8 https://chromium.googlesource.com/crosvm/crosvm >>"$L" 2>&1 || { echo "CLONE FAILED" >>"$L"; exit 1; }; fi
cd crosvm && git log -1 --format='HEAD %h %ci' >>"$L"
rustup show active-toolchain >>"$L" 2>&1
cargo build --release --features all-msvc64,whpx,composite-disk >>"$L" 2>&1
echo "EXIT $?" >>"$L"
