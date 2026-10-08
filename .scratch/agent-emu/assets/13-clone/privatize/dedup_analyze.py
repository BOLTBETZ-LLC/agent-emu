# Same-content analysis of CoW clones' private pages.
# usage: python dedup_analyze.py <snapdir> <pages.bin> [<pages.bin> ...]
# (a) identical to the template at the same GPA, (b) content exists anywhere in the template
# (page level, and whole-64-KiB-chunk at a 64 KiB-aligned template offset), (c) identical across
# clones but not in the template, (d) zero pages. Also whole chunks that are fully (a).
import hashlib, struct, sys
from collections import Counter, defaultdict

snap, files = sys.argv[1], sys.argv[2:]
P, C = 4096, 65536
mem = open(f"{snap}/mem", "rb").read()  # region 0 starts at GPA 0 and covers the guest's low RAM
h = lambda b: hashlib.blake2b(b, digest_size=12).digest()
tmpl_page = [h(mem[o:o + P]) for o in range(0, len(mem), P)]
tmpl_set = set(tmpl_page)
tmpl_chunk_set = {h(mem[o:o + C]) for o in range(0, len(mem), C)}
ZERO = h(bytes(P))


def load(f):
    b = open(f, "rb").read(); out = {}
    for o in range(0, len(b), 8 + P):
        gpa = struct.unpack_from("<Q", b, o)[0]; out[gpa] = b[o + 8:o + 8 + P]
    return out


clones = [load(f) for f in files]
hashes = [{g: h(d) for g, d in c.items()} for c in clones]
count = Counter(x for hs in hashes for x in set(hs.values()))
for f, c, hs in zip(files, clones, hashes):
    n = len(c); mb = lambda k: f"{k / 256:.1f} MB ({100 * k / max(n, 1):.0f}%)"
    a = sum(1 for g, x in hs.items() if g // P < len(tmpl_page) and tmpl_page[g // P] == x)
    zero = sum(1 for x in hs.values() if x == ZERO)
    b_any = sum(1 for g, x in hs.items() if x in tmpl_set and not (g // P < len(tmpl_page) and tmpl_page[g // P] == x))
    cross = sum(1 for x in hs.values() if x not in tmpl_set and count[x] >= 2)
    # whole 64 KiB chunks: every private page in it identical to the template at the same GPA
    by_chunk = defaultdict(list)
    for g, x in hs.items(): by_chunk[g // C].append(g // P < len(tmpl_page) and tmpl_page[g // P] == x)
    chunks_all_a = sum(1 for v in by_chunk.values() if all(v))
    pages_in_those = sum(len(v) for v in by_chunk.values() if all(v))
    # whole chunk content equal to some 64 KiB-aligned template chunk (remap at another offset)
    chunk_b = 0
    for ci in by_chunk:
        gb = ci * C
        if gb + C <= len(mem):
            buf = b"".join(c[gb + k * P] if gb + k * P in c else mem[gb + k * P:gb + (k + 1) * P] for k in range(16))
            if h(buf) in tmpl_chunk_set and buf != mem[gb:gb + C]: chunk_b += 1
    print(f"{f}: {n / 256:.0f} MB private")
    print(f"  (a) identical to template at same GPA: {mb(a)}; whole 64K chunks revertable now: {chunks_all_a} ({pages_in_those / 256:.1f} MB private freed)")
    print(f"  (b) content in template at another offset (4K page level): {mb(b_any)}; whole chunk = another aligned template chunk: {chunk_b} chunks")
    print(f"  (c) shared with another clone, not in template: {mb(cross)}")
    print(f"  (d) zero pages: {mb(zero)}  (zero pages also counted in (a)/(b) when the template has them there)")
