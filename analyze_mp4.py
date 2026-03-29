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

def read_all_stco(fp, trak_start, trak_end):
    """Read ALL chunk offsets (not capped at 1000)."""
    result = find_box(fp, "stco", trak_start, trak_end)
    if not result:
        return []
    pos, size, hdr = result
    fp.seek(pos + hdr + 4)
    count = struct.unpack(">I", fp.read(4))[0]
    return [struct.unpack(">I", fp.read(4))[0] for _ in range(count)]


def read_all_stsz(fp, trak_start, trak_end):
    """Returns (default_size, [sample_sizes])."""
    result = find_box(fp, "stsz", trak_start, trak_end)
    if not result:
        return 0, []
    pos, size, hdr = result
    fp.seek(pos + hdr + 4)
    default_size = struct.unpack(">I", fp.read(4))[0]
    count        = struct.unpack(">I", fp.read(4))[0]
    if default_size > 0:
        return default_size, []
    sizes = [struct.unpack(">I", fp.read(4))[0] for _ in range(count)]
    return 0, sizes


def read_stsc_entries(fp, trak_start, trak_end):
    """Returns list of (first_chunk_1based, samples_per_chunk)."""
    result = find_box(fp, "stsc", trak_start, trak_end)
    if not result:
        return []
    pos, size, hdr = result
    fp.seek(pos + hdr + 4)
    count = struct.unpack(">I", fp.read(4))[0]
    entries = []
    for _ in range(count):
        fc  = struct.unpack(">I", fp.read(4))[0]
        spc = struct.unpack(">I", fp.read(4))[0]
        _   = fp.read(4)  # sample_description_index
        entries.append((fc, spc))
    return entries


def read_all_samples(fp, trak_start, trak_end):
    """Read and concatenate all raw sample bytes for a track."""
    import math as _math
    chunk_offsets = read_all_stco(fp, trak_start, trak_end)
    default_size, sample_sizes = read_all_stsz(fp, trak_start, trak_end)
    stsc = read_stsc_entries(fp, trak_start, trak_end)
    if not stsc:
        stsc = [(1, max(1, len(sample_sizes) // max(len(chunk_offsets), 1)))]

    sample_count = len(sample_sizes) if default_size == 0 else sum(1 for _ in chunk_offsets)
    parts = []
    sample_idx = 0
    for chunk_idx, chunk_off in enumerate(chunk_offsets):
        chunk_num = chunk_idx + 1
        spc = 1
        for fc, s in reversed(stsc):
            if chunk_num >= fc:
                spc = s
                break
        offset = chunk_off
        for _ in range(spc):
            if sample_idx >= sample_count:
                break
            sz = default_size if default_size > 0 else (sample_sizes[sample_idx] if sample_idx < len(sample_sizes) else 0)
            if sz > 0:
                fp.seek(offset)
                parts.append(fp.read(sz))
            offset += sz
            sample_idx += 1
    return b"".join(parts)


def parse_aim_records(data):
    """Scan bytes for AIM (S...) records: 28 53 uint32 uint16 float32 29."""
    import math as _math
    records = []
    i = 0
    n = len(data)
    while i < n - 12:
        if data[i] == 0x28 and data[i+1] == 0x53 and data[i+12] == 0x29:
            ts  = struct.unpack_from("<I", data, i+2)[0]
            ch  = struct.unpack_from("<H", data, i+6)[0]
            val = struct.unpack_from("<f", data, i+8)[0]
            if not (_math.isnan(val) or _math.isinf(val)):
                records.append((ts, ch, val))
            i += 13
        else:
            i += 1
    return records


def aim_channel_stats(records):
    """Return dict channel_id -> {min, max, mean, ptp, n}."""
    from collections import defaultdict as _dd
    ch_vals = _dd(list)
    for ts, ch, val in records:
        ch_vals[ch].append(val)
    stats = {}
    for ch, vals in ch_vals.items():
        mn, mx = min(vals), max(vals)
        stats[ch] = {"min": mn, "max": mx,
                     "mean": sum(vals)/len(vals),
                     "ptp": mx - mn, "n": len(vals)}
    return stats


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

                # For non-video/audio tracks, dump raw bytes AND parse AIM records
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

                    if "aim" in name.lower():
                        print(f"\n    ── AIM channel statistics (reading all {sample_count} samples) ──")
                        print("    (This may take a few seconds...)")
                        all_data = read_all_samples(fp, trak_start, trak_end)
                        print(f"    Total bytes read : {len(all_data):,}")
                        records = parse_aim_records(all_data)
                        print(f"    (S…) records     : {len(records):,}")
                        if records:
                            stats = aim_channel_stats(records)
                            print(f"\n    {'Ch':>5}  {'N':>7}  {'Min':>12}  {'Max':>12}  {'Mean':>12}  {'PTP':>10}")
                            print("    " + "-" * 65)
                            for ch in sorted(stats):
                                s = stats[ch]
                                print(f"    {ch:>5}  {s['n']:>7}  {s['min']:>12.4f}  {s['max']:>12.4f}"
                                      f"  {s['mean']:>12.4f}  {s['ptp']:>10.4f}")

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
