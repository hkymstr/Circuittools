"""
Video panel — MP4 playback with telemetry overlay.

Single-video mode (default):
  Full-width video with speed / G-force / gear overlay.

Side-by-side comparison mode (activated when 2 laps are selected):
  Two videos play simultaneously, each locked to its lap's elapsed time.
  The faster lap gets a green border; the slower gets a red border.
  Both show independent telemetry overlays with live channel values.

Sync logic:
  The PlaybackController provides current_time (absolute session seconds).
  For each video pane:
    elapsed = current_time - lap.start_time           (0 at lap start)
    video_ms = (lap.start_time + video_offset + elapsed) * 1000
             = (current_time + video_offset) * 1000

  Since both laps are segments of the same .mp4, the same formula applies —
  we simply seek two QMediaPlayer instances to different absolute positions.
"""
from __future__ import annotations

import os
import numpy as np
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QColor, QPainter, QFont, QPen, QPalette
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSplitter,
    QSizePolicy, QFrame,
)

from ...data.session import (
    Session, Lap, CH_TIME, CH_SPEED, CH_AX, CH_AY, CH_RPM, CH_GEAR,
)
from ..style import (
    ACCENT_GREEN, ACCENT_ORANGE, ACCENT_RED, ACCENT_BLUE,
    BG_DARK, TEXT_PRIMARY, TEXT_SECONDARY,
)

_OVERLAY_GREEN = QColor(0, 192, 64, 230)
_BORDER_FASTER = "#00c040"   # green — faster lap
_BORDER_SLOWER = "#e03030"   # red   — slower lap
_BORDER_NEUTRAL = "#333333"  # grey  — single video / equal


# ---------------------------------------------------------------------------
# Telemetry overlay
# ---------------------------------------------------------------------------

class TelemetryOverlay(QWidget):
    """Transparent HUD painted over one video pane."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._session: Session | None = None
        self._current_time: float = 0.0

    def update_time(self, t: float) -> None:
        self._current_time = t
        self.update()

    def set_session(self, session: Session | None) -> None:
        self._session = session

    def paintEvent(self, event) -> None:
        if self._session is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._draw(p)
        p.end()

    def _draw(self, p: QPainter) -> None:
        s = self._session
        t = self._current_time
        w, h = self.width(), self.height()

        speed = s.value_at_time(CH_SPEED, t) or 0.0
        ax    = s.value_at_time(CH_AX,    t) or 0.0
        ay    = s.value_at_time(CH_AY,    t) or 0.0
        gear  = s.value_at_time(CH_GEAR,  t)

        margin  = 10
        pw      = max(120, int(w * 0.21))
        ph      = 100
        x0      = margin
        y0      = h - ph - margin

        # Panel background
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 155))
        p.drawRoundedRect(x0, y0, pw, ph, 5, 5)

        # Speed
        sf = QFont("Consolas", max(7, int(h * 0.05)))
        sf.setBold(True)
        p.setFont(sf)
        p.setPen(QPen(_OVERLAY_GREEN))
        p.drawText(x0 + 7, y0 + int(ph * 0.42), f"{speed:.0f}")

        uf = QFont("Consolas", max(5, int(h * 0.022)))
        p.setFont(uf)
        p.setPen(QPen(QColor(160, 160, 160, 200)))
        p.drawText(x0 + 7, y0 + int(ph * 0.55), "km/h")

        # G bars
        bx = x0 + 7
        bw = pw - 14
        self._g_bar(p, bx, y0 + int(ph * 0.65), bw, 5, ay,  3.0, "Lat G")
        self._g_bar(p, bx, y0 + int(ph * 0.80), bw, 5, -ax, 2.0, "Lon G")

        # Gear
        if gear is not None:
            gf = QFont("Consolas", max(9, int(h * 0.065)))
            gf.setBold(True)
            p.setFont(gf)
            p.setPen(QPen(QColor(255, 200, 0, 220)))
            gx = w - margin - int(w * 0.09)
            p.drawText(gx, y0 + int(ph * 0.58), str(int(gear)))

    def _g_bar(self, p, x, y, w, h, val, scale, label):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(55, 55, 55, 180))
        p.drawRoundedRect(x, y, w, h, 2, 2)
        cx = x + w // 2
        frac = float(np.clip(abs(val) / scale, 0.0, 1.0))
        blen = int(frac * (w // 2))
        if blen > 0:
            colour = QColor(255, 80, 80, 220) if val >= 0 else QColor(80, 160, 255, 220)
            p.setBrush(colour)
            fx = cx if val >= 0 else cx - blen
            p.drawRoundedRect(fx, y, blen, h, 2, 2)
        p.setBrush(QColor(255, 255, 255, 180))
        p.drawRect(cx - 1, y, 2, h)
        lf = QFont("Consolas", max(4, h - 1))
        p.setFont(lf)
        p.setPen(QPen(QColor(130, 130, 130, 200)))
        p.drawText(x, y - 1, label)


# ---------------------------------------------------------------------------
# Single video pane (one player + one overlay + border)
# ---------------------------------------------------------------------------

class _VideoPane(QWidget):
    """
    One video slot: border frame + QVideoWidget + TelemetryOverlay + lap label.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._session: Session | None = None
        self._lap: Lap | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Lap label strip
        self._lap_label = QLabel("—")
        self._lap_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lap_label.setFixedHeight(20)
        self._lap_label.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 11px; font-weight: bold; "
            "background: #111; color: #808080;"
        )
        layout.addWidget(self._lap_label)

        # Border frame
        self._frame = QFrame()
        self._frame.setLineWidth(3)
        self._frame.setFrameShape(QFrame.Shape.Box)
        self._frame.setStyleSheet(f"border: 3px solid {_BORDER_NEUTRAL}; background: black;")
        frame_layout = QVBoxLayout(self._frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)

        # Video widget
        self._video_widget = QVideoWidget()
        self._video_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        frame_layout.addWidget(self._video_widget)
        layout.addWidget(self._frame, 1)

        # Overlay on top of video widget
        self._overlay = TelemetryOverlay(self._frame)
        self._frame.resizeEvent = self._on_frame_resize  # type: ignore

        # Placeholder
        self._placeholder = QLabel("No video")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color: #383838; font-size: 13px; background: #0a0a0a;")
        frame_layout.addWidget(self._placeholder)
        self._video_widget.hide()

        # Media player
        self._player = QMediaPlayer()
        self._audio  = QAudioOutput()
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(self._video_widget)
        self._audio.setVolume(0.0)   # muted by default; main pane enables audio
        self._player.errorOccurred.connect(self._on_player_error)

    # ------------------------------------------------------------------

    def _on_frame_resize(self, event) -> None:
        self._overlay.setGeometry(self._video_widget.geometry())

    def load(self, session: Session, lap: Lap | None = None) -> None:
        """Load video source and configure for a given lap."""
        self._session = session
        self._lap = lap
        self._overlay.set_session(session)

        if session.has_video():
            # Disconnect any previous status handler before setting new source
            try:
                self._player.mediaStatusChanged.disconnect(self._on_media_loaded)
            except RuntimeError:
                pass
            self._player.mediaStatusChanged.connect(self._on_media_loaded)
            self._player.setSource(QUrl.fromLocalFile(session.video_path))
            self._video_widget.show()
            self._placeholder.hide()
        else:
            self._video_widget.hide()
            self._placeholder.show()

    def _on_media_loaded(self, status) -> None:
        """Show first frame once media is buffered; update lap label."""
        if status == QMediaPlayer.MediaStatus.LoadedMedia or \
           status == QMediaPlayer.MediaStatus.BufferedMedia:
            try:
                self._player.mediaStatusChanged.disconnect(self._on_media_loaded)
            except RuntimeError:
                pass
            # Pause to display the first frame (only if not already playing)
            if self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                self._player.pause()
            lap = self._lap
            session = self._session
            if lap is not None:
                self._lap_label.setText(f"Lap {lap.number}  —  {lap.lap_time_str}")
            elif session is not None:
                self._lap_label.setText(os.path.basename(session.source_file or ""))

    def _on_player_error(self, error, error_string: str) -> None:
        print(f"[VideoPane] player error {error}: {error_string}")

    def unload(self) -> None:
        self._player.setSource(QUrl())
        self._session = None
        self._lap = None
        self._overlay.set_session(None)
        self._lap_label.setText("—")
        self.set_border(_BORDER_NEUTRAL)

    def set_border(self, colour: str) -> None:
        self._frame.setStyleSheet(
            f"border: 3px solid {colour}; background: black;"
        )

    def set_label_colour(self, colour: str) -> None:
        self._lap_label.setStyleSheet(
            f"font-family: Consolas, monospace; font-size: 11px; font-weight: bold; "
            f"background: #111; color: {colour};"
        )

    def enable_audio(self, on: bool) -> None:
        self._audio.setVolume(0.7 if on else 0.0)

    def seek(self, session_time: float, video_offset: float, force: bool = False) -> None:
        """
        Seek the player to the absolute session time.
        When the player is actively playing, skip the seek — repeatedly calling
        setPosition() while playing fights the player's own clock and stalls it.
        Only seek when paused or when force=True (e.g. user scrubbed the slider).
        """
        if force or self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            ms = int(max(0.0, session_time + video_offset) * 1000)
            self._player.setPosition(ms)

    def update_overlay(self, session_time: float) -> None:
        self._overlay.update_time(session_time)


# ---------------------------------------------------------------------------
# VideoPanel
# ---------------------------------------------------------------------------

class VideoPanel(QWidget):
    """
    Video playback panel.

    Call set_compare_laps([lap_num_a, lap_num_b]) to switch to side-by-side mode.
    Call set_compare_laps([]) or set_compare_laps([one_lap]) to return to
    single-video mode.
    """

    def __init__(self, playback, parent=None) -> None:
        super().__init__(parent)
        self._playback = playback
        self._session: Session | None = None
        self._compare_laps: list[int] = []

        self._build_ui()
        playback.session_loaded.connect(self._on_session_loaded)
        playback.time_changed.connect(self._on_time_changed)

    def set_playing(self, playing: bool) -> None:
        """Called by PlaybackController when play/pause state changes."""
        if playing:
            self._left._player.play()
            if self._right.isVisible():
                self._right._player.play()
        else:
            self._left._player.pause()
            if self._right.isVisible():
                self._right._player.pause()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Toolbar
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

        btn_earlier = QPushButton("◀ 0.1s")
        btn_earlier.setFixedHeight(20)
        btn_earlier.setStyleSheet("font-size: 10px; padding: 1px 6px;")
        btn_earlier.clicked.connect(lambda: self._adjust_offset(-0.1))
        tb.addWidget(btn_earlier)

        btn_later = QPushButton("0.1s ▶")
        btn_later.setFixedHeight(20)
        btn_later.setStyleSheet("font-size: 10px; padding: 1px 6px;")
        btn_later.clicked.connect(lambda: self._adjust_offset(0.1))
        tb.addWidget(btn_later)

        layout.addWidget(toolbar)

        # Two panes side by side (splitter so user can resize)
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(3)

        self._left  = _VideoPane()
        self._right = _VideoPane()

        self._splitter.addWidget(self._left)
        self._splitter.addWidget(self._right)
        layout.addWidget(self._splitter, 1)

        # Start in single-video mode (right pane hidden)
        self._right.hide()
        self._left.enable_audio(True)

        # No-video placeholder (shown when no session loaded)
        self._placeholder = QLabel(
            "No video loaded\n\nUse File → Open to load an .mp4 file"
        )
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(
            "color: #505050; font-size: 14px; background: #0a0a0a;"
        )
        layout.addWidget(self._placeholder)
        self._splitter.hide()

    # ------------------------------------------------------------------
    # Session loading
    # ------------------------------------------------------------------

    def _on_session_loaded(self, session: Session) -> None:
        self._session = session
        self._compare_laps = []

        if session.has_video():
            self._left.load(session, lap=None)
            self._right.unload()
            self._right.hide()
            self._splitter.show()
            self._placeholder.hide()
            self._title_label.setText(
                f"Video — {os.path.basename(session.video_path)}"  # type: ignore
            )
        else:
            self._splitter.hide()
            self._placeholder.show()

    # ------------------------------------------------------------------
    # Comparison mode
    # ------------------------------------------------------------------

    def set_compare_laps(self, lap_numbers: list[int]) -> None:
        """Switch between single and side-by-side mode."""
        self._compare_laps = lap_numbers
        session = self._session
        if session is None or not session.has_video():
            return

        if len(lap_numbers) >= 2:
            self._enter_compare_mode(lap_numbers[0], lap_numbers[1])
        elif len(lap_numbers) == 1:
            self._enter_single_mode(lap_numbers[0])
        else:
            self._enter_single_mode(None)

    def _enter_single_mode(self, lap_num: int | None) -> None:
        session = self._session
        if session is None:
            return
        lap = (
            next((l for l in session.laps if l.number == lap_num), None)
            if lap_num is not None else None
        )
        self._left.load(session, lap)
        self._left.set_border(_BORDER_NEUTRAL)
        self._left.set_label_colour("#808080")
        self._right.unload()
        self._right.hide()
        self._splitter.setSizes([1, 0])

    def _enter_compare_mode(self, lap_num_a: int, lap_num_b: int) -> None:
        session = self._session
        if session is None:
            return

        lap_a = next((l for l in session.laps if l.number == lap_num_a), None)
        lap_b = next((l for l in session.laps if l.number == lap_num_b), None)
        if lap_a is None or lap_b is None:
            return

        # Faster lap = green border (left by convention); slower = red (right)
        if lap_a.lap_time <= lap_b.lap_time:
            faster, slower = lap_a, lap_b
        else:
            faster, slower = lap_b, lap_a

        self._left.load(session, faster)
        self._left.set_border(_BORDER_FASTER)
        self._left.set_label_colour(_BORDER_FASTER)

        self._right.load(session, slower)
        self._right.set_border(_BORDER_SLOWER)
        self._right.set_label_colour(_BORDER_SLOWER)
        self._right.show()

        self._splitter.setSizes([1, 1])   # equal split
        self._left.enable_audio(True)
        self._right.enable_audio(False)

    # ------------------------------------------------------------------
    # Time sync
    # ------------------------------------------------------------------

    def _on_time_changed(self, t: float) -> None:
        if self._session is None:
            return
        offset = self._session.video_offset

        # Left pane always syncs to absolute time
        self._left.seek(t, offset)
        self._left.update_overlay(t)

        # Right pane syncs to the same elapsed position within its own lap
        if self._right.isVisible() and self._right._lap is not None:
            ref_lap  = self._left._lap
            comp_lap = self._right._lap
            if ref_lap is not None:
                elapsed = t - ref_lap.start_time
                comp_t  = comp_lap.start_time + elapsed
                self._right.seek(comp_t, offset)
                self._right.update_overlay(comp_t)
            else:
                self._right.seek(t, offset)
                self._right.update_overlay(t)

    # ------------------------------------------------------------------
    # Offset
    # ------------------------------------------------------------------

    def _adjust_offset(self, delta: float) -> None:
        if self._session is None:
            return
        self._session.video_offset += delta
        self._offset_label.setText(f"Offset: {self._session.video_offset:+.3f} s")
        self._on_time_changed(self._playback.current_time)
