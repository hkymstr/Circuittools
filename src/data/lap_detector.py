"""
Lap detection for circuit sessions.

Two strategies:
  1. Finish-line crossing  — requires a known start/finish GPS coordinate.
     Detects when the vehicle crosses an imaginary line perpendicular to the
     track at the finish point.

  2. Speed-based auto-detect — finds the slowest point of each lap (e.g.,
     the first corner after the straight) and uses repeated passes through
     that point to segment laps.  Works without any prior knowledge of the
     circuit.
"""
from __future__ import annotations

import numpy as np
from .session import Session, Lap, CH_TIME, CH_LAT, CH_LON, CH_SPEED


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_laps(
    session: Session,
    finish_lat: float | None = None,
    finish_lon: float | None = None,
    min_lap_seconds: float = 30.0,
) -> list[Lap]:
    """
    Detect laps in the session and store them in session.laps.

    Parameters
    ----------
    session         The session to analyse.
    finish_lat/lon  Known finish line GPS coords (optional).  If provided, lap
                    detection uses finish-line crossings.  Otherwise, an
                    automatic method is used.
    min_lap_seconds Minimum valid lap time.  Crossings closer together than
                    this are ignored.
    """
    if not session.has_gps():
        return []

    if finish_lat is not None and finish_lon is not None:
        laps = _detect_by_finish_line(session, finish_lat, finish_lon, min_lap_seconds)
    else:
        laps = _detect_auto(session, min_lap_seconds)

    session.laps = laps
    return laps


# ---------------------------------------------------------------------------
# Strategy 1: Finish-line crossing
# ---------------------------------------------------------------------------

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in metres."""
    R = 6_371_000.0
    d_lat = np.radians(lat2 - lat1)
    d_lon = np.radians(lon2 - lon1)
    a = (
        np.sin(d_lat / 2) ** 2
        + np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) * np.sin(d_lon / 2) ** 2
    )
    return 2 * R * np.arcsin(np.sqrt(a))


def _detect_by_finish_line(
    session: Session,
    finish_lat: float,
    finish_lon: float,
    min_lap_seconds: float,
) -> list[Lap]:
    """Detect laps by proximity to a known finish-line coordinate."""
    lat = session.channels[CH_LAT]
    lon = session.channels[CH_LON]
    t = session.channels[CH_TIME]

    # Compute distance to finish for every sample
    R = 6_371_000.0
    dlat = np.radians(lat - finish_lat)
    dlon = np.radians(lon - finish_lon)
    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(np.radians(finish_lat)) * np.cos(np.radians(lat)) * np.sin(dlon / 2) ** 2
    )
    dist = 2 * R * np.arcsin(np.sqrt(a))

    # Finish-line trigger radius (metres) — tighten if laps are short
    radius = 30.0
    while radius > 5.0:
        crossings = _find_crossings(dist, radius, t, min_lap_seconds)
        if len(crossings) >= 2:
            break
        radius -= 5.0

    if len(crossings) < 2:
        return []

    return _crossings_to_laps(crossings, t)


def _find_crossings(
    dist: np.ndarray,
    radius: float,
    t: np.ndarray,
    min_lap_seconds: float,
) -> list[int]:
    """Return indices where dist goes below radius (minimum trigger)."""
    inside = dist < radius
    # Find leading edges (False→True transitions)
    edges = np.where(np.diff(inside.astype(int)) == 1)[0] + 1

    crossings: list[int] = []
    last_t = -min_lap_seconds
    for idx in edges:
        if t[idx] - last_t >= min_lap_seconds:
            crossings.append(int(idx))
            last_t = t[idx]

    return crossings


def _crossings_to_laps(crossings: list[int], t: np.ndarray) -> list[Lap]:
    laps = []
    for i in range(len(crossings) - 1):
        start = crossings[i]
        end = crossings[i + 1]
        laps.append(
            Lap(
                number=i + 1,
                start_idx=start,
                end_idx=end,
                start_time=float(t[start]),
                end_time=float(t[end]),
            )
        )
    return laps


# ---------------------------------------------------------------------------
# Strategy 2: Auto-detect without finish line
# ---------------------------------------------------------------------------

def _detect_auto(session: Session, min_lap_seconds: float) -> list[Lap]:
    """
    Heuristic lap detection:
      1. Find the GPS point that is visited most repeatedly (the pit-lane /
         start-finish area tends to cluster).
      2. Use proximity to that point as the lap trigger.
    """
    lat = session.channels[CH_LAT]
    lon = session.channels[CH_LON]
    t = session.channels[CH_TIME]

    if len(t) < 100:
        return []

    # Downsample for speed
    step = max(1, len(t) // 2000)
    lat_s = lat[::step]
    lon_s = lon[::step]

    # Find the point with maximum density of other points within 100 m
    best_idx = 0
    best_count = 0
    sample_n = min(200, len(lat_s))
    idxs = np.linspace(0, len(lat_s) - 1, sample_n, dtype=int)
    for i in idxs:
        dlat = np.radians(lat_s - lat_s[i])
        dlon = np.radians(lon_s - lon_s[i])
        a = (
            np.sin(dlat / 2) ** 2
            + np.cos(np.radians(lat_s[i])) * np.cos(np.radians(lat_s)) * np.sin(dlon / 2) ** 2
        )
        d = 2 * 6_371_000 * np.arcsin(np.sqrt(a.clip(0, 1)))
        count = int(np.sum(d < 100))
        if count > best_count:
            best_count = count
            best_idx = i

    finish_lat = float(lat_s[best_idx])
    finish_lon = float(lon_s[best_idx])

    return _detect_by_finish_line(session, finish_lat, finish_lon, min_lap_seconds)


# ---------------------------------------------------------------------------
# Utility: set finish line from user click on track map
# ---------------------------------------------------------------------------

def set_finish_from_track_click(
    session: Session,
    click_lat: float,
    click_lon: float,
) -> list[Lap]:
    """Re-run lap detection using a user-specified finish line coordinate."""
    return detect_laps(session, finish_lat=click_lat, finish_lon=click_lon)
