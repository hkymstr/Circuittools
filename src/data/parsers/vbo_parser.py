"""
VBOX .vbo file parser.

The VBO format is a plain-text file with sections:
  [header]        — free-form metadata
  [channel list]  — one channel name per line
  [column names]  — optional friendly names
  [data]          — space-separated rows, one per sample

VBOX channel names and units:
  sats, time (UTC HH:MM:SS.ss), lat (deg*60), long (deg*60),
  velocity (km/h), heading (°), height (m), vertical-velocity (m/s),
  session-type-identifier, glonass-sats,
  imu-acc-x, imu-acc-y, imu-acc-z (G)
"""
from __future__ import annotations

import os
import re
import numpy as np

from .base import BaseParser
from ..session import (
    Session,
    CH_TIME, CH_LAT, CH_LON, CH_SPEED, CH_HEADING,
    CH_AX, CH_AY, CH_AZ, CH_HEIGHT,
)

_VBO_CHANNEL_MAP = {
    "time":               CH_TIME,
    "lat":                CH_LAT,
    "long":               CH_LON,
    "longitude":          CH_LON,
    "velocity":           CH_SPEED,
    "heading":            CH_HEADING,
    "height":             CH_HEIGHT,
    "imu-acc-x":          CH_AX,
    "imu-acc-y":          CH_AY,
    "imu-acc-z":          CH_AZ,
    "accelerationx":      CH_AX,
    "accelerationy":      CH_AY,
    "accelerationz":      CH_AZ,
}


def _vbo_time_to_seconds(value: float) -> float:
    """
    VBO stores UTC as HHMMSS.ss packed into a float.
    Convert to seconds-of-day.
    """
    hhmm = int(value / 100)
    ss = value - hhmm * 100
    hh = hhmm // 100
    mm = hhmm % 100
    return hh * 3600 + mm * 60 + ss


class VboParser(BaseParser):

    def can_parse(self, path: str) -> bool:
        ext = os.path.splitext(path)[1].lower()
        return ext == ".vbo"

    def parse(self, path: str) -> Session:
        session = Session()
        session.source_file = path

        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read()

        sections = self._split_sections(raw)

        # --- metadata ---
        for line in sections.get("header", "").splitlines():
            line = line.strip()
            if "=" in line:
                k, v = line.split("=", 1)
                session.metadata[k.strip()] = v.strip()
            elif line:
                session.metadata.setdefault("info", "")
                session.metadata["info"] += line + " "

        # --- channel list ---
        raw_channels = [
            c.strip().lower()
            for c in sections.get("channel list", "").splitlines()
            if c.strip()
        ]

        # --- data rows ---
        data_text = sections.get("data", "")
        rows: list[list[float]] = []
        for line in data_text.splitlines():
            line = line.strip()
            if not line or line.startswith("["):
                continue
            parts = line.split()
            if len(parts) != len(raw_channels):
                continue
            try:
                rows.append([float(p) for p in parts])
            except ValueError:
                pass

        if not rows:
            raise ValueError("No data rows found in VBO file.")

        arr = np.array(rows, dtype=float)   # shape (N, C)

        # --- build channels ---
        for i, raw_name in enumerate(raw_channels):
            canon = _VBO_CHANNEL_MAP.get(raw_name, raw_name)
            session.channels[canon] = arr[:, i]

        # --- convert VBO lat/lon from (degrees * 60) to decimal degrees ---
        if CH_LAT in session.channels:
            session.channels[CH_LAT] = session.channels[CH_LAT] / 60.0
        if CH_LON in session.channels:
            session.channels[CH_LON] = session.channels[CH_LON] / 60.0

        # --- convert packed UTC time to elapsed seconds ---
        if CH_TIME in session.channels:
            t = np.array([
                _vbo_time_to_seconds(v) for v in session.channels[CH_TIME]
            ])
            t = t - t[0]
            # Handle midnight rollover
            t[t < 0] += 86400
            session.channels[CH_TIME] = t
        else:
            n = arr.shape[0]
            session.channels[CH_TIME] = np.arange(n, dtype=float) / 20.0

        return session

    # ------------------------------------------------------------------

    @staticmethod
    def _split_sections(text: str) -> dict[str, str]:
        """Split a VBO file into named sections."""
        sections: dict[str, str] = {}
        current: str | None = None
        buf: list[str] = []

        for line in text.splitlines():
            m = re.match(r"^\[(.+?)\]", line.strip())
            if m:
                if current is not None:
                    sections[current] = "\n".join(buf)
                current = m.group(1).lower()
                buf = []
            else:
                buf.append(line)

        if current is not None:
            sections[current] = "\n".join(buf)

        return sections
