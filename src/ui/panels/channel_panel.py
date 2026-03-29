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
        self._active_lap = None   # Lap | None — which lap is currently displayed

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
        self._active_lap = None
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

        # Remove any existing Delta-T plot
        if "__delta_t__" in self._plots:
            old = self._plots.pop("__delta_t__")
            self._plot_layout.removeWidget(old)
            old.deleteLater()

        if not lap_numbers or len(lap_numbers) < 1:
            return

        overlay_idx = 0
        for lap_num in lap_numbers[:4]:
            # Skip the active lap — it's already shown as the main data
            if self._active_lap is not None and self._active_lap.number == lap_num:
                continue
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
                    plot.set_compare_data(overlay_idx, t_relative, data)
            overlay_idx += 1

        # Add Delta-T plot when exactly two laps are compared
        if len(lap_numbers) >= 2 and self._session.best_lap:
            self._add_delta_t_plot(lap_numbers[0], lap_numbers[1])

    # ------------------------------------------------------------------
    # Time / lap events
    # ------------------------------------------------------------------

    def _add_delta_t_plot(self, ref_lap_num: int, comp_lap_num: int) -> None:
        """Insert a Delta-T plot at the top of the channel stack."""
        from ...data.delta import compute_delta_t
        session = self._session
        if session is None:
            return
        ref_lap  = next((l for l in session.laps if l.number == ref_lap_num),  None)
        comp_lap = next((l for l in session.laps if l.number == comp_lap_num), None)
        if ref_lap is None or comp_lap is None:
            return
        try:
            _dist, delta_t = compute_delta_t(session, ref_lap, comp_lap)
        except Exception:
            return

        # Use the comparison lap's time axis (relative)
        comp_ch = session.lap_channels(comp_lap)
        t = comp_ch[CH_TIME] - comp_ch[CH_TIME][0]

        # Delta-T plot — blue, centred on zero, fixed ±5 s range
        plot = _ChannelPlot(
            "__delta_t__", "Δ Time", "s",
            pen_colour=ACCENT_BLUE, pen_width=1.5,
        )
        plot.setYRange(-5, 5, padding=0.05)
        # Zero reference line
        zero_line = pg.InfiniteLine(
            angle=0, movable=False,
            pen=pg.mkPen("#505050", width=1, style=pg.QtCore.Qt.PenStyle.DashLine),
        )
        plot.addItem(zero_line)
        plot.set_data(t, delta_t)
        plot.seek_requested.connect(self._playback.seek)

        if self._link_x_axis is not None:
            plot.getViewBox().setXLink(self._link_x_axis)

        # Insert at position 0 (top of stack)
        self._plot_layout.insertWidget(0, plot)
        self._plots["__delta_t__"] = plot

    def _on_time_changed(self, t: float) -> None:
        # When a lap is active, express cursor in lap-relative seconds (0 = lap start)
        if self._active_lap is not None:
            cursor_t = t - self._active_lap.start_time
        else:
            cursor_t = t
        for plot in self._plots.values():
            plot.set_cursor(cursor_t)

    def _on_lap_selected(self, lap_number: int) -> None:
        if self._session is None:
            return
        if lap_number == 0:
            self._active_lap = None
            self._load_channel_data(self._session.channels.get(CH_TIME),
                                    self._session.channels)
        else:
            lap = next(
                (l for l in self._session.laps if l.number == lap_number), None
            )
            if lap is None:
                return
            self._active_lap = lap
            ch = self._session.lap_channels(lap)
            t_abs = ch.get(CH_TIME)
            if t_abs is None or len(t_abs) == 0:
                return
            t_rel = t_abs - t_abs[0]   # relative time: 0 → lap_time
            self._load_channel_data(t_rel, ch)

    def _load_channel_data(self, t, channels: dict) -> None:
        """Push new (t, data) arrays into every existing plot."""
        if t is None or len(t) == 0:
            return
        for ch, plot in self._plots.items():
            if ch == "__delta_t__":
                continue
            data = channels.get(ch)
            if data is not None and len(data) == len(t):
                plot.set_data(t, data)
        t0, t1 = float(t[0]), float(t[-1])
        for plot in self._plots.values():
            plot.set_x_range(t0, t1)
