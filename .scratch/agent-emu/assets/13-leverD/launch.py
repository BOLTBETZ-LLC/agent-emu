# usage: launch.py <dir> <id> <mem> [cpus]  - start crosvm + console bridge (port 7100+id), detached.
import os, subprocess, sys, time
W = "C:/dev/agent-emu-work"; d, i, mem = sys.argv[1], sys.argv[2], sys.argv[3]; cpus = sys.argv[4] if len(sys.argv) > 4 else "2"
for f in ("kernel.log", "logcat.log", "crosvm.log", "console.log"):
    if os.path.exists(f"{d}/{f}"): os.remove(f"{d}/{f}")
env = dict(os.environ, AE_DIR=d.replace("/", "\\"), AE_ID=i, AE_MEM=mem, AE_CPUS=cpus)
F = 0x08000000
subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f"{W}/leverD/tools/boot.ps1"], env=env,
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=F)
subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f"{W}/console_bridge.ps1", "-Id", i,
                  "-Port", str(7100 + int(i)), "-Log", f"{d}/console.log".replace("/", "\\")],
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=F)
print("launched", time.time())
