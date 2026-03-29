"""
MP4 telemetry parser for AIM Smartycam3.

The AIM Smartycam3 embeds all telemetry in a dedicated 'meta' track inside
the MP4 container.  The track handler name is 'MetaAimHandler'.

Binary record format (13 bytes each, little-endian):
    0x28 0x53   magic "(S"
    uint32      timestamp_ms
    uint16      channel_id
    float32     value
    0x29        magic ")"

Channels are identified automatically from their value ranges:
  • latitude  — stable values in [-90, 90], ptp < 2 °
  • longitude — stable values in [-180, 180], ptp < 5 °, different from lat
  • speed     — values in [0, 400] km/h, ptp > 5
  • altitude  — values in [0, 10 000] m/ft, ptp < 3 000

Diagnostic mode (prints all channel IDs and statistics):
    python -m src.data.parsers.mp4_parser /path/to/file.mp4
"""
from __future__ import annotations

import math
import os
import struct
import json
from collections import defaultdict
from typing import BinaryIO, Iterator

import numpy as np

from .base import BaseParser
from ..session import (
    Session,
    CH_TIME, CH_LAT, CH_LON, CH_SPEED, CH_HEADING,
    CH_AX, CH_AY, CH_AZ, CH_HEIGHT,
)


# ---------------------------------------------------------------------------
# Low-level MP4 box helpers
# ---------------------------------------------------------------------------

class Box:
    __slots__ = ("offset", "size", "fourcc", "data")

    def __init__(self, offset: int, size: int, fourcc: str, data: bytes) -> None:
        self.offset = offset
        self.size = size
        self.fourcc = fourcc
        self.data = data  # payload bytes after the box header

    def __repr__(self) -> str:
        return f"Box({self.fourcc!r}, size={self.size}, offset={self.offset})"


def iter_boxes(fp: BinaryIO, start: int = 0, end: int | None = None) -> Iterator[Box]:
    """Yield top-level boxes within [start, end)."""
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
        if size == 1:
            ext = fp.read(8)
            if len(ext) < 8:
                break
            size = struct.unpack(">Q", ext)[0]
            payload_offset = pos + 16
            payload_size = size - 16
        elif size == 0:
            payload_offset = pos + 8
            payload_size = end - payload_offset
            size = end - pos
        else:
            payload_offset = pos + 8
            payload_size = size - 8

        if payload_size < 0:
            break

        fp.seek(payload_offset)
        # Cap at 8 MB per box to protect against corrupt sizes
        data = fp.read(min(payload_size, 8 * 1024 * 1024))
        yield Box(pos, size, fourcc, data)
        pos += size


def find_box(fp: BinaryIO, path: list[str], start: int = 0, end: int | None = None) -> Box | None:
    """Find a box by path, e.g. ['moov', 'trak', 'mdia', 'hdlr']."""
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
# AIM Smartycam3 telemetry parser
# ---------------------------------------------------------------------------

class Mp4Parser(BaseParser):

    def can_parse(self, path: str) -> bool:
        return os.path.splitext(path)[1].lower() in (".mp4", ".mov", ".avi")

    def parse(self, path: str) -> Session:
        session = Session()
        session.source_file = path
        session.video_path = path

        with open(path, "rb") as fp:
            fp.seek(0, 2)
            file_size = fp.tell()

            moov = find_box(fp, ["moov"], 0, file_size)
            if not moov:
                session.metadata["telemetry"] = "none"
                return session

            moov_start = moov.offset + 8
            moov_end = moov.offset + moov.size

            # Try AIM Smartycam3 meta track
            if self._parse_aim_track(fp, moov_start, moov_end, session):
                return session

            # Try GoPro GPMD metadata track
            if self._parse_gopro_track(fp, moov_start, moov_end, session):
                return session

            # Last resort: look for JSON GPS in udta
            self._parse_udta_gps(fp, moov_start, moov_end, session)

        if CH_TIME not in session.channels:
            session.metadata["telemetry"] = "none"

        return session

    # ------------------------------------------------------------------
    # AIM Smartycam3
    # ------------------------------------------------------------------

    def _parse_aim_track(
        self, fp: BinaryIO, moov_start: int, moov_end: int, session: Session
    ) -> bool:
        """Find and parse the AIM meta track. Returns True on success."""
        trak_bounds = self._find_aim_meta_track(fp, moov_start, moov_end)
        if not trak_bounds:
            return False

        trak_start, trak_end = trak_bounds
        raw_data = self._read_track_data(fp, trak_start, trak_end)
        if not raw_data:
            return False

        records = self._parse_aim_records(raw_data)
        if len(records) < 10:
            return False

        assignments, ch_stats = self._auto_assign_channels(records)

        # Need at least time or speed to be useful
        if assignments.get("time") is None and assignments.get("speed") is None:
            return False

        self._build_session(records, assignments, ch_stats, session)

        # Recover GPS lat/lon from <hGPS> blocks (int32 × 1e-7 at fixed offsets)
        lats, lons = self._parse_aim_gps_blocks(raw_data)
        self._inject_gps_blocks(lats, lons, session)

        return True

    def _find_aim_meta_track(
        self, fp: BinaryIO, moov_start: int, moov_end: int
    ) -> tuple[int, int] | None:
        """Return (trak_start, trak_end) for the AIM MetaAimHandler track."""
        for trak in iter_boxes(fp, moov_start, moov_end):
            if trak.fourcc != "trak":
                continue
            ts = trak.offset + 8
            te = trak.offset + trak.size

            hdlr = find_box(fp, ["mdia", "hdlr"], ts, te)
            if not hdlr or len(hdlr.data) < 12:
                continue

            # hdlr payload layout:
            #   0-3:  version + flags
            #   4-7:  pre_defined
            #   8-11: handler_type   (b'meta', b'vide', b'soun', …)
            #  12-23: reserved
            #  24+:   handler_name   (null-terminated UTF-8)
            handler_type = hdlr.data[8:12].decode("latin-1", errors="replace").strip("\x00")
            handler_name = (
                hdlr.data[24:].decode("utf-8", errors="replace").strip("\x00").lower()
                if len(hdlr.data) > 24
                else ""
            )

            if handler_type == "meta" and "aim" in handler_name:
                return ts, te

        return None

    def _read_track_data(
        self, fp: BinaryIO, trak_start: int, trak_end: int
    ) -> bytes | None:
        """
        Read all sample bytes for a track using stco / stsc / stsz tables.
        Returns a single bytes blob of all sample data concatenated.
        """
        stco_box = find_box(fp, ["mdia", "minf", "stbl", "stco"], trak_start, trak_end)
        stsc_box = find_box(fp, ["mdia", "minf", "stbl", "stsc"], trak_start, trak_end)
        stsz_box = find_box(fp, ["mdia", "minf", "stbl", "stsz"], trak_start, trak_end)

        if not stco_box or not stsz_box:
            return None

        # stco: version+flags(4) + count(4) + offsets(count × 4)
        if len(stco_box.data) < 8:
            return None
        chunk_count = struct.unpack_from(">I", stco_box.data, 4)[0]
        if len(stco_box.data) < 8 + chunk_count * 4:
            return None
        chunk_offsets = struct.unpack_from(f">{chunk_count}I", stco_box.data, 8)

        # stsz: version+flags(4) + default_size(4) + count(4) [+ sizes(count × 4)]
        if len(stsz_box.data) < 12:
            return None
        default_size  = struct.unpack_from(">I", stsz_box.data, 4)[0]
        sample_count  = struct.unpack_from(">I", stsz_box.data, 8)[0]
        if default_size == 0:
            if len(stsz_box.data) < 12 + sample_count * 4:
                return None
            sample_sizes: list[int] = list(
                struct.unpack_from(f">{sample_count}I", stsz_box.data, 12)
            )
        else:
            sample_sizes = [default_size] * sample_count

        # stsc: version+flags(4) + count(4) + entries(count × 12)
        # Each entry: first_chunk(4) + samples_per_chunk(4) + desc_idx(4)
        stsc_entries: list[tuple[int, int]] = []
        if stsc_box and len(stsc_box.data) >= 8:
            sc_count = struct.unpack_from(">I", stsc_box.data, 4)[0]
            for i in range(sc_count):
                fc, spc, _ = struct.unpack_from(">III", stsc_box.data, 8 + i * 12)
                stsc_entries.append((fc, spc))
        if not stsc_entries:
            stsc_entries = [(1, max(1, sample_count // max(chunk_count, 1)))]

        # Build (file_offset, sample_size) pairs
        sample_pairs: list[tuple[int, int]] = []
        sample_idx = 0
        for chunk_idx in range(chunk_count):
            chunk_num = chunk_idx + 1  # 1-based
            spc = 1
            for fc, s in reversed(stsc_entries):
                if chunk_num >= fc:
                    spc = s
                    break
            file_off = chunk_offsets[chunk_idx]
            for _ in range(spc):
                if sample_idx >= sample_count:
                    break
                sz = sample_sizes[sample_idx]
                if sz > 0:
                    sample_pairs.append((file_off, sz))
                file_off += sz
                sample_idx += 1

        # Read all samples
        parts: list[bytes] = []
        for file_off, sz in sample_pairs:
            fp.seek(file_off)
            parts.append(fp.read(sz))

        return b"".join(parts) if parts else None

    # ------------------------------------------------------------------
    # Record scanner
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_aim_records(data: bytes) -> list[tuple[int, int, float]]:
        """
        Scan *data* for AIM (S…) records.

        Each record is exactly 13 bytes:
            0x28 0x53  "(S"
            uint32 LE  timestamp_ms
            uint16 LE  channel_id
            float32 LE value
            0x29       ")"

        Returns a list of (timestamp_ms, channel_id, float_value).
        NaN / Inf values are dropped.
        """
        records: list[tuple[int, int, float]] = []
        i = 0
        n = len(data)
        while i < n - 12:
            if data[i] == 0x28 and data[i + 1] == 0x53 and data[i + 12] == 0x29:
                ts  = struct.unpack_from("<I", data, i + 2)[0]
                ch  = struct.unpack_from("<H", data, i + 6)[0]
                val = struct.unpack_from("<f", data, i + 8)[0]
                if not (math.isnan(val) or math.isinf(val)):
                    records.append((ts, ch, val))
                i += 13
            else:
                i += 1
        return records

    # ------------------------------------------------------------------
    # Channel auto-detection
    # ------------------------------------------------------------------

    @staticmethod
    def _auto_assign_channels(
        records: list[tuple[int, int, float]],
    ) -> tuple[dict[str, int | None], dict[int, dict]]:
        """
        Identify semantic channels from value-range statistics.

        Confirmed channel IDs from SCHD1246.MP4 analysis:
          23 = session_time (seconds, 0–769)
          42 = RPM  (669–7339)
          43 = GPS speed km/h  (0–217)
          44 = gear  (0–7)
          51 = throttle %  (6–90)
           3 = altitude ft  (1159–1166)
          58 = brake (0–66)
          60 = lateral accel m/s²  (−14 to +37, mean≈0)
        GPS lat/lon are stored in bare records inside <hGPS> blocks
        (not in (S…) records) — see _parse_aim_bare_gps().
        """
        ch_vals: dict[int, list[float]] = defaultdict(list)
        for _, ch, val in records:
            ch_vals[ch].append(val)

        ch_stats: dict[int, dict] = {}
        for ch, vals in ch_vals.items():
            mn, mx = min(vals), max(vals)
            ch_stats[ch] = {
                "min":  mn,
                "max":  mx,
                "mean": sum(vals) / len(vals),
                "ptp":  mx - mn,
                "n":    len(vals),
            }

        used: set[int] = set()

        def _pick(cands: list[tuple[int, dict]], key_fn) -> int | None:
            if not cands:
                return None
            ch = key_fn(cands)
            used.add(ch)
            return ch

        # --- Session time ---
        # Monotonically increasing channel: largest ptp, min≈0, max > 60 s
        time_cands = [
            (ch, s) for ch, s in ch_stats.items()
            if s["min"] >= 0 and s["max"] > 60 and s["ptp"] > 60
        ]
        time_ch = _pick(time_cands, lambda c: max(c, key=lambda x: x[1]["ptp"])[0])

        # --- Speed km/h ---
        # ptp > 50, max ≤ 400, min = 0
        speed_cands = [
            (ch, s) for ch, s in ch_stats.items()
            if ch not in used
            and s["min"] >= 0 and s["max"] <= 400 and s["ptp"] > 50
        ]
        speed_ch = _pick(speed_cands, lambda c: max(c, key=lambda x: x[1]["max"])[0])

        # --- RPM ---
        # max > 2000, ptp > 2000
        rpm_cands = [
            (ch, s) for ch, s in ch_stats.items()
            if ch not in used and s["max"] > 2000 and s["ptp"] > 2000
        ]
        rpm_ch = _pick(rpm_cands, lambda c: max(c, key=lambda x: x[1]["max"])[0])

        # --- Gear ---
        # max ≤ 10, ptp ≤ 10, min = 0
        gear_cands = [
            (ch, s) for ch, s in ch_stats.items()
            if ch not in used
            and s["min"] >= 0 and s["max"] <= 10 and s["ptp"] <= 10
        ]
        gear_ch = _pick(gear_cands, lambda c: max(c, key=lambda x: x[1]["ptp"])[0])

        # --- Throttle % ---
        # min ≥ 0, max ≤ 105, ptp > 30, mean < 70
        throttle_cands = [
            (ch, s) for ch, s in ch_stats.items()
            if ch not in used
            and s["min"] >= 0 and s["max"] <= 105 and s["ptp"] > 30 and s["mean"] < 70
        ]
        throttle_ch = _pick(throttle_cands, lambda c: max(c, key=lambda x: x[1]["ptp"])[0])

        # --- Brake ---
        # min ≥ 0, max ≤ 200, ptp > 10, mean < 50, not already used
        brake_cands = [
            (ch, s) for ch, s in ch_stats.items()
            if ch not in used
            and s["min"] >= 0 and s["max"] <= 200 and s["ptp"] > 10 and s["mean"] < 50
        ]
        brake_ch = _pick(brake_cands, lambda c: max(c, key=lambda x: x[1]["ptp"])[0])

        # --- Altitude (feet or metres) ---
        # Positive, small ptp relative to mean (stable over session), mean > 100
        alt_cands = [
            (ch, s) for ch, s in ch_stats.items()
            if ch not in used
            and s["min"] > 0 and s["ptp"] < 0.2 * s["mean"] and s["mean"] > 100
        ]
        alt_ch = _pick(alt_cands, lambda c: min(c, key=lambda x: x[1]["ptp"])[0])

        # GPS lat/lon: NOT present in (S…) records for this firmware.
        # They are stored as int32 × 1e-7 in <hGPS> blocks; handled by _parse_aim_gps_blocks().
        lat_ch: int | None = None
        lon_ch: int | None = None

        assignments: dict[str, int | None] = {
            "time":     time_ch,
            "lat":      lat_ch,
            "lon":      lon_ch,
            "speed":    speed_ch,
            "rpm":      rpm_ch,
            "gear":     gear_ch,
            "throttle": throttle_ch,
            "brake":    brake_ch,
            "alt":      alt_ch,
        }
        return assignments, ch_stats

    # ------------------------------------------------------------------
    # Build Session
    # ------------------------------------------------------------------

    @staticmethod
    def _build_session(
        records: list[tuple[int, int, float]],
        assignments: dict[str, int | None],
        ch_stats: dict[int, dict],
        session: Session,
    ) -> None:
        from ..session import CH_RPM, CH_GEAR, CH_THROTTLE, CH_BRAKE

        # Collect per-channel time-series keyed by channel ID
        ch_ts:  dict[int, list[int]]   = defaultdict(list)
        ch_val: dict[int, list[float]] = defaultdict(list)
        for ts, ch, val in records:
            ch_ts[ch].append(ts)
            ch_val[ch].append(val)

        # --- Master time axis: use the session-timer channel's record TIMESTAMPS
        #     as the common interpolation grid so every channel ends up the same length.
        time_ch = assignments.get("time")
        if time_ch is not None and time_ch in ch_ts:
            t_ms_grid = np.array(ch_ts[time_ch], dtype=np.float64)
            t_arr     = np.array(ch_val[time_ch], dtype=np.float64)
        else:
            speed_ch = assignments.get("speed")
            ref_ch   = speed_ch if speed_ch is not None else next(iter(ch_ts), None)
            if ref_ch is None:
                return
            t_ms_grid = np.array(ch_ts[ref_ch], dtype=np.float64)
            t_arr     = (t_ms_grid - t_ms_grid[0]) / 1000.0

        if len(t_arr) == 0:
            return
        session.channels[CH_TIME] = t_arr

        def arr_interp(ch_id: int | None) -> np.ndarray | None:
            """Interpolate channel ch_id onto t_ms_grid so it matches CH_TIME length."""
            if ch_id is None or ch_id not in ch_val:
                return None
            src_ts  = np.array(ch_ts[ch_id],  dtype=np.float64)
            src_val = np.array(ch_val[ch_id], dtype=np.float64)
            if len(src_ts) < 2:
                return None
            return np.interp(t_ms_grid, src_ts, src_val)

        # --- GPS (lat/lon inserted later by _inject_gps_blocks) ---

        # --- Speed ---
        spd = arr_interp(assignments.get("speed"))
        if spd is not None:
            session.channels[CH_SPEED] = spd

        # --- Altitude  (feet → metres) ---
        alt = arr_interp(assignments.get("alt"))
        if alt is not None:
            session.channels[CH_HEIGHT] = alt * 0.3048  # ft → m

        # --- RPM ---
        rpm = arr_interp(assignments.get("rpm"))
        if rpm is not None:
            session.channels[CH_RPM] = rpm

        # --- Gear ---
        gear = arr_interp(assignments.get("gear"))
        if gear is not None:
            session.channels[CH_GEAR] = gear

        # --- Throttle ---
        thr = arr_interp(assignments.get("throttle"))
        if thr is not None:
            session.channels[CH_THROTTLE] = thr

        # --- Brake (scale 0–brk_max → 0–100 %) ---
        brk = arr_interp(assignments.get("brake"))
        if brk is not None:
            brk_max = ch_stats.get(assignments["brake"], {}).get("max", 100.0) or 100.0
            session.channels[CH_BRAKE] = np.clip(brk / brk_max * 100.0, 0, 100)

        session.metadata["telemetry"] = "AIM"
        session.metadata["aim_channel_map"] = {
            k: v for k, v in assignments.items() if v is not None
        }

    # ------------------------------------------------------------------
    # GPS from <hGPS> blocks (int32 × 1e-7 at fixed offsets)
    # ------------------------------------------------------------------
    # AIM stores GPS lat/lon as int32 × 1e-7 degrees at FIXED byte offsets
    # inside <hGPS\x00 SIZE VER> block content:
    #   content[4:8]   = int32 LE latitude  × 1e7
    #   content[12:16] = int32 LE longitude × 1e7
    # Verified from SCHD1246.MP4:
    #   content[4:8]  = 0x22C14220 = 582,968,352 → 58.2968°N
    #   content[12:16]= 0x0D03096A = 218,499,434 → 21.8499°E
    # Block header: tag(6) + size_LE(4) + version(1) + '>'(1) = 12 bytes

    @staticmethod
    def _parse_aim_gps_blocks(data: bytes) -> tuple[list[float], list[float]]:
        """
        Scan *data* for <hGPS\\x00> blocks and extract lat/lon as int32 × 1e-7.
        Returns (lats, lons) lists of float degrees.
        """
        tag = b'\x3c\x68\x47\x50\x53\x00'  # b'<hGPS\x00'
        lats: list[float] = []
        lons: list[float] = []
        i = 0
        n = len(data)
        while i < n - 20:
            if data[i:i+6] == tag:
                block_size = struct.unpack_from("<I", data, i + 6)[0]
                content_start = i + 12  # 6-byte tag + 4-byte size + 1 ver + '>'
                content_end = min(i + block_size, n)
                if content_end - content_start >= 16:
                    lat_raw = struct.unpack_from("<i", data, content_start + 4)[0]
                    lon_raw = struct.unpack_from("<i", data, content_start + 12)[0]
                    lat = lat_raw / 1e7
                    lon = lon_raw / 1e7
                    if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat != 0.0 or lon != 0.0):
                        lats.append(lat)
                        lons.append(lon)
                i = max(i + 1, content_end)
            else:
                i += 1
        return lats, lons

    @staticmethod
    def _inject_gps_blocks(
        lats: list[float],
        lons: list[float],
        session: Session,
    ) -> None:
        """Add GPS lat/lon arrays resampled to match CH_TIME length."""
        if not lats or not lons:
            return
        n_target = len(session.channels.get(CH_TIME, []))
        if n_target == 0 or len(lats) == n_target:
            session.channels[CH_LAT] = np.array(lats, dtype=np.float64)
            session.channels[CH_LON] = np.array(lons, dtype=np.float64)
        else:
            # GPS blocks are sampled at a different rate; resample to telemetry grid
            src = np.linspace(0.0, 1.0, len(lats))
            tgt = np.linspace(0.0, 1.0, n_target)
            session.channels[CH_LAT] = np.interp(tgt, src, lats)
            session.channels[CH_LON] = np.interp(tgt, src, lons)

    # ------------------------------------------------------------------
    # GoPro GPMD fallback
    # ------------------------------------------------------------------

    def _parse_gopro_track(
        self, fp: BinaryIO, moov_start: int, moov_end: int, session: Session
    ) -> bool:
        for trak in iter_boxes(fp, moov_start, moov_end):
            if trak.fourcc != "trak":
                continue
            ts = trak.offset + 8
            te = trak.offset + trak.size
            hdlr = find_box(fp, ["mdia", "hdlr"], ts, te)
            if not hdlr or b"GoPro MET" not in hdlr.data:
                continue
            raw = self._read_track_data(fp, ts, te)
            if raw:
                return self._parse_gpmd(raw, session)
        return False

    @staticmethod
    def _parse_gpmd(data: bytes, session: Session) -> bool:
        pos = 0
        lats: list[float] = []
        lons: list[float] = []
        speeds: list[float] = []
        altitudes: list[float] = []

        while pos < len(data) - 8:
            key = data[pos:pos + 4].decode("latin-1")
            size = data[pos + 5]
            repeat = struct.unpack_from(">H", data, pos + 6)[0]
            pos += 8
            total = size * repeat
            payload = data[pos: pos + total]
            pos += total
            if pos % 4:
                pos += 4 - (pos % 4)

            if key == "GPS5" and size == 20:
                for i in range(repeat):
                    lat, lon, alt, spd, _ = struct.unpack_from(">iiiii", payload, i * 20)
                    lats.append(lat / 1e7)
                    lons.append(lon / 1e7)
                    altitudes.append(alt / 1000.0)
                    speeds.append(spd / 1000.0 * 3.6)

        if not lats:
            return False

        n = len(lats)
        t = np.linspace(0.0, n / 18.0, n)
        session.channels[CH_TIME]  = t
        session.channels[CH_LAT]   = np.array(lats)
        session.channels[CH_LON]   = np.array(lons)
        session.channels[CH_SPEED] = np.array(speeds)
        session.channels[CH_HEIGHT] = np.array(altitudes)
        session.metadata["telemetry"] = "GoPro GPMD"
        return True

    # ------------------------------------------------------------------
    # Generic udta GPS
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_udta_gps(
        fp: BinaryIO, moov_start: int, moov_end: int, session: Session
    ) -> None:
        udta = find_box(fp, ["udta"], moov_start, moov_end)
        if not udta:
            return
        text = udta.data.decode("utf-8", errors="ignore")
        try:
            m = json.loads(text)
            gps = m.get("gps", [])
            if gps:
                session.channels[CH_LAT]  = np.array([p["lat"] for p in gps])
                session.channels[CH_LON]  = np.array([p["lon"] for p in gps])
                session.channels[CH_TIME] = np.array([p.get("t", i) for i, p in enumerate(gps)])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI: diagnostic — channel statistics
# ---------------------------------------------------------------------------

def _diagnose(path: str) -> None:
    """Print full channel statistics for an AIM Smartycam3 MP4 file."""
    import sys
    print(f"\nDiagnosing: {os.path.basename(path)}")
    print(f"File size : {os.path.getsize(path):,} bytes\n")

    parser = Mp4Parser()
    with open(path, "rb") as fp:
        fp.seek(0, 2)
        file_size = fp.tell()

        moov = find_box(fp, ["moov"], 0, file_size)
        if not moov:
            print("ERROR: no moov box found")
            sys.exit(1)

        moov_s = moov.offset + 8
        moov_e = moov.offset + moov.size

        bounds = parser._find_aim_meta_track(fp, moov_s, moov_e)
        if not bounds:
            print("No AIM MetaAimHandler track found.")
            sys.exit(1)

        ts, te = bounds
        print("AIM meta track found.")
        raw = parser._read_track_data(fp, ts, te)

    if not raw:
        print("Could not read track data.")
        return

    print(f"Total raw bytes read : {len(raw):,}")
    records = Mp4Parser._parse_aim_records(raw)
    print(f"(S…) records parsed  : {len(records):,}\n")

    if not records:
        print("No records found — check that the file has AIM telemetry.")
        return

    assignments, ch_stats = Mp4Parser._auto_assign_channels(records)

    # Print sorted by channel ID
    print(f"{'Ch':>5}  {'N':>7}  {'Min':>12}  {'Max':>12}  {'Mean':>12}  {'PTP':>10}  Auto")
    print("-" * 70)
    inv = {v: k for k, v in assignments.items() if v is not None}
    for ch in sorted(ch_stats):
        s = ch_stats[ch]
        label = inv.get(ch, "")
        print(
            f"{ch:>5}  {s['n']:>7}  {s['min']:>12.4f}  {s['max']:>12.4f}"
            f"  {s['mean']:>12.4f}  {s['ptp']:>10.4f}  {label}"
        )

    print("\nAuto-assigned channels:")
    for name, ch_id in assignments.items():
        if ch_id is not None:
            s = ch_stats[ch_id]
            print(f"  {name:8s} → channel {ch_id}  (mean={s['mean']:.4f}, ptp={s['ptp']:.4f})")
        else:
            print(f"  {name:8s} → NOT FOUND")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m src.data.parsers.mp4_parser <file.mp4>")
        sys.exit(1)
    _diagnose(sys.argv[1])
