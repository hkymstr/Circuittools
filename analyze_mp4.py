"""
MP4 telemetry diagnostic tool for AIM Smartycam3.
Run:  python analyze_mp4.py <file.mp4>

Prints:
  1. Full MP4 box/atom tree
  2. All tracks found (type, codec, sample count, sample size)
  3. Raw hex dump of first 256 bytes of each non-video/audio track
  4. Any readable text/metadata found in udta/moov boxes
"""
import sys
import struct
import os

def read_box_header(fp):
    data = fp.read(8)
    if len(data) < 8:
        return None, None, None
    size = struct.unpack(">I", data[:4])[0]
    fourcc = data[4:8].decode("latin-1", errors="replace")
    if size == 1:
        ext = fp.read(8)
        if len(ext) < 8:
            return None, None, None
        size = struct.unpack(">Q", ext)[0]
        header_size = 16
    elif size == 0:
        fp.seek(0, 2)
        size = fp.tell() - (fp.tell() - 8)
        fp.seek(-8, 1)
        header_size = 8
    else:
        header_size = 8
    return size, fourcc, header_size

CONTAINER_BOXES = {
    "moov","trak","mdia","minf","stbl","udta","edts","dinf",
    "meta","ilst","moof","traf","mvex","schi",
}

def dump_tree(fp, start, end, depth=0):
    pos = start
    boxes = []
    while pos < end - 8:
        fp.seek(pos)
        size, fourcc, hdr = read_box_header(fp)
        if size is None or size < 8:
            break
        indent = "  " * depth
        print(f"{indent}{fourcc!r:10s} offset={pos:<12d} size={size}")
        boxes.append((pos, size, fourcc))
        if fourcc in CONTAINER_BOXES and size > 8:
            dump_tree(fp, pos + hdr, pos + size, depth + 1)
        pos += size
    return boxes

def read_stco(fp, trak_start, trak_end):
    """Read chunk offsets for a track."""
    offsets = []
    pos = trak_start
    while pos < trak_end - 8:
        fp.seek(pos)
        size, fourcc, hdr = read_box_header(fp)
        if size is None or size < 8:
            break
        if fourcc == "stco":
            fp.seek(pos + hdr + 4)  # skip version+flags
            count = struct.unpack(">I", fp.read(4))[0]
            for _ in range(min(count, 1000)):
                o = struct.unpack(">I", fp.read(4))[0]
                offsets.append(o)
            break
        if fourcc in CONTAINER_BOXES:
            sub = read_stco_from(fp, pos + hdr, pos + size)
            if sub:
                return sub
        pos += size
    return offsets

def read_stco_from(fp, start, end):
    pos = start
    while pos < end - 8:
        fp.seek(pos)
        size, fourcc, hdr = read_box_header(fp)
        if size is None or size < 8:
            break
        if fourcc == "stco":
            fp.seek(pos + hdr + 4)
            count = struct.unpack(">I", fp.read(4))[0]
            offsets = []
            for _ in range(min(count, 1000)):
                o = struct.unpack(">I", fp.read(4))[0]
                offsets.append(o)
            return offsets
        if fourcc in CONTAINER_BOXES:
            sub = read_stco_from(fp, pos + hdr, pos + size)
            if sub:
                return sub
        pos += size
    return []

def find_box(fp, fourcc_target, start, end, depth=0):
    if depth > 6:
        return None
    pos = start
    while pos < end - 8:
        fp.seek(pos)
        size, fourcc, hdr = read_box_header(fp)
        if size is None or size < 8:
            break
        if fourcc == fourcc_target:
            return pos, size, hdr
        if fourcc in CONTAINER_BOXES:
            result = find_box(fp, fourcc_target, pos + hdr, pos + size, depth + 1)
            if result:
                return result
        pos += size
    return None

def get_track_handler(fp, trak_start, trak_end):
    result = find_box(fp, "hdlr", trak_start, trak_end)
    if not result:
        return "unknown", "unknown"
    pos, size, hdr = result
    fp.seek(pos + hdr + 8)  # skip version(1)+flags(3)+pre_defined(4)
    handler = fp.read(4).decode("latin-1", errors="replace").strip("\x00")
    fp.seek(pos + hdr + 24)  # skip to name
    name_bytes = fp.read(size - hdr - 24)
    name = name_bytes.decode("utf-8", errors="replace").strip("\x00").strip()
    return handler, name

def get_sample_info(fp, trak_start, trak_end):
    """Get sample count and default sample size."""
    result = find_box(fp, "stsz", trak_start, trak_end)
    if not result:
        return 0, 0
    pos, size, hdr = result
    fp.seek(pos + hdr + 4)  # version+flags
    default_size = struct.unpack(">I", fp.read(4))[0]
    count = struct.unpack(">I", fp.read(4))[0]
    return count, default_size

def hexdump(data, max_bytes=256):
    data = data[:max_bytes]
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"  {i:04x}  {hex_part:<48s}  {ascii_part}")
    return "\n".join(lines)

def analyze(path):
    print(f"\n{'='*60}")
    print(f"Analyzing: {os.path.basename(path)}")
    print(f"File size: {os.path.getsize(path):,} bytes")
    print(f"{'='*60}\n")

    with open(path, "rb") as fp:
        fp.seek(0, 2)
        file_size = fp.tell()

        print("── BOX TREE ──────────────────────────────────────────────")
        dump_tree(fp, 0, file_size)

        # Find moov
        moov = find_box(fp, "moov", 0, file_size)
        if not moov:
            print("\nERROR: No moov box found — not a valid MP4.")
            return

        moov_pos, moov_size, moov_hdr = moov
        moov_start = moov_pos + moov_hdr
        moov_end   = moov_pos + moov_size

        print("\n── TRACKS ────────────────────────────────────────────────")
        track_num = 0
        pos = moov_start
        while pos < moov_end - 8:
            fp.seek(pos)
            size, fourcc, hdr = read_box_header(fp)
            if size is None or size < 8:
                break
            if fourcc == "trak":
                track_num += 1
                trak_start = pos + hdr
                trak_end   = pos + size
                handler, name = get_track_handler(fp, trak_start, trak_end)
                sample_count, default_size = get_sample_info(fp, trak_start, trak_end)
                offsets = read_stco_from(fp, trak_start, trak_end)

                print(f"\n  Track {track_num}:")
                print(f"    Handler type : {handler!r}")
                print(f"    Handler name : {name!r}")
                print(f"    Sample count : {sample_count}")
                print(f"    Default size : {default_size}")
                print(f"    Chunk offsets: {offsets[:5]}{'...' if len(offsets) > 5 else ''}")

                # For non-video/audio tracks, dump raw bytes
                if handler not in ("vide", "soun") and offsets:
                    print(f"\n    ── Raw bytes of first chunk (offset {offsets[0]}) ──")
                    fp.seek(offsets[0])
                    raw = fp.read(256)
                    print(hexdump(raw))
                    if sample_count > 1 and len(offsets) > 1:
                        print(f"\n    ── Raw bytes of second chunk (offset {offsets[1]}) ──")
                        fp.seek(offsets[1])
                        raw2 = fp.read(256)
                        print(hexdump(raw2))

            pos += size

        # Dump udta / user metadata text
        udta = find_box(fp, "udta", moov_start, moov_end)
        if udta:
            pos2, size2, hdr2 = udta
            fp.seek(pos2 + hdr2)
            raw_udta = fp.read(size2 - hdr2)
            text = raw_udta.decode("utf-8", errors="replace")
            printable = ''.join(c if c.isprintable() or c in '\n\r\t' else ' ' for c in text)
            collapsed = ' '.join(printable.split())
            if collapsed.strip():
                print(f"\n── UDTA METADATA ─────────────────────────────────────────")
                print(collapsed[:2000])

    print(f"\n{'='*60}")
    print("Done. Paste the full output above into Claude.")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python analyze_mp4.py <file.mp4>")
        sys.exit(1)
    analyze(sys.argv[1])
