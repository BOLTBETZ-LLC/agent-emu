# Convert an Android sparse image to a raw image (stdlib only).
import struct, sys

def main(src, dst):
    with open(src, "rb") as f, open(dst, "wb") as o:
        magic, major, minor, fhs, chs, blk, total_blks, total_chunks, _ = struct.unpack("<IHHHHIIII", f.read(28))
        assert magic == 0xED26FF3A, "not a sparse image"
        f.seek(fhs)
        for _ in range(total_chunks):
            ctype, _, nblk, total = struct.unpack("<HHII", f.read(chs))
            n = nblk * blk
            if ctype == 0xCAC1:  # raw
                o.write(f.read(n))
            elif ctype == 0xCAC2:  # fill
                o.write(f.read(4) * (n // 4))
            elif ctype == 0xCAC3:  # don't care
                o.seek(n, 1)
            elif ctype == 0xCAC4:  # crc32
                f.read(4)
        o.truncate(total_blks * blk)
    print(dst, total_blks * blk)

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
