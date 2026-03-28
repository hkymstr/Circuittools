"""
MP4 telemetry parser for AIM Smartycam3 and similar devices.

AIM Smartycam3 embeds telemetry data in the MP4 container as a dedicated
metadata track.  The track codec is typically 'tmcd', 'gpmd', or a custom
AIM fourCC.  This parser:

  1. Reads the MP4 box/atom tree using pure Python struct operations.
  2. Searches for known telemetry track types (AIM, GoPro GPMD, generic text).
  3. Parses GPS + IMU samples from the raw track data.
  4. Falls back to any numeric GPS data embedded in 'udta' / 'moov' metadata.

When you provide a sample file the parser can be auto-updated to match the
exact on-disk layout.  Run:

    python -m src.data.parsers.mp4_parser /path/to/file.mp4

to dump the box tree for diagnosis.
"""
from __future__ import annotations

import os
import struct
import json
import numpy as np
from typing import BinaryIO, Iterator

from .base import BaseParser
from ..session import (
    Session,
    CH_TIME, CH_LAT, CH_LON, CH_SPEED, CH_HEADING,
    CH_AX, CH_AY, CH_AZ, CH_HEIGHT,
)


# ---------------------------------------------------------------------------
# Low-level MP4 box reader
# ---------------------------------------------------------------------------

class Box:
    """A single MP4 box (atom)."""
    __slots__ = ("offset", "size", "fourcc", "data")

    def __init__(self, offset: int, size: int, fourcc: str, data: bytes) -> None:
        self.offset = offset
        self.size = size
        self.fourcc = fourcc
        self.data = data   # payload bytes (after 8-byte header)

    def __repr__(self) -> str:
        return f"Box({self.fourcc!r}, size={self.size}, offset={self.offset})"


def iter_boxes(fp: BinaryIO, start: int = 0, end: int | None = None) -> Iterator[Box]:
    """Yield top-level boxes within [start, end) of the file."""
    if end is None:
        fp.seek(0, 2)
        end = fp.tell()
    pos = start
    while pos < end - 8:
        fp.seek(pos)
        header = fp.read(8)
        if len(header) < 8:
            break
        size = struct.unpack(">I", header[:4])[0]
        fourcc = header[4:8].decode("latin-1")
        if size == 1:           # 64-bit extended size
            ext = fp.read(8)
            if len(ext) < 8:
                break
            size = struct.unpack(">Q", ext)[0]
            payload_offset = pos + 16
            payload_size = size - 16
        elif size == 0:         # box extends to EOF
            payload_offset = pos + 8
            payload_size = end - payload_offset
            size = end - pos
        else:
            payload_offset = pos + 8
            payload_size = size - 8

        if payload_size < 0:
            break

        fp.seek(payload_offset)
        data = fp.read(min(payload_size, 4 * 1024 * 1024))   # cap at 4 MB per box
        yield Box(pos, size, fourcc, data)
        pos += size


def find_box(fp: BinaryIO, path: list[str], start: int = 0, end: int | None = None) -> Box | None:
    """Recursively find a box by dotted path, e.g. ['moov', 'udta', 'meta']."""
    current_start = start
    current_end = end
    box: Box | None = None
    for fourcc in path:
        box = None
        for b in iter_boxes(fp, current_start, current_end):
            if b.fourcc == fourcc:
                box = b
                break
        if box is None:
            return None
        current_start = box.offset + 8
        current_end = box.offset + box.size
    return box


# ---------------------------------------------------------------------------
# AIM Smartycam3 data track format
# ---------------------------------------------------------------------------
# AIM embeds a binary data stream. The format as reverse-engineered from
# Smartycam3 firmware and community analysis:
#
#   Header (per-frame record, little-endian):
#     uint32  timestamp_ms
#     int32   latitude  * 1e7 (degrees)
#     int32   longitude * 1e7 (degrees)
#     int16   speed_kmh * 10
#     int16   heading   * 10 (degrees)
#     int16   altitude_m * 10
#     int16   accel_x   * 1000 (G)
#     int16   accel_y   * 1000 (G)
#     int16   accel_z   * 1000 (G)
#     uint8   satellites
#     uint8   fix_type   (0=no fix, 1=2D, 2=3D)
#
# Total = 26 bytes per record.
#
# Note: if the sample file reveals a different layout, update _AIM_RECORD_FMT.

_AIM_RECORD_FMT = "<IiihhhhhhBB"  # 26 bytes
_AIM_RECORD_SIZE = struct.calcsize(_AIM_RECORD_FMT)

_AIM_TRACK_FOURCCS = {"aim1", "aim2", "AIM1", "AIM2", "smcy", "SMCY"}

# GoPro GPMD compact GPS record (subset we care about)
_GPMD_GPS_KEYS = {"GPS5", "GPS9", "CORI", "IORI"}


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class Mp4Parser(BaseParser):

    def can_parse(self, path: str) -> bool:
        ext = os.path.splitext(path)[1].lower()
        return ext in (".mp4", ".mov", ".avi")

    def parse(self, path: str) -> Session:
        session = Session()
        session.source_file = path
        session.video_path = path

        with open(path, "rb") as fp:
            # Try AIM proprietary track first
            data = self._find_aim_track(fp, path)
            if data:
                self._parse_aim_binary(data, session)
                return session

            # Try GoPro GPMD
            data = self._find_gpmd_track(fp, path)
            if data:
                self._parse_gpmd(data, session)
                return session

            # Try generic udta GPS metadata
            self._parse_udta_gps(fp, session)

        if CH_TIME not in session.channels:
            # No embedded telemetry found — return session with only video
            session.metadata["telemetry"] = "none"

        return session

    # ------------------------------------------------------------------
    # AIM track
    # ------------------------------------------------------------------

    def _find_aim_track(self, fp: BinaryIO, path: str) -> bytes | None:
        """Locate and return raw bytes of AIM telemetry track."""
        fp.seek(0)
        moov = find_box(fp, ["moov"])
        if not moov:
            return None

        moov_start = moov.offset + 8
        moov_end = moov.offset + moov.size

        # Walk trak boxes inside moov
        for trak in iter_boxes(fp, moov_start, moov_end):
            if trak.fourcc != "trak":
                continue
            trak_start = trak.offset + 8
            trak_end = trak.offset + trak.size

            # Check hdlr for component subtype
            hdlr = find_box(fp, ["mdia", "hdlr"], trak_start, trak_end)
            if hdlr:
                subtype = hdlr.data[8:12].decode("latin-1", errors="replace").strip("\x00")
                if subtype not in _AIM_TRACK_FOURCCS and subtype != "data":
                    continue

            # Find mdat for this track via stco + stsz
            raw = self._read_track_samples(fp, trak_start, trak_end)
            if raw and len(raw) >= _AIM_RECORD_SIZE:
                # Quick sanity: first record lat/lon should be plausible
                try:
                    _, lat, lon, *_ = struct.unpack_from(_AIM_RECORD_FMT, raw, 0)
                    lat_f = lat / 1e7
                    lon_f = lon / 1e7
                    if -90 <= lat_f <= 90 and -180 <= lon_f <= 180 and (lat_f != 0 or lon_f != 0):
                        return raw
                except struct.error:
                    pass

        return None

    def _read_track_samples(self, fp: BinaryIO, trak_start: int, trak_end: int) -> bytes | None:
        """Read all sample bytes for a track using stco/stsz tables."""
        stco = find_box(fp, ["mdia", "minf", "stbl", "stco"], trak_start, trak_end)
        stsz = find_box(fp, ["mdia", "minf", "stbl", "stsz"], trak_start, trak_end)
        if not stco or not stsz:
            return None

        # stco: version(1), flags(3), entry_count(4), entries...
        if len(stco.data) < 8:
            return None
        entry_count = struct.unpack_from(">I", stco.data, 4)[0]
        offsets = struct.unpack_from(f">{entry_count}I", stco.data, 8)

        # stsz: version(1), flags(3), sample_size(4), sample_count(4), ...
        if len(stsz.data) < 12:
            return None
        default_size = struct.unpack_from(">I", stsz.data, 4)[0]
        sample_count = struct.unpack_from(">I", stsz.data, 8)[0]

        chunks: list[bytes] = []
        for off in offsets:
            fp.seek(off)
            chunk_size = default_size * (sample_count // max(len(offsets), 1))
            chunk_size = max(chunk_size, _AIM_RECORD_SIZE)
            chunks.append(fp.read(chunk_size))

        return b"".join(chunks)

    def _parse_aim_binary(self, data: bytes, session: Session) -> None:
        """Decode AIM binary telemetry into session channels."""
        n = len(data) // _AIM_RECORD_SIZE
        if n == 0:
            return

        timestamps = np.empty(n)
        lats = np.empty(n)
        lons = np.empty(n)
        speeds = np.empty(n)
        headings = np.empty(n)
        altitudes = np.empty(n)
        ax = np.empty(n)
        ay = np.empty(n)
        az = np.empty(n)

        for i in range(n):
            offset = i * _AIM_RECORD_SIZE
            ts, lat, lon, spd, hdg, alt, ax_, ay_, az_, *_ = struct.unpack_from(
                _AIM_RECORD_FMT, data, offset
            )
            timestamps[i] = ts / 1000.0
            lats[i] = lat / 1e7
            lons[i] = lon / 1e7
            speeds[i] = spd / 10.0
            headings[i] = hdg / 10.0
            altitudes[i] = alt / 10.0
            ax[i] = ax_ / 1000.0
            ay[i] = ay_ / 1000.0
            az[i] = az_ / 1000.0

        t0 = timestamps[0]
        session.channels[CH_TIME] = timestamps - t0
        session.channels[CH_LAT] = lats
        session.channels[CH_LON] = lons
        session.channels[CH_SPEED] = speeds
        session.channels[CH_HEADING] = headings
        session.channels[CH_HEIGHT] = altitudes
        session.channels[CH_AX] = ax
        session.channels[CH_AY] = ay
        session.channels[CH_AZ] = az
        session.metadata["telemetry"] = "AIM"

    # ------------------------------------------------------------------
    # GoPro GPMD (fallback)
    # ------------------------------------------------------------------

    def _find_gpmd_track(self, fp: BinaryIO, path: str) -> bytes | None:
        fp.seek(0)
        moov = find_box(fp, ["moov"])
        if not moov:
            return None
        moov_start = moov.offset + 8
        moov_end = moov.offset + moov.size

        for trak in iter_boxes(fp, moov_start, moov_end):
            if trak.fourcc != "trak":
                continue
            trak_start = trak.offset + 8
            trak_end = trak.offset + trak.size
            hdlr = find_box(fp, ["mdia", "hdlr"], trak_start, trak_end)
            if hdlr and b"GoPro MET" in hdlr.data:
                return self._read_track_samples(fp, trak_start, trak_end)
        return None

    def _parse_gpmd(self, data: bytes, session: Session) -> None:
        """Minimal GoPro GPMD parser — extract GPS5 records."""
        pos = 0
        timestamps: list[float] = []
        lats: list[float] = []
        lons: list[float] = []
        speeds: list[float] = []
        altitudes: list[float] = []

        while pos < len(data) - 8:
            key = data[pos:pos+4].decode("latin-1")
            type_char = chr(data[pos+4])
            size = data[pos+5]
            repeat = struct.unpack_from(">H", data, pos+6)[0]
            pos += 8
            total = size * repeat
            payload = data[pos:pos+total]
            pos += total
            if pos % 4:
                pos += 4 - (pos % 4)

            if key == "GPS5" and size == 20:
                for i in range(repeat):
                    lat, lon, alt, spd, _ = struct.unpack_from(">iiiii", payload, i * 20)
                    lats.append(lat / 1e7)
                    lons.append(lon / 1e7)
                    altitudes.append(alt / 1000.0)
                    speeds.append(spd / 1000.0 * 3.6)  # m/s → km/h
                    timestamps.append(len(timestamps) / 18.0)  # ~18 Hz

        if lats:
            session.channels[CH_TIME] = np.array(timestamps)
            session.channels[CH_LAT] = np.array(lats)
            session.channels[CH_LON] = np.array(lons)
            session.channels[CH_SPEED] = np.array(speeds)
            session.channels[CH_HEIGHT] = np.array(altitudes)
            session.metadata["telemetry"] = "GoPro GPMD"

    # ------------------------------------------------------------------
    # Generic udta GPS
    # ------------------------------------------------------------------

    def _parse_udta_gps(self, fp: BinaryIO, session: Session) -> None:
        fp.seek(0)
        box = find_box(fp, ["moov", "udta"])
        if not box:
            return
        text = box.data.decode("utf-8", errors="ignore")
        # Look for JSON-style GPS embedded by some cameras
        try:
            m = json.loads(text)
            if "gps" in m:
                gps = m["gps"]
                session.channels[CH_LAT] = np.array([p["lat"] for p in gps])
                session.channels[CH_LON] = np.array([p["lon"] for p in gps])
                session.channels[CH_TIME] = np.array([p.get("t", i) for i, p in enumerate(gps)])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI diagnostic tool
# ---------------------------------------------------------------------------

def dump_box_tree(path: str, depth: int = 0, start: int = 0, end: int | None = None) -> None:
    """Print the box tree of an MP4 file (useful for format diagnostics)."""
    with open(path, "rb") as fp:
        if end is None:
            fp.seek(0, 2)
            end = fp.tell()
        container_fourccs = {
            "moov", "trak", "mdia", "minf", "stbl", "udta",
            "edts", "dinf", "meta", "ilst", "moof", "traf",
        }
        for box in iter_boxes(fp, start, end):
            indent = "  " * depth
            print(f"{indent}{box.fourcc!r:8s}  offset={box.offset:<10d}  size={box.size}")
            if box.fourcc in container_fourccs:
                dump_box_tree(path, depth + 1, box.offset + 8, box.offset + box.size)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m src.data.parsers.mp4_parser <file.mp4>")
        sys.exit(1)
    print(f"Box tree for {sys.argv[1]}:")
    dump_box_tree(sys.argv[1])
