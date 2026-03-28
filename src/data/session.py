"""
Core data models for circuit tool sessions.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import numpy as np


# Standard channel names used throughout the application
CH_TIME = "time"           # seconds from session start
CH_LAT = "latitude"        # decimal degrees
CH_LON = "longitude"       # decimal degrees
CH_SPEED = "speed"         # km/h
CH_HEADING = "heading"     # degrees 0-360
CH_AX = "accel_x"          # longitudinal G (positive = braking)
CH_AY = "accel_y"          # lateral G (positive = right)
CH_AZ = "accel_z"          # vertical G
CH_RPM = "rpm"
CH_GEAR = "gear"
CH_THROTTLE = "throttle"   # 0-100 %
CH_BRAKE = "brake"         # 0-100 %
CH_HEIGHT = "height"       # metres above sea level

# Human-readable labels and units for channels
CHANNEL_META: dict[str, tuple[str, str]] = {
    CH_TIME:     ("Time",         "s"),
    CH_LAT:      ("Latitude",     "°"),
    CH_LON:      ("Longitude",    "°"),
    CH_SPEED:    ("Speed",        "km/h"),
    CH_HEADING:  ("Heading",      "°"),
    CH_AX:       ("Long. G",      "G"),
    CH_AY:       ("Lat. G",       "G"),
    CH_AZ:       ("Vert. G",      "G"),
    CH_RPM:      ("RPM",          "rpm"),
    CH_GEAR:     ("Gear",         ""),
    CH_THROTTLE: ("Throttle",     "%"),
    CH_BRAKE:    ("Brake",        "%"),
    CH_HEIGHT:   ("Altitude",     "m"),
}

# Default display order in the channel panel
DEFAULT_CHANNEL_ORDER = [
    CH_SPEED, CH_AY, CH_AX, CH_THROTTLE, CH_BRAKE, CH_RPM, CH_GEAR,
]


@dataclass
class Lap:
    """A single timed lap in a session."""
    number: int
    start_idx: int       # index into session data arrays
    end_idx: int
    start_time: float    # seconds
    end_time: float      # seconds

    @property
    def lap_time(self) -> float:
        return self.end_time - self.start_time

    @property
    def lap_time_str(self) -> str:
        total = self.lap_time
        minutes = int(total // 60)
        seconds = total % 60
        return f"{minutes}:{seconds:06.3f}"


class Session:
    """
    Holds all data for one recorded session.

    Channels are stored as equal-length numpy arrays indexed by the same
    sample number.  ``channels[CH_TIME]`` is always present.
    """

    def __init__(self) -> None:
        self.channels: dict[str, np.ndarray] = {}
        self.laps: list[Lap] = []
        self.metadata: dict[str, str] = {}
        self.video_path: Optional[str] = None
        self.video_offset: float = 0.0   # seconds: data_time = video_time + offset
        self.source_file: Optional[str] = None

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def time(self) -> np.ndarray:
        return self.channels[CH_TIME]

    @property
    def duration(self) -> float:
        t = self.channels.get(CH_TIME)
        if t is None or len(t) == 0:
            return 0.0
        return float(t[-1] - t[0])

    @property
    def sample_count(self) -> int:
        t = self.channels.get(CH_TIME)
        return 0 if t is None else len(t)

    def has_gps(self) -> bool:
        return CH_LAT in self.channels and CH_LON in self.channels

    def has_video(self) -> bool:
        return self.video_path is not None

    def channel_names(self) -> list[str]:
        return list(self.channels.keys())

    # ------------------------------------------------------------------
    # Index / interpolation helpers
    # ------------------------------------------------------------------

    def idx_at_time(self, t: float) -> int:
        """Return the nearest sample index for a given time in seconds."""
        arr = self.channels.get(CH_TIME)
        if arr is None or len(arr) == 0:
            return 0
        return int(np.searchsorted(arr, t, side="left").clip(0, len(arr) - 1))

    def value_at_time(self, channel: str, t: float) -> Optional[float]:
        arr = self.channels.get(channel)
        if arr is None or len(arr) == 0:
            return None
        idx = self.idx_at_time(t)
        return float(arr[idx])

    # ------------------------------------------------------------------
    # GPS helpers
    # ------------------------------------------------------------------

    def gps_xy(self) -> tuple[np.ndarray, np.ndarray]:
        """
        Convert lat/lon to approximate local XY metres using equirectangular
        projection centred on the mean position.
        """
        lat = self.channels[CH_LAT]
        lon = self.channels[CH_LON]
        lat_rad = np.radians(lat)
        lat0 = np.radians(np.mean(lat))
        R = 6_371_000.0
        x = R * np.radians(lon - np.mean(lon)) * np.cos(lat0)
        y = R * (lat_rad - lat0)
        return x, y

    # ------------------------------------------------------------------
    # Speed helpers
    # ------------------------------------------------------------------

    @property
    def max_speed(self) -> float:
        s = self.channels.get(CH_SPEED)
        return float(np.nanmax(s)) if s is not None and len(s) else 0.0

    # ------------------------------------------------------------------
    # Lap helpers
    # ------------------------------------------------------------------

    @property
    def best_lap(self) -> Optional[Lap]:
        if not self.laps:
            return None
        return min(self.laps, key=lambda l: l.lap_time)

    def lap_channels(self, lap: Lap) -> dict[str, np.ndarray]:
        """Return channel slices for a specific lap."""
        return {
            name: arr[lap.start_idx:lap.end_idx]
            for name, arr in self.channels.items()
        }
