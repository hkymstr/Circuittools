"""
Delta-T calculation — time gain/loss of one lap vs a reference lap.

Delta-T is the central analysis channel in Vbox Circuit Tools.
A negative value means the comparison lap is ahead (faster) at that point;
positive means it is behind (slower).

The calculation is distance-based:
  1. Convert each lap's GPS trace to cumulative distance along track.
  2. For every distance point in the comparison lap, interpolate the time
     at that distance in the reference lap.
  3. delta_t[i] = time_comparison[i] - time_reference_at_same_distance[i]

This is independent of minor GPS position noise because it uses the
cumulative distance (scalar) rather than raw lat/lon matching.
"""
from __future__ import annotations

import numpy as np
from .session import Session, Lap, CH_LAT, CH_LON, CH_TIME


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cumulative_distance(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """
    Return cumulative great-circle distance in metres along a GPS path.
    Output[0] == 0.  Shape matches input.
    """
    if len(lat) < 2:
        return np.zeros(len(lat))

    lat_r = np.radians(lat)
    lon_r = np.radians(lon)
    dlat = np.diff(lat_r)
    dlon = np.diff(lon_r)

    lat_mid = (lat_r[:-1] + lat_r[1:]) / 2.0
    # Equirectangular approximation — fast and accurate for short segments
    dx = dlon * np.cos(lat_mid) * 6_371_000.0
    dy = dlat * 6_371_000.0
    seg = np.sqrt(dx ** 2 + dy ** 2)
    return np.concatenate([[0.0], np.cumsum(seg)])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_delta_t(
    session: Session,
    reference_lap: Lap,
    comparison_lap: Lap,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Compute Delta-T between two laps.

    Returns
    -------
    dist_ref : np.ndarray
        Cumulative distance array for the *reference* lap (metres, starts at 0).
    delta_t  : np.ndarray
        Time delta (seconds) at each sample of the comparison lap.
        Negative = comparison is ahead (faster); positive = behind (slower).
        Sampled at the same cadence as the *comparison* lap.
    """
    if not session.has_gps():
        raise ValueError("Session has no GPS data; cannot compute Delta-T")

    ref_ch   = session.lap_channels(reference_lap)
    comp_ch  = session.lap_channels(comparison_lap)

    ref_lat  = ref_ch[CH_LAT]
    ref_lon  = ref_ch[CH_LON]
    ref_time = ref_ch[CH_TIME] - ref_ch[CH_TIME][0]   # relative to lap start

    comp_lat  = comp_ch[CH_LAT]
    comp_lon  = comp_ch[CH_LON]
    comp_time = comp_ch[CH_TIME] - comp_ch[CH_TIME][0]

    # Cumulative distances
    dist_ref  = _cumulative_distance(ref_lat,  ref_lon)
    dist_comp = _cumulative_distance(comp_lat, comp_lon)

    # Normalise to lap length (handle minor GPS length differences)
    ref_lap_len  = dist_ref[-1]  if dist_ref[-1]  > 0 else 1.0
    comp_lap_len = dist_comp[-1] if dist_comp[-1] > 0 else 1.0

    dist_ref_norm  = dist_ref  / ref_lap_len
    dist_comp_norm = dist_comp / comp_lap_len

    # For each distance fraction in comparison lap, find time in reference lap
    ref_time_at_comp_dist = np.interp(dist_comp_norm, dist_ref_norm, ref_time)

    delta_t = comp_time - ref_time_at_comp_dist

    return dist_comp * (ref_lap_len / comp_lap_len), delta_t


def compute_all_delta_t(
    session: Session,
    reference_lap: Lap,
) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """
    Compute Delta-T between the reference lap and every other lap.

    Returns a dict mapping  lap_number → (dist, delta_t).
    The reference lap itself is excluded.
    """
    results: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for lap in session.laps:
        if lap.number == reference_lap.number:
            continue
        try:
            dist, dt = compute_delta_t(session, reference_lap, lap)
            results[lap.number] = (dist, dt)
        except Exception:
            pass
    return results
