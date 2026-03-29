"""
Lap timing panel — shows a table of all laps with times, speeds, and
delta to best lap.  Supports multi-select for comparison overlay.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QLabel, QPushButton, QHeaderView, QAbstractItemView,
)

from ...data.session import Session, Lap
from ...data.lap_detector import detect_laps, set_finish_from_track_click
from ..style import (
    ACCENT_GREEN, ACCENT_ORANGE, ACCENT_BLUE, ACCENT_RED,
    LAP_BEST_BG, LAP_SELECTED_BG, TEXT_PRIMARY, TEXT_SECONDARY, BG_PANEL,
)


_COL_LAP    = 0
_COL_TIME   = 1
_COL_DELTA  = 2
_COL_VMAX   = 3
_COL_VAVG   = 4
_HEADERS    = ["Lap", "Lap Time", "Δ Best", "V-Max", "V-Avg"]


class LapPanel(QWidget):
    """Lap list with timing, delta, and speed statistics."""

    lap_selected = pyqtSignal(int)     # lap number (0 = full session)
    laps_compare_changed = pyqtSignal(list)   # list of lap numbers to overlay

    def __init__(self, playback, parent=None) -> None:
        super().__init__(parent)
        self._playback = playback
        self._session: Session | None = None
        self._compare_laps: list[int] = []

        self._build_ui()
        playback.session_loaded.connect(self._on_session_loaded)
        playback.laps_updated.connect(self._refresh_table)
        playback.time_changed.connect(self._on_time_changed)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- header bar ---
        header = QWidget()
        header.setFixedHeight(30)
        header.setStyleSheet(f"background: #1a1a1a; border-bottom: 1px solid #333;")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(8, 0, 8, 0)
        h_layout.setSpacing(6)

        title = QLabel("Laps")
        title.setStyleSheet(
            f"font-size: 10px; font-weight: 600; letter-spacing: 0.5px; "
            f"text-transform: uppercase; color: {TEXT_SECONDARY};"
        )
        h_layout.addWidget(title)
        h_layout.addStretch()

        self._btn_detect = QPushButton("Auto Detect")
        self._btn_detect.setFixedHeight(20)
        self._btn_detect.setStyleSheet(
            "font-size: 10px; padding: 1px 8px;"
        )
        self._btn_detect.clicked.connect(self._auto_detect_laps)
        h_layout.addWidget(self._btn_detect)

        layout.addWidget(header)

        # --- table ---
        self._table = QTableWidget()
        self._table.setColumnCount(len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            _COL_LAP, QHeaderView.ResizeMode.Fixed
        )
        self._table.setColumnWidth(_COL_LAP, 38)
        self._table.setStyleSheet(f"font-size: 11px;")

        mono = QFont("Consolas", 10)
        for col in (_COL_TIME, _COL_DELTA, _COL_VMAX, _COL_VAVG):
            self._table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.Stretch
            )

        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.cellDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self._table)

        # --- summary bar ---
        self._summary = QLabel()
        self._summary.setStyleSheet(
            f"background: #1a1a1a; border-top: 1px solid #333; "
            f"padding: 3px 8px; font-size: 10px; color: {TEXT_SECONDARY};"
        )
        self._summary.setFixedHeight(22)
        layout.addWidget(self._summary)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _on_session_loaded(self, session: Session) -> None:
        self._session = session
        self._compare_laps = []
        self._refresh_table()

    def _refresh_table(self) -> None:
        import numpy as np
        from ...data.session import CH_SPEED
        session = self._session
        self._table.setRowCount(0)
        if session is None:
            return

        laps = session.laps
        best = session.best_lap
        mono = QFont("Consolas", 10)

        # Row 0 = Full Session (lap number 0)
        total_rows = 1 + len(laps)
        self._table.setRowCount(total_rows)

        # -- Full Session row --
        dur = session.duration
        spd_all = session.channels.get(CH_SPEED)
        v_max_all = float(np.nanmax(spd_all)) if spd_all is not None and len(spd_all) else 0.0
        v_avg_all = float(np.nanmean(spd_all)) if spd_all is not None and len(spd_all) else 0.0
        m, s = int(dur // 60), dur % 60
        dur_str = f"{m}:{s:06.3f}"
        for col, (text, align) in enumerate([
            ("All", Qt.AlignmentFlag.AlignCenter),
            (dur_str, Qt.AlignmentFlag.AlignCenter),
            ("—", Qt.AlignmentFlag.AlignCenter),
            (f"{v_max_all:.1f}", Qt.AlignmentFlag.AlignCenter),
            (f"{v_avg_all:.1f}", Qt.AlignmentFlag.AlignCenter),
        ]):
            item = QTableWidgetItem(text)
            item.setTextAlignment(align)
            item.setFont(mono)
            item.setData(Qt.ItemDataRole.UserRole, 0)   # lap 0 = full session
            item.setForeground(QColor(TEXT_SECONDARY))
            self._table.setItem(0, col, item)
        self._table.setRowHeight(0, 24)

        if not laps:
            self._summary.setText("No laps detected — click Auto Detect")
            return

        # -- Individual lap rows --
        for row, lap in enumerate(laps, start=1):
            channels = session.lap_channels(lap)
            spd = channels.get(CH_SPEED)
            v_max = float(np.nanmax(spd)) if spd is not None and len(spd) else 0.0
            v_avg = float(np.nanmean(spd)) if spd is not None and len(spd) else 0.0

            delta = lap.lap_time - best.lap_time if best else 0.0
            is_best = best and lap.number == best.number

            items = [
                (str(lap.number), Qt.AlignmentFlag.AlignCenter),
                (lap.lap_time_str, Qt.AlignmentFlag.AlignCenter),
                ("Best" if is_best else f"+{delta:.3f}", Qt.AlignmentFlag.AlignCenter),
                (f"{v_max:.1f}", Qt.AlignmentFlag.AlignCenter),
                (f"{v_avg:.1f}", Qt.AlignmentFlag.AlignCenter),
            ]

            for col, (text, align) in enumerate(items):
                item = QTableWidgetItem(text)
                item.setTextAlignment(align)
                item.setFont(mono)
                item.setData(Qt.ItemDataRole.UserRole, lap.number)

                if is_best:
                    item.setForeground(QColor(ACCENT_GREEN))
                    item.setBackground(QColor(LAP_BEST_BG))
                elif col == _COL_DELTA and not is_best:
                    item.setForeground(QColor(ACCENT_ORANGE))

                self._table.setItem(row, col, item)
            self._table.setRowHeight(row, 24)

        self._summary.setText(
            f"{len(laps)} laps  |  "
            f"Best: {best.lap_time_str if best else '—'}  |  "
            f"V-Max: {session.max_speed:.1f} km/h"
        )

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def _on_time_changed(self, t: float) -> None:
        """Bold the row for the lap that contains the current playhead."""
        if self._session is None:
            return
        active_lap = next(
            (l for l in self._session.laps if l.start_time <= t < l.end_time),
            None,
        )
        for row in range(self._table.rowCount()):
            item0 = self._table.item(row, 0)
            if item0 is None:
                continue
            lap_num = item0.data(Qt.ItemDataRole.UserRole)
            in_this_row = (active_lap is not None and active_lap.number == lap_num)
            f = QFont("Consolas", 10)
            f.setBold(in_this_row)
            for col in range(self._table.columnCount()):
                cell = self._table.item(row, col)
                if cell:
                    cell.setFont(f)

    def _on_selection_changed(self) -> None:
        selected_rows = {i.row() for i in self._table.selectedIndexes()}
        compare = []
        for row in selected_rows:
            item = self._table.item(row, 0)
            if item:
                compare.append(item.data(Qt.ItemDataRole.UserRole))
        self._compare_laps = compare
        self.laps_compare_changed.emit(compare)

        if len(selected_rows) == 1:
            row = next(iter(selected_rows))
            item = self._table.item(row, 0)
            if item:
                self.lap_selected.emit(item.data(Qt.ItemDataRole.UserRole))

    def _on_double_click(self, row: int, col: int) -> None:
        item = self._table.item(row, 0)
        if item:
            lap_num = item.data(Qt.ItemDataRole.UserRole)
            self._playback.select_lap(lap_num)

    def _auto_detect_laps(self) -> None:
        if self._session is None:
            return
        detect_laps(self._session)
        self._playback.notify_laps_updated()
