# Fetch the API 36 Cuttlefish only_phone prebuilt (build 15581820) via the public Build API v4,
# using Cuttlefish's own compiled-in public key. Parallel ranged download, resumable.
import json, os, urllib.request, concurrent.futures as cf
KEY = "AIzaSyBIelMvbjtNkpa5O96eqbm_IuSUA5WsO14"
B, T = "15581820", "aosp_cf_x86_64_only_phone-userdebug"
OUT = "C:/dev/agent-emu-work/images/15581820"
SEG = 32 << 20  # 32 MiB segments
base = f"https://androidbuild-pa.googleapis.com/v4/builds/{B}/{T}/attempts/latest/artifacts"
def get(u, hdr=None):
    h = {"User-Agent": "Mozilla/5.0"}; h.update(hdr or {})
    return urllib.request.urlopen(urllib.request.Request(u, headers=h), timeout=300).read()
arts = json.loads(get(f"{base}?key={KEY}&pageSize=1000")).get("artifacts", [])
want = [a for a in arts if a["name"].endswith((".zip", ".tar.gz")) and any(k in a["name"] for k in ("-img-", "target_files", "otatools", "cvd-host_package"))]
jobs = []
for a in want:
    size = int(a["size"]); d = os.path.join(OUT, a["name"] + ".parts"); os.makedirs(d, exist_ok=True)
    url = a["name"]
    for i, off in enumerate(range(0, size, SEG)):
        jobs.append((a["name"], url, os.path.join(d, f"{i:05d}"), off, min(off + SEG, size) - 1))
def seg(j):
    name, url, p, s, e = j
    if os.path.exists(p) and os.path.getsize(p) == e - s + 1: return
    err = None
    for _ in range(5):
        try:
            u = json.loads(get(f"{base}/{url}/url?key={KEY}")); u = u.get("signedUrl") or u.get("url")
            data = get(u, {"Range": f"bytes={s}-{e}"})
            if len(data) == e - s + 1:
                open(p + ".tmp", "wb").write(data); os.replace(p + ".tmp", p); return
        except Exception as x: err = x
    raise RuntimeError(f"segment failed {name} {s}: {err!r}")
with cf.ThreadPoolExecutor(16) as ex:
    for n, _ in enumerate(ex.map(seg, jobs), 1):
        if n % 10 == 0: print(f"segments {n}/{len(jobs)}", flush=True)
for a in want:
    d = os.path.join(OUT, a["name"] + ".parts"); dst = os.path.join(OUT, a["name"])
    with open(dst, "wb") as f:
        for p in sorted(os.listdir(d)): f.write(open(os.path.join(d, p), "rb").read())
    assert os.path.getsize(dst) == int(a["size"]), a["name"]
    print("done", a["name"], os.path.getsize(dst), flush=True)
print("ALLDONE")
