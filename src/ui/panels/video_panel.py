"""
Video panel — MP4 playback with telemetry overlay.

Uses PyQt6 QtMultimedia for hardware-accelerated video decoding.
The panel overlays live telemetry values (speed, G-forces, gear) on the
video frame, mirroring the Vbox Video overlay style.

Video sync:
  The playback controller drives the timeline.  When time changes this panel
  calls QMediaPlayer.setPosition() to keep video in sync.
  Conversely when the user drags the video slider, the controller is seeked.
"""
from __future__ import annotations

import numpy as np
from PyQt6.QtCore import Qt, QUrl, pyqtSlot
from PyQt6.QtGui import QColor, QPainter, QFont, QPen
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QStackedLayout,
)

from ...data.session import (
    Session, CH_TIME, CH_SPEED, CH_AX, CH_AY, CH_RPM, CH_GEAR,
)
from ..style import (
    ACCENT_GREEN, ACCENT_ORANGE, ACCENT_RED, BG_DARK, TEXT_PRIMARY,
    TEXT_SECONDARY,
)

_OVERLAY_BG    = QColor(0, 0, 0, 160)
_OVERLAY_TEXT  = QColor(255, 255, 255, 230)
_OVERLAY_GREEN = QColor(0, 192, 64, 230)
_OVERLAY_RED   = QColor(224, 48, 48, 230)


class TelemetryOverlay(QWidget):
    """
    Transparent overlay drawn on top of the video widget.
    Paints live speed, G-force bars, and gear.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._session: Session | None = None
        self._current_time: float = 0.0

    def update_time(self, t: float) -> None:
        self._current_time = t
        self.update()

    def set_session(self, session: Session) -> None:
        self._session = session

    def paintEvent(self, event) -> None:
        if self._session is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._draw_overlay(painter)
        painter.end()

    def _draw_overlay(self, painter: QPainter) -> None:
        session = self._session
        t = self._current_time
        w, h = self.width(), self.height()

        # Values at current time
        speed  = session.value_at_time(CH_SPEED, t) or 0.0
        ax     = session.value_at_time(CH_AX, t) or 0.0
        ay     = session.value_at_time(CH_AY, t) or 0.0
        rpm    = session.value_at_time(CH_RPM, t)
        gear   = session.value_at_time(CH_GEAR, t)

        margin = 12
        panel_w = max(140, int(w * 0.22))
        panel_h = 110
        x0 = margin
        y0 = h - panel_h - margin

        # Background panel
        painter.setBrush(QColor(0, 0, 0, 160))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(x0, y0, panel_w, panel_h, 6, 6)

        # Speed
        speed_font = QFont("Consolas", max(8, int(h * 0.055)))
        speed_font.setBold(True)
        painter.setFont(speed_font)
        painter.setPen(QPen(_OVERLAY_GREEN))
        painter.drawText(
            x0 + 8, y0 + int(panel_h * 0.42),
            f"{speed:.0f}"
        )

        # "km/h" label
        unit_font = QFont("Consolas", max(6, int(h * 0.025)))
        painter.setFont(unit_font)
        painter.setPen(QPen(QColor(160, 160, 160, 200)))
        painter.drawText(
            x0 + 8, y0 + int(panel_h * 0.55),
            "km/h"
        )

        # G-force bars
        bar_y_lat = y0 + int(panel_h * 0.65)
        bar_y_lon = y0 + int(panel_h * 0.80)
        bar_x = x0 + 8
        bar_w = panel_w - 16
        bar_h = 6
        centre = bar_x + bar_w // 2

        self._draw_g_bar(painter, bar_x, bar_y_lat, bar_w, bar_h, ay,  3.0, "Lat G")
        self._draw_g_bar(painter, bar_x, bar_y_lon, bar_w, bar_h, -ax, 2.0, "Lon G")

        # Gear (top right corner)
        if gear is not None:
            gear_font = QFont("Consolas", max(10, int(h * 0.07)))
            gear_font.setBold(True)
            painter.setFont(gear_font)
            painter.setPen(QPen(QColor(255, 200, 0, 220)))
            gx = w - margin - int(w * 0.08)
            painter.drawText(gx, y0 + int(panel_h * 0.6), str(int(gear)))
            gear_lbl = QFont("Consolas", max(6, int(h * 0.025)))
            painter.setFont(gear_lbl)
            painter.setPen(QPen(QColor(160, 160, 160, 180)))
            painter.drawText(gx + 4, y0 + int(panel_h * 0.75), "gear")

    def _draw_g_bar(
        self, painter: QPainter,
        x: int, y: int, w: int, h: int,
        value: float, scale: float,
        label: str,
    ) -> None:
        # Background
        painter.setBrush(QColor(60, 60, 60, 180))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(x, y, w, h, 2, 2)

        # Filled portion
        centre = x + w // 2
        fraction = float(np.clip(abs(value) / scale, 0.0, 1.0))
        bar_len = int(fraction * (w // 2))
        if value >= 0:
            fill_x = centre
            fill_w = bar_len
            colour = QColor(255, 80, 80, 220)
        else:
            fill_x = centre - bar_len
            fill_w = bar_len
            colour = QColor(80, 160, 255, 220)

        if fill_w > 0:
            painter.setBrush(colour)
            painter.drawRoundedRect(fill_x, y, fill_w, h, 2, 2)

        # Centre tick
        painter.setBrush(QColor(255, 255, 255, 180))
        painter.drawRect(centre - 1, y, 2, h)

        # Label
        lbl_font = QFont("Consolas", max(5, h - 1))
        painter.setFont(lbl_font)
        painter.setPen(QPen(QColor(140, 140, 140, 200)))
        painter.drawText(x, y - 1, label)


class VideoPanel(QWidget):
    """MP4 video playback panel with telemetry overlay."""

    def __init__(self, playback, parent=None) -> None:
        super().__init__(parent)
        self._playback = playback
        self._session: Session | None = None
        self._syncing = False   # prevent feedback loop during seek

        self._build_ui()
        playback.session_loaded.connect(self._on_session_loaded)
        playback.time_changed.connect(self._on_time_changed)

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

        self._title_label = QLabel("Video")
        self._title_label.setStyleSheet(
            f"font-size: 10px; font-weight: 600; letter-spacing: 0.5px; "
            f"color: {TEXT_SECONDARY};"
        )
        tb.addWidget(self._title_label)
        tb.addStretch()

        self._offset_label = QLabel("Offset: 0.000 s")
        self._offset_label.setStyleSheet(f"font-size: 10px; color: {TEXT_SECONDARY};")
        tb.addWidget(self._offset_label)

        self._btn_earlier = QPushButton("◀ 0.1s")
        self._btn_earlier.setFixedHeight(20)
        self._btn_earlier.setStyleSheet("font-size: 10px; padding: 1px 6px;")
        self._btn_earlier.clicked.connect(lambda: self._adjust_offset(-0.1))
        tb.addWidget(self._btn_earlier)

        self._btn_later = QPushButton("0.1s ▶")
        self._btn_later.setFixedHeight(20)
        self._btn_later.setStyleSheet("font-size: 10px; padding: 1px 6px;")
        self._btn_later.clicked.connect(lambda: self._adjust_offset(0.1))
        tb.addWidget(self._btn_later)

        layout.addWidget(toolbar)

        # video + overlay stack
        video_container = QWidget()
        video_container.setStyleSheet("background: black;")
        video_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        stack = QVBoxLayout(video_container)
        stack.setContentsMargins(0, 0, 0, 0)

        self._video_widget = QVideoWidget()
        self._video_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        stack.addWidget(self._video_widget)
        layout.addWidget(video_container)

        # Overlay (child of video_container so it floats on top)
        self._overlay = TelemetryOverlay(video_container)
        self._overlay.setGeometry(video_container.rect())
        self._overlay.raise_()

        video_container.resizeEvent = self._on_container_resize  # type: ignore

        # QMediaPlayer
        self._player = QMediaPlayer()
        self._audio  = QAudioOutput()
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(self._video_widget)
        self._audio.setVolume(0.7)

        # no-video placeholder
        self._placeholder = QLabel(
            "No video loaded\n\nUse File → Open to load an .mp4 file"
        )
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(
            f"color: #505050; font-size: 14px; background: #0a0a0a;"
        )
        layout.addWidget(self._placeholder)

        self._video_widget.hide()

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def _on_container_resize(self, event) -> None:
        self._overlay.setGeometry(self._video_widget.geometry())

    def _on_session_loaded(self, session: Session) -> None:
        self._session = session
        self._overlay.set_session(session)

        if session.has_video():
            self._load_video(session.video_path)  # type: ignore
        else:
            self._video_widget.hide()
            self._placeholder.show()

    def _load_video(self, path: str) -> None:
        self._player.setSource(QUrl.fromLocalFile(path))
        self._video_widget.show()
        self._placeholder.hide()
        import os
        self._title_label.setText(f"Video — {os.path.basename(path)}")

    def _on_time_changed(self, t: float) -> None:
        if self._session is None:
            return
        self._overlay.update_time(t)

        if not self._session.has_video():
            return

        self._syncing = True
        video_t = t + self._session.video_offset
        ms = int(max(0.0, video_t) * 1000)
        self._player.setPosition(ms)
        self._syncing = False

    def _adjust_offset(self, delta: float) -> None:
        if self._session is None:
            return
        self._session.video_offset += delta
        self._offset_label.setText(f"Offset: {self._session.video_offset:+.3f} s")
        # Re-sync
        self._on_time_changed(self._playback.current_time)
