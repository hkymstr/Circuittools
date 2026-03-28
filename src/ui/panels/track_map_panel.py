"""
Track map panel — 2D GPS overhead view with speed-coded colour.

Features:
  • Full track plotted as coloured line (speed gradient: blue→green→red)
  • Moving position marker
  • Per-lap colour overlays for comparison
  • Click on map to set finish line for lap detection
  • Zoom/pan via pyqtgraph mouse interaction
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal, QPointF
from PyQt6.QtGui import QColor, QPen
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton

from ...data.session import Session, Lap, CH_SPEED, CH_LAT, CH_LON
from ..style import (
    ACCENT_GREEN, ACCENT_ORANGE, ACCENT_BLUE, BG_DARK, BG_PANEL, BORDER,
    TEXT_SECONDARY,
)

# Speed colourmap: slow=blue, mid=green/yellow, fast=red
_SPEED_COLOURS = [
    (0.00, (  0,  64, 255)),   # blue
    (0.33, (  0, 200,  80)),   # green
    (0.60, (255, 200,   0)),   # yellow
    (1.00, (255,  20,   0)),   # red
]

_COMPARISON_COLOURS = [
    ACCENT_BLUE,
    "#ff00ff",
    "#00ffff",
    "#ff8000",
]


def _speed_colour(fraction: float) -> tuple[int, int, int]:
    """Interpolate speed colour given fraction in [0, 1]."""
    fraction = float(np.clip(fraction, 0.0, 1.0))
    for i in range(len(_SPEED_COLOURS) - 1):
        t0, c0 = _SPEED_COLOURS[i]
        t1, c1 = _SPEED_COLOURS[i + 1]
        if t0 <= fraction <= t1:
            f = (fraction - t0) / (t1 - t0)
            r = int(c0[0] + f * (c1[0] - c0[0]))
            g = int(c0[1] + f * (c1[1] - c0[1]))
            b = int(c0[2] + f * (c1[2] - c0[2]))
            return r, g, b
    return _SPEED_COLOURS[-1][1]


def _session_to_xy(session: Session) -> tuple[np.ndarray, np.ndarray]:
    """Return track X/Y coordinates in metres centred on the track."""
    return session.gps_xy()


class TrackMapPanel(QWidget):
    """Interactive GPS track map panel."""

    finish_line_set = pyqtSignal(float, float)   # lat, lon

    def __init__(self, playback, parent=None) -> None:
        super().__init__(parent)
        self._playback = playback
        self._session: Session | None = None
        self._setting_finish = False

        self._build_ui()
        playback.session_loaded.connect(self._on_session_loaded)
        playback.time_changed.connect(self._on_time_changed)
        playback.laps_updated.connect(self._on_laps_updated)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # toolbar
        toolbar = QWidget()
        toolbar.setFixedHeight(30)
        toolbar.setStyleSheet(f"background: #1a1a1a; border-bottom: 1px solid #333;")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(8, 0, 8, 0)
        tb_layout.setSpacing(6)

        lbl = QLabel("Track Map")
        lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 600; letter-spacing: 0.5px; "
            f"color: {TEXT_SECONDARY};"
        )
        tb_layout.addWidget(lbl)
        tb_layout.addStretch()

        self._btn_finish = QPushButton("Set Finish Line")
        self._btn_finish.setCheckable(True)
        self._btn_finish.setFixedHeight(20)
        self._btn_finish.setStyleSheet("font-size: 10px; padding: 1px 8px;")
        self._btn_finish.toggled.connect(self._toggle_finish_mode)
        tb_layout.addWidget(self._btn_finish)

        self._btn_reset = QPushButton("Reset View")
        self._btn_reset.setFixedHeight(20)
        self._btn_reset.setStyleSheet("font-size: 10px; padding: 1px 8px;")
        self._btn_reset.clicked.connect(self._reset_view)
        tb_layout.addWidget(self._btn_reset)

        layout.addWidget(toolbar)

        # pyqtgraph plot
        pg.setConfigOptions(antialias=True, background=BG_DARK)
        self._plot = pg.PlotWidget()
        self._plot.setAspectLocked(True)
        self._plot.hideAxis("left")
        self._plot.hideAxis("bottom")
        self._plot.setMouseEnabled(True, True)
        self._plot.getViewBox().setDefaultPadding(0.05)

        self._plot.scene().sigMouseClicked.connect(self._on_map_click)
        layout.addWidget(self._plot)

        # legend / status
        self._status = QLabel()
        self._status.setStyleSheet(
            f"background: #1a1a1a; border-top: 1px solid #333; "
            f"padding: 3px 8px; font-size: 10px; color: {TEXT_SECONDARY};"
        )
        self._status.setFixedHeight(22)
        layout.addWidget(self._status)

        # items that get updated
        self._track_segments: list[pg.PlotDataItem] = []
        self._position_marker: pg.ScatterPlotItem | None = None
        self._finish_marker: pg.ScatterPlotItem | None = None
        self._comparison_items: list[pg.PlotDataItem] = []

    # ------------------------------------------------------------------
    # Session loading
    # ------------------------------------------------------------------

    def _on_session_loaded(self, session: Session) -> None:
        self._session = session
        self._plot.clear()
        self._track_segments.clear()
        self._comparison_items.clear()
        self._position_marker = None
        self._finish_marker = None

        if not session.has_gps():
            self._status.setText("No GPS data")
            return

        self._draw_track()
        self._add_position_marker()
        self._reset_view()
        self._status.setText(
            f"Track: {session.max_speed:.0f} km/h max  |  "
            f"Scroll/pinch to zoom, drag to pan"
        )

    def _draw_track(self) -> None:
        if self._session is None:
            return
        session = self._session
        x, y = _session_to_xy(session)

        speed = session.channels.get(CH_SPEED)
        if speed is None:
            # plain white line
            item = pg.PlotDataItem(x, y, pen=pg.mkPen(ACCENT_GREEN, width=2))
            self._plot.addItem(item)
            self._track_segments.append(item)
            return

        spd_max = float(np.nanmax(speed)) or 1.0
        spd_norm = np.clip(speed / spd_max, 0.0, 1.0)

        # Draw coloured segments
        n = len(x)
        step = max(1, n // 2000)   # limit draw calls for large datasets
        for i in range(0, n - step, step):
            colour = _speed_colour(float(spd_norm[i]))
            pen = pg.mkPen(colour, width=3)
            seg = pg.PlotDataItem(
                x[i:i + step + 1], y[i:i + step + 1], pen=pen
            )
            self._plot.addItem(seg)
            self._track_segments.append(seg)

    def _add_position_marker(self) -> None:
        self._position_marker = pg.ScatterPlotItem(
            size=12, pen=pg.mkPen(BG_DARK, width=2),
            brush=pg.mkBrush(ACCENT_ORANGE),
        )
        self._plot.addItem(self._position_marker)

    # ------------------------------------------------------------------
    # Comparison laps
    # ------------------------------------------------------------------

    def set_compare_laps(self, lap_numbers: list[int]) -> None:
        for item in self._comparison_items:
            self._plot.removeItem(item)
        self._comparison_items.clear()

        if self._session is None:
            return

        for i, lap_num in enumerate(lap_numbers[:4]):
            lap = next((l for l in self._session.laps if l.number == lap_num), None)
            if lap is None:
                continue
            channels = self._session.lap_channels(lap)
            lat = channels.get(CH_LAT)
            lon = channels.get(CH_LON)
            if lat is None or lon is None:
                continue

            # convert to same reference frame as the full track
            lat_full = self._session.channels[CH_LAT]
            lon_full = self._session.channels[CH_LON]
            lat0 = float(np.mean(lat_full))
            lon0 = float(np.mean(lon_full))
            R = 6_371_000.0
            lat0_rad = np.radians(lat0)
            x = R * np.radians(lon - lon0) * np.cos(lat0_rad)
            y = R * np.radians(lat - lat0)

            colour = _COMPARISON_COLOURS[i % len(_COMPARISON_COLOURS)]
            pen = pg.mkPen(colour, width=2, style=Qt.PenStyle.DashLine)
            item = pg.PlotDataItem(x, y, pen=pen)
            self._plot.addItem(item)
            self._comparison_items.append(item)

    # ------------------------------------------------------------------
    # Time cursor
    # ------------------------------------------------------------------

    def _on_time_changed(self, t: float) -> None:
        if self._session is None or not self._session.has_gps():
            return
        if self._position_marker is None:
            return

        x, y = _session_to_xy(self._session)
        idx = self._session.idx_at_time(t)
        if 0 <= idx < len(x):
            self._position_marker.setData([x[idx]], [y[idx]])

    # ------------------------------------------------------------------
    # Laps updated (re-draw finish marker)
    # ------------------------------------------------------------------

    def _on_laps_updated(self) -> None:
        if self._session is None or not self._session.laps:
            return
        lap = self._session.laps[0]
        self._show_finish_marker(lap.start_time)

    def _show_finish_marker(self, t: float) -> None:
        if self._session is None or not self._session.has_gps():
            return
        x, y = _session_to_xy(self._session)
        idx = self._session.idx_at_time(t)
        if self._finish_marker is None:
            self._finish_marker = pg.ScatterPlotItem(
                size=16,
                pen=pg.mkPen(ACCENT_GREEN, width=2),
                brush=pg.mkBrush(0, 0, 0, 0),
                symbol="t",
            )
            self._plot.addItem(self._finish_marker)
        self._finish_marker.setData([x[idx]], [y[idx]])

    # ------------------------------------------------------------------
    # Finish-line placement
    # ------------------------------------------------------------------

    def _toggle_finish_mode(self, checked: bool) -> None:
        self._setting_finish = checked
        if checked:
            self._status.setText(
                "Click on the track to set the start/finish line position"
            )
        else:
            self._status.setText("")

    def _on_map_click(self, event) -> None:
        if not self._setting_finish:
            return
        if self._session is None or not self._session.has_gps():
            return

        pos = self._plot.getViewBox().mapSceneToView(event.scenePos())
        click_x = pos.x()
        click_y = pos.y()

        # Convert plot XY back to lat/lon
        lat_full = self._session.channels[CH_LAT]
        lon_full = self._session.channels[CH_LON]
        lat0 = float(np.mean(lat_full))
        lon0 = float(np.mean(lon_full))
        R = 6_371_000.0
        lat0_rad = np.radians(lat0)

        click_lat = lat0 + np.degrees(click_y / R)
        click_lon = lon0 + np.degrees(click_x / (R * np.cos(lat0_rad)))

        self.finish_line_set.emit(float(click_lat), float(click_lon))
        self._btn_finish.setChecked(False)
        self._setting_finish = False

    # ------------------------------------------------------------------
    # View helpers
    # ------------------------------------------------------------------

    def _reset_view(self) -> None:
        self._plot.getViewBox().autoRange()
