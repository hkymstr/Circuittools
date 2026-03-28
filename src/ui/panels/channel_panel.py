"""
Data channel panel — stacked time-series graphs for all telemetry channels.

Features:
  • Speed, lateral G, longitudinal G, throttle, brake, RPM stacked vertically
  • Synchronised vertical cursor across all plots (linked to playback time)
  • Click on any plot to seek playback to that time
  • Multi-lap comparison overlay (each lap a different colour)
  • Channel visibility toggles
  • Y-axis auto-scale or fixed-range per channel
"""
from __future__ import annotations

from typing import Optional
import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPen, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel,
    QPushButton, QCheckBox, QSizePolicy,
)

from ...data.session import Session, CH_TIME, DEFAULT_CHANNEL_ORDER, CHANNEL_META
from ..style import (
    ACCENT_GREEN, ACCENT_ORANGE, ACCENT_BLUE, BG_DARK, BG_PANEL,
    BORDER, TEXT_PRIMARY, TEXT_SECONDARY, BG_TOOLBAR,
)

_CHANNEL_PENS = {
    "speed":    (ACCENT_GREEN, 1.5),
    "accel_y":  ("#ff4444", 1.2),
    "accel_x":  ("#ff8800", 1.2),
    "throttle": (ACCENT_GREEN, 1.2),
    "brake":    ("#ff2020", 1.2),
    "rpm":      ("#8080ff", 1.0),
    "gear":     ("#c0c000", 1.0),
}

_COMPARISON_PENS = [
    ACCENT_BLUE,
    "#ff00cc",
    "#00ccff",
    "#ffcc00",
]

# Y-axis fixed ranges per channel (min, max); None = auto
_CHANNEL_RANGES: dict[str, tuple[float, float] | None] = {
    "speed":    (0, None),
    "accel_y":  (-3, 3),
    "accel_x":  (-2, 2),
    "throttle": (0, 100),
    "brake":    (0, 100),
    "rpm":      (0, None),
    "gear":     (0, 8),
}

_PLOT_HEIGHT = 90   # pixels per channel plot


class _ChannelPlot(pg.PlotWidget):
    """One channel's plot row."""

    seek_requested = pyqtSignal(float)

    def __init__(
        self,
        channel: str,
        label: str,
        unit: str,
        pen_colour: str,
        pen_width: float = 1.5,
    ) -> None:
        super().__init__(background=BG_DARK)
        self.channel = channel
        self._duration = 0.0

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(_PLOT_HEIGHT)
        self.setMouseEnabled(x=True, y=False)
        self.showGrid(x=False, y=True, alpha=0.15)
        self.hideButtons()
        self.getAxis("bottom").hide()

        # Y axis label
        self.getAxis("left").setWidth(52)
        self.getAxis("left").setLabel(
            f"<span style='color:{TEXT_SECONDARY};font-size:9px'>"
            f"{label}<br/>({unit})</span>"
        )

        # Main trace
        self._main_item = self.plot(pen=pg.mkPen(pen_colour, width=pen_width))

        # Comparison traces
        self._compare_items: list[pg.PlotDataItem] = []

        # Cursor line
        self._cursor = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen(ACCENT_ORANGE, width=1.5, style=Qt.PenStyle.DashLine),
        )
        self.addItem(self._cursor)

        # Range
        r = _CHANNEL_RANGES.get(channel)
        if r:
            mn, mx = r
            if mn is not None and mx is not None:
                self.setYRange(mn, mx, padding=0.05)
            elif mn is not None:
                vb = self.getViewBox()
                vb.setLimits(yMin=mn)

        self.scene().sigMouseClicked.connect(self._on_click)

    def set_data(self, time: np.ndarray, values: np.ndarray) -> None:
        self._duration = float(time[-1]) if len(time) else 0.0
        self._main_item.setData(time, values)
        # auto-range Y if no fixed range
        r = _CHANNEL_RANGES.get(self.channel)
        if r is None or r[1] is None:
            mn = float(np.nanmin(values)) if len(values) else 0.0
            mx = float(np.nanmax(values)) if len(values) else 1.0
            pad = (mx - mn) * 0.1 or 1.0
            lo = (r[0] if r else None) or (mn - pad)
            hi = mx + pad
            self.setYRange(lo, hi, padding=0)

    def set_x_range(self, t_min: float, t_max: float) -> None:
        self.setXRange(t_min, t_max, padding=0)

    def set_cursor(self, t: float) -> None:
        self._cursor.setValue(t)

    def set_compare_data(self, idx: int, time: np.ndarray, values: np.ndarray) -> None:
        while len(self._compare_items) <= idx:
            colour = _COMPARISON_PENS[len(self._compare_items) % len(_COMPARISON_PENS)]
            item = self.plot(
                pen=pg.mkPen(colour, width=1.2, style=Qt.PenStyle.DashLine)
            )
            self._compare_items.append(item)
        self._compare_items[idx].setData(time, values)

    def clear_compare_data(self) -> None:
        for item in self._compare_items:
            item.setData([], [])

    def _on_click(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        vb = self.getViewBox()
        pos = vb.mapSceneToView(event.scenePos())
        self.seek_requested.emit(float(pos.x()))


class ChannelPanel(QWidget):
    """Stacked channel graphs panel."""

    seek_requested = pyqtSignal(float)

    def __init__(self, playback, parent=None) -> None:
        super().__init__(parent)
        self._playback = playback
        self._session: Session | None = None
        self._plots: dict[str, _ChannelPlot] = {}
        self._visible_channels: set[str] = set(DEFAULT_CHANNEL_ORDER)
        self._link_x_axis: pg.ViewBox | None = None

        self._build_ui()
        playback.session_loaded.connect(self._on_session_loaded)
        playback.time_changed.connect(self._on_time_changed)
        playback.lap_selected.connect(self._on_lap_selected)

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
        toolbar.setStyleSheet("background: #1a1a1a; border-bottom: 1px solid #333;")
        tb = QHBoxLayout(toolbar)
        tb.setContentsMargins(8, 0, 8, 0)
        tb.setSpacing(6)

        lbl = QLabel("Channels")
        lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 600; letter-spacing: 0.5px; "
            f"color: {TEXT_SECONDARY};"
        )
        tb.addWidget(lbl)
        tb.addStretch()

        layout.addWidget(toolbar)

        # scrollable plot area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("border: none;")

        self._plot_container = QWidget()
        self._plot_layout = QVBoxLayout(self._plot_container)
        self._plot_layout.setContentsMargins(0, 0, 0, 0)
        self._plot_layout.setSpacing(1)
        scroll.setWidget(self._plot_container)
        layout.addWidget(scroll)

        # time axis at bottom
        self._time_axis = pg.PlotWidget(background=BG_DARK)
        self._time_axis.setFixedHeight(28)
        self._time_axis.hideAxis("left")
        self._time_axis.setMouseEnabled(x=True, y=False)
        self._time_axis.getAxis("bottom").setLabel(
            "<span style='color:#606060;font-size:9px'>Time (s)</span>"
        )
        layout.addWidget(self._time_axis)

    # ------------------------------------------------------------------
    # Session loading
    # ------------------------------------------------------------------

    def _on_session_loaded(self, session: Session) -> None:
        self._session = session
        self._rebuild_plots()

    def _rebuild_plots(self) -> None:
        # Remove old plots
        for plot in self._plots.values():
            self._plot_layout.removeWidget(plot)
            plot.deleteLater()
        self._plots.clear()
        self._link_x_axis = None

        session = self._session
        if session is None:
            return

        t = session.channels.get(CH_TIME)
        if t is None:
            return

        channels_to_show = [
            ch for ch in DEFAULT_CHANNEL_ORDER if ch in session.channels
        ]
        # Add any extra channels not in default order
        for ch in session.channels:
            if ch != CH_TIME and ch not in channels_to_show:
                channels_to_show.append(ch)

        for ch in channels_to_show:
            label, unit = CHANNEL_META.get(ch, (ch, ""))
            pen_colour, pen_width = _CHANNEL_PENS.get(ch, (ACCENT_GREEN, 1.0))
            plot = _ChannelPlot(ch, label, unit, pen_colour, pen_width)
            plot.seek_requested.connect(self.seek_requested)
            plot.seek_requested.connect(self._playback.seek)

            data = session.channels[ch]
            plot.set_data(t, data)

            # Link X axes
            if self._link_x_axis is None:
                self._link_x_axis = plot.getViewBox()
                self._time_axis.getViewBox().setXLink(self._link_x_axis)
            else:
                plot.getViewBox().setXLink(self._link_x_axis)

            self._plot_layout.addWidget(plot)
            self._plots[ch] = plot

        self._plot_layout.addStretch()

    # ------------------------------------------------------------------
    # Comparison laps
    # ------------------------------------------------------------------

    def set_compare_laps(self, lap_numbers: list[int]) -> None:
        if self._session is None:
            return
        for plot in self._plots.values():
            plot.clear_compare_data()

        for i, lap_num in enumerate(lap_numbers[:4]):
            lap = next(
                (l for l in self._session.laps if l.number == lap_num), None
            )
            if lap is None:
                continue
            channels = self._session.lap_channels(lap)
            t_lap = channels.get(CH_TIME)
            if t_lap is None:
                continue
            t_relative = t_lap - t_lap[0]

            for ch, plot in self._plots.items():
                data = channels.get(ch)
                if data is not None:
                    plot.set_compare_data(i, t_relative, data)

    # ------------------------------------------------------------------
    # Time / lap events
    # ------------------------------------------------------------------

    def _on_time_changed(self, t: float) -> None:
        for plot in self._plots.values():
            plot.set_cursor(t)

    def _on_lap_selected(self, lap_number: int) -> None:
        if self._session is None:
            return
        if lap_number == 0:
            t = self._session.channels.get(CH_TIME)
            if t is not None:
                for plot in self._plots.values():
                    plot.set_x_range(float(t[0]), float(t[-1]))
        else:
            lap = next(
                (l for l in self._session.laps if l.number == lap_number), None
            )
            if lap:
                for plot in self._plots.values():
                    plot.set_x_range(lap.start_time, lap.end_time)
