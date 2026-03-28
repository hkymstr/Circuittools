"""
CSV parser — supports:
  • Race Studio 3 CSV exports from AIM devices (Smartycam3, etc.)
  • Generic GPS/telemetry CSV with auto-detected column names
"""
from __future__ import annotations

import os
import re
import numpy as np
import pandas as pd

from .base import BaseParser
from ..session import (
    Session,
    CH_TIME, CH_LAT, CH_LON, CH_SPEED, CH_HEADING,
    CH_AX, CH_AY, CH_AZ, CH_RPM, CH_GEAR, CH_THROTTLE, CH_BRAKE, CH_HEIGHT,
)

# Mapping from common CSV column name variants → canonical channel name.
# Matching is case-insensitive, and leading/trailing spaces are stripped.
_COLUMN_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^time$|^timestamp$|^t$|^elapsed"), CH_TIME),
    (re.compile(r"^(gps_)?lat(itude)?$"), CH_LAT),
    (re.compile(r"^(gps_)?lon(gitude|g)?$"), CH_LON),
    (re.compile(r"^(gps_)?speed$|^velocity$|^spd$"), CH_SPEED),
    (re.compile(r"^(gps_)?head(ing)?$|^course$|^bearing$"), CH_HEADING),
    (re.compile(r"^acc?el?(_?x|_?long)$|^long(itudinal)?_?[ag]$"), CH_AX),
    (re.compile(r"^acc?el?(_?y|_?lat)$|^lat(eral)?_?[ag]$"), CH_AY),
    (re.compile(r"^acc?el?(_?z|_?vert)$|^vert(ical)?_?[ag]$"), CH_AZ),
    (re.compile(r"^rpm$|^engine_?speed$"), CH_RPM),
    (re.compile(r"^gear$"), CH_GEAR),
    (re.compile(r"^thr(ottle)?(%|_pos)?$|^acc?elerat(or|ion)_pos"), CH_THROTTLE),
    (re.compile(r"^brake(%|_pos|_pressure)?$"), CH_BRAKE),
    (re.compile(r"^(gps_)?alt(itude)?$|^height$|^elevation$"), CH_HEIGHT),
]


def _map_column(name: str) -> str | None:
    """Return canonical channel name for a CSV column, or None if unknown."""
    clean = name.strip().lower()
    for pattern, canonical in _COLUMN_MAP:
        if pattern.match(clean):
            return canonical
    return None


def _detect_header_rows(path: str) -> int:
    """
    Race Studio 3 exports have a metadata preamble before the data table.
    Return the number of rows to skip so that pandas lands on the column row.
    """
    skip = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            stripped = line.strip()
            if not stripped:
                skip = i + 1
                continue
            # If this line looks like a header row (first token not numeric)
            first = stripped.split(",")[0].strip()
            try:
                float(first)
                # Found a numeric row — the previous non-numeric row was the header
                return skip
            except ValueError:
                skip = i
    return 0


class CsvParser(BaseParser):

    def can_parse(self, path: str) -> bool:
        ext = os.path.splitext(path)[1].lower()
        return ext in (".csv", ".txt")

    def parse(self, path: str) -> Session:
        session = Session()
        session.source_file = path

        # --- find where data starts ---
        header_row = _detect_header_rows(path)

        # Try to sniff separator
        with open(path, encoding="utf-8", errors="replace") as f:
            for _ in range(header_row):
                f.readline()
            sample = f.read(4096)
        sep = "," if sample.count(",") >= sample.count(";") else ";"

        df = pd.read_csv(
            path,
            sep=sep,
            header=header_row,
            skip_blank_lines=True,
            encoding="utf-8",
            errors="replace",
            low_memory=False,
        )

        # --- collect metadata from preamble ---
        if header_row > 0:
            with open(path, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i >= header_row:
                        break
                    if "," in line:
                        k, *rest = line.strip().split(",", 1)
                        if k.strip() and rest:
                            session.metadata[k.strip()] = rest[0].strip()

        # --- map columns to canonical names ---
        mapped: dict[str, str] = {}   # csv_col → canonical
        for col in df.columns:
            canon = _map_column(str(col))
            if canon and canon not in mapped.values():
                mapped[col] = canon

        if not mapped:
            raise ValueError(
                "No recognisable data columns found. "
                "Please export from Race Studio 3 with standard column names."
            )

        for csv_col, canon in mapped.items():
            try:
                arr = pd.to_numeric(df[csv_col], errors="coerce").to_numpy(dtype=float)
                session.channels[canon] = arr
            except Exception:
                pass

        # Also store unrecognised numeric columns under their original names
        for col in df.columns:
            if col not in mapped:
                try:
                    arr = pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)
                    if not np.all(np.isnan(arr)):
                        session.channels[col.strip()] = arr
                except Exception:
                    pass

        # --- synthesise time channel if missing ---
        if CH_TIME not in session.channels:
            n = len(next(iter(session.channels.values())))
            # Try to determine sample rate from the data length; default 20 Hz
            session.channels[CH_TIME] = np.arange(n, dtype=float) / 20.0

        # --- ensure time starts at 0 ---
        t = session.channels[CH_TIME]
        session.channels[CH_TIME] = t - t[0]

        # --- convert speed m/s → km/h if values look like m/s ---
        if CH_SPEED in session.channels:
            spd = session.channels[CH_SPEED]
            if np.nanmax(spd) < 100:   # likely m/s
                session.channels[CH_SPEED] = spd * 3.6

        return session
