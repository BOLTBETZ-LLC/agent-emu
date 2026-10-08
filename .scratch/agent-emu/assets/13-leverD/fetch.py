# Lever D fetcher: one artifact of one build via Build API v4 (same key as fetch-images.py). Usage: fetch.py BUILD TARGET OUTDIR NAME...
import json, os, sys, urllib.request, concurrent.futures as cf
KEY = "AIzaSyBIelMvbjtNkpa5O96eqbm_IuSUA5WsO14"
B, T, OUT, names = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
SEG = 32 << 20
base = f"https://androidbuild-pa.googleapis.com/v4/builds/{B}/{T}/attempts/latest/artifacts"
def get(u, hdr=None):
    h = {"User-Agent": "Mozilla/5.0"}; h.update(hdr or {})
    return urllib.request.urlopen(urllib.request.Request(u, headers=h), timeout=300).read()
os.makedirs(OUT, exist_ok=True)
def size(n):
    tok = ""
    while True:
        r = json.loads(get(f"{base}?key={KEY}&pageSize=1000" + (f"&pageToken={tok}" if tok else "")))
        for a in r.get("artifacts", []):
            if a["name"] == n: return int(a["size"])
        tok = r.get("nextPageToken")
        if not tok: raise KeyError(n)
jobs = []; sizes = {}
for n in names:
    sizes[n] = s = size(n); d = os.path.join(OUT, n + ".parts"); os.makedirs(d, exist_ok=True)
    for i, off in enumerate(range(0, s, SEG)): jobs.append((n, os.path.join(d, f"{i:05d}"), off, min(off + SEG, s) - 1))
def seg(j):
    n, p, s, e = j
    if os.path.exists(p) and os.path.getsize(p) == e - s + 1: return
    for _ in range(5):
        try:
            u = json.loads(get(f"{base}/{n}/url?key={KEY}")); u = u.get("signedUrl") or u.get("url")
            data = get(u, {"Range": f"bytes={s}-{e}"})
            if len(data) == e - s + 1: open(p + ".tmp", "wb").write(data); os.replace(p + ".tmp", p); return
        except Exception as x: err = x; print("retry", n, s, repr(x)[:200], flush=True)
    raise RuntimeError(f"segment failed {n} {s}")
with cf.ThreadPoolExecutor(12) as ex: list(ex.map(seg, jobs))
for n in names:
    d = os.path.join(OUT, n + ".parts"); dst = os.path.join(OUT, n)
    with open(dst, "wb") as f:
        for p in sorted(os.listdir(d)): f.write(open(os.path.join(d, p), "rb").read())
    assert os.path.getsize(dst) == sizes[n]; print("done", n, sizes[n], flush=True)
