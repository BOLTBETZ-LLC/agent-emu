# Join host privatized-page dumps with guest /proc/kpageflags: what each privatized guest page is now.
# usage: python attrib_analyze.py <tag>
import glob, os, re, struct, sys
from collections import Counter

X = "C:/dev/agent-emu-work/clone-exp"; OUT = f"{X}/out/{sys.argv[1]}"
# kpageflags bits (include/uapi/linux/kernel-page-flags.h)
B = dict(LOCKED=0, ERROR=1, REFERENCED=2, UPTODATE=3, DIRTY=4, LRU=5, ACTIVE=6, SLAB=7, WRITEBACK=8, RECLAIM=9, BUDDY=10,
         MMAP=11, ANON=12, SWAPCACHE=13, SWAPBACKED=14, COMPOUND_HEAD=15, COMPOUND_TAIL=16, HUGE=17, UNEVICTABLE=18, HWPOISON=19,
         NOPAGE=20, KSM=21, THP=22, OFFLINE=23, ZERO_PAGE=24, IDLE=25, PGTABLE=26)


def kind(f):
    has = lambda n: f >> B[n] & 1
    if has("NOPAGE"): return "nopage"
    if has("BUDDY"): return "free(buddy)"
    if has("OFFLINE"): return "offline(balloon)"
    if has("SLAB"): return "slab"
    if has("PGTABLE"): return "pagetable"
    if has("ZERO_PAGE"): return "zero"
    if has("ANON") or has("SWAPBACKED"):
        return "anon/shmem" + ("+mapped" if has("MMAP") else "")
    if has("LRU"): return "file cache" + ("+mapped" if has("MMAP") else "")
    if f == 0: return "kernel other (no flags: zsmalloc, vmalloc, stacks, pinned, unref'd free)"
    return "other flags"


def host(path):
    b = open(path, "rb").read(); off = 0; out = {}
    while off < len(b):
        base, n = struct.unpack_from("<QQ", b, off); off += 16
        out[base] = b[off:off + n]; off += n
    return out


dumps = sorted(glob.glob(f"{OUT}/host-*.bin"))
print("host dumps:", [os.path.basename(d) for d in dumps])
priv_t = {}
for d in dumps:
    t = int(re.search(r"host-(\d+)\.bin", d).group(1)); h = host(d)
    priv_t[t] = {(base >> 12) + k for base, fl in h.items() for k, v in enumerate(fl) if v == 2}
    print(f"t={t:4d}s privatized {len(priv_t[t]) / 256:.0f} MB")
for g in sorted(glob.glob(f"{OUT}/g*-kpageflags.bin")):
    tg = int(re.search(r"g(\d+)-", g).group(1)); kp = open(g, "rb").read(); npfn = len(kp) // 8
    th = max([t for t in priv_t if t <= tg + 15] or [min(priv_t)])
    pf = priv_t[th]
    c = Counter(kind(struct.unpack_from("<Q", kp, p * 8)[0]) for p in pf if p < npfn)
    allc = Counter(kind(struct.unpack_from("<Q", kp, p * 8)[0]) for p in range(npfn))
    print(f"\nguest t={tg}s vs host dump t={th}s: {len(pf) / 256:.0f} MB privatized; by current guest use (MB privatized / MB total in guest):")
    for k, v in c.most_common(): print(f"  {k:70s} {v / 256:7.1f} / {allc[k] / 256:7.1f}")
# what got privatized between dumps, classified at the last guest pull
ts = sorted(priv_t)
if len(ts) > 2:
    last = sorted(glob.glob(f"{OUT}/g*-kpageflags.bin"))[-1]; kp = open(last, "rb").read(); npfn = len(kp) // 8
    for lo, hi in ((ts[0], 60), (60, 300), (300, 330), (330, ts[-1])):
        a0 = max([t for t in ts if t <= lo] or [ts[0]]); a1 = max([t for t in ts if t <= hi] or [ts[0]])
        new = priv_t[a1] - priv_t[a0]
        c = Counter(kind(struct.unpack_from("<Q", kp, p * 8)[0]) for p in new if p < npfn)
        print(f"\nnew privatized {a0}->{a1}s: {len(new) / 256:.0f} MB, now:", {k: round(v / 256, 1) for k, v in c.most_common(6)})
