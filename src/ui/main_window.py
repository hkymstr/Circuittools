"""
MainWindow — the primary application window.

Layout (mirrors Vbox Circuit Tools):

  ┌─────────────────────────────────────────────────────────────┐
  │  Menu bar                                                   │
  ├─────────────────────────────────────────────────────────────┤
  │  Toolbar  [Open ▾] [Lap ▾] [Rate ▾] [Compare] ──────────── │
  ├──────────────┬───────────────────────┬──────────────────────┤
  │              │                       │                      │
  │  Lap Panel   │     Video Panel       │   Track Map Panel    │
  │  (left dock) │   (central widget)    │   (right dock)       │
  │              ├───────────────────────┤                      │
  │              │  Channel Data Panel   │                      │
  │              │  (bottom of central)  │                      │
  ├──────────────┴───────────────────────┴──────────────────────┤
  │  ◀◀  ◀  ▶/❚❚  ▶  ▶▶  │════════════════════│  0:00.000  /  0:00.000 │
  └─────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import os
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QAction, QIcon, QKeySequence
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QDockWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QLabel, QPushButton, QSlider, QComboBox,
    QFileDialog, QStatusBar, QToolBar, QMessageBox, QSizePolicy,
)

from .playback import PlaybackController
from .panels.video_panel import VideoPanel
from .panels.track_map_panel import TrackMapPanel
from .panels.channel_panel import ChannelPanel
from .panels.lap_panel import LapPanel
from ..data.parsers.loader import load_file, attach_video
from ..data.lap_detector import detect_laps, set_finish_from_track_click
from ..data.session import Session


class MainWindow(QMainWindow):

    APP_TITLE = "Circuit Tools — Smartycam3 Analyser"

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(self.APP_TITLE)
        self.resize(1600, 900)
        self.setMinimumSize(QSize(900, 600))

        self._playback = PlaybackController(self)
        self._session: Session | None = None

        self._build_ui()
        self._build_menus()
        self._build_toolbar()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Central widget: video (top) + channels (bottom)
        central = QWidget()
        self.setCentralWidget(central)
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        # Splitter: video top, channels bottom
        self._central_splitter = QSplitter(Qt.Orientation.Vertical)
        self._central_splitter.setHandleWidth(3)

        self._video_panel = VideoPanel(self._playback)
        self._channel_panel = ChannelPanel(self._playback)

        self._central_splitter.addWidget(self._video_panel)
        self._central_splitter.addWidget(self._channel_panel)
        self._central_splitter.setSizes([420, 280])
        central_layout.addWidget(self._central_splitter)

        # Playback bar
        central_layout.addWidget(self._build_playback_bar())

        # Dock: lap panel (left)
        self._lap_panel = LapPanel(self._playback)
        lap_dock = QDockWidget("Laps", self)
        lap_dock.setObjectName("dock_laps")
        lap_dock.setWidget(self._lap_panel)
        lap_dock.setMinimumWidth(220)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, lap_dock)

        # Dock: track map (right)
        self._track_map_panel = TrackMapPanel(self._playback)
        map_dock = QDockWidget("Track Map", self)
        map_dock.setObjectName("dock_map")
        map_dock.setWidget(self._track_map_panel)
        map_dock.setMinimumWidth(280)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, map_dock)

        # Status bar
        self._status = QStatusBar()
        self._status_label = QLabel("Ready — open a file to begin")
        self._status_label.setStyleSheet("padding: 0 8px;")
        self._status.addWidget(self._status_label, 1)
        self.setStatusBar(self._status)

    def _build_playback_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("playback_bar")
        bar.setFixedHeight(48)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(6)

        # Transport buttons
        self._btn_to_start = QPushButton("⏮")
        self._btn_step_back = QPushButton("⏪")
        self._btn_play = QPushButton("▶")
        self._btn_play.setObjectName("btn_play")
        self._btn_step_fwd  = QPushButton("⏩")
        self._btn_to_end    = QPushButton("⏭")

        for btn in (
            self._btn_to_start, self._btn_step_back,
            self._btn_play,
            self._btn_step_fwd, self._btn_to_end,
        ):
            btn.setFixedSize(36, 30)
            layout.addWidget(btn)

        layout.addSpacing(8)

        # Playback rate
        self._rate_combo = QComboBox()
        self._rate_combo.addItems(["½×", "1×", "2×", "4×", "8×"])
        self._rate_combo.setCurrentIndex(1)
        self._rate_combo.setFixedWidth(56)
        self._rate_combo.setFixedHeight(28)
        layout.addWidget(self._rate_combo)

        layout.addSpacing(8)

        # Time display (current)
        self._time_label = QLabel("0:00.000")
        self._time_label.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 15px; "
            "font-weight: bold; color: #ff6600; min-width: 80px;"
        )
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._time_label)

        layout.addSpacing(4)
        sep = QLabel("/")
        sep.setStyleSheet("color: #505050; font-size: 13px;")
        layout.addWidget(sep)
        layout.addSpacing(4)

        # Time display (total)
        self._total_label = QLabel("0:00.000")
        self._total_label.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 13px; "
            "color: #606060; min-width: 70px;"
        )
        layout.addWidget(self._total_label)

        layout.addSpacing(8)

        # Scrub slider
        self._scrubber = QSlider(Qt.Orientation.Horizontal)
        self._scrubber.setRange(0, 10000)
        self._scrubber.setValue(0)
        layout.addWidget(self._scrubber, 1)

        # Speed display
        self._speed_label = QLabel("0 km/h")
        self._speed_label.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 13px; "
            "color: #00c040; min-width: 80px;"
        )
        self._speed_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._speed_label)

        return bar

    # ------------------------------------------------------------------
    # Menus
    # ------------------------------------------------------------------

    def _build_menus(self) -> None:
        menubar = self.menuBar()

        # File
        file_menu = menubar.addMenu("&File")

        open_act = QAction("&Open Data File…", self)
        open_act.setShortcut(QKeySequence.StandardKey.Open)
        open_act.triggered.connect(self.open_data_file)
        file_menu.addAction(open_act)

        open_video_act = QAction("Open &Video File…", self)
        open_video_act.setShortcut("Ctrl+Shift+O")
        open_video_act.triggered.connect(self.open_video_file)
        file_menu.addAction(open_video_act)

        file_menu.addSeparator()

        quit_act = QAction("&Quit", self)
        quit_act.setShortcut(QKeySequence.StandardKey.Quit)
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        # Laps
        laps_menu = menubar.addMenu("&Laps")

        detect_act = QAction("Auto-Detect Laps", self)
        detect_act.setShortcut("Ctrl+D")
        detect_act.triggered.connect(self._auto_detect_laps)
        laps_menu.addAction(detect_act)

        set_finish_act = QAction("Set Finish Line on Map…", self)
        set_finish_act.triggered.connect(
            lambda: self._track_map_panel._btn_finish.setChecked(True)
        )
        laps_menu.addAction(set_finish_act)

        # View
        view_menu = menubar.addMenu("&View")

        reset_layout_act = QAction("Reset Layout", self)
        reset_layout_act.triggered.connect(self._reset_layout)
        view_menu.addAction(reset_layout_act)

        # Help
        help_menu = menubar.addMenu("&Help")
        about_act = QAction("&About", self)
        about_act.triggered.connect(self._show_about)
        help_menu.addAction(about_act)

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> None:
        tb = self.addToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))

        open_btn = tb.addAction("Open File")
        open_btn.triggered.connect(self.open_data_file)

        open_video_btn = tb.addAction("Open Video")
        open_video_btn.triggered.connect(self.open_video_file)

        tb.addSeparator()

        detect_btn = tb.addAction("Detect Laps")
        detect_btn.triggered.connect(self._auto_detect_laps)

    # ------------------------------------------------------------------
    # Signal connections
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        pb = self._playback

        pb.session_loaded.connect(self._on_session_loaded)
        pb.time_changed.connect(self._on_time_changed)
        pb.laps_updated.connect(self._on_laps_updated)

        self._btn_play.clicked.connect(pb.toggle_play)
        self._btn_to_start.clicked.connect(lambda: pb.seek(0.0))
        self._btn_to_end.clicked.connect(lambda: pb.seek(pb.duration))
        self._btn_step_back.clicked.connect(lambda: pb.step_frame(-1))
        self._btn_step_fwd.clicked.connect(lambda: pb.step_frame(1))

        self._scrubber.sliderPressed.connect(self._on_scrubber_pressed)
        self._scrubber.sliderMoved.connect(self._on_scrubber_moved)

        self._rate_combo.currentIndexChanged.connect(self._on_rate_changed)

        self._lap_panel.laps_compare_changed.connect(self._on_compare_changed)

        self._track_map_panel.finish_line_set.connect(self._on_finish_line_set)

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    def open_data_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Data / Video File",
            "",
            "Supported files (*.mp4 *.vbo *.csv *.txt);;"
            "Smartycam3 MP4 — video + telemetry (*.mp4);;"
            "VBOX files (*.vbo);;"
            "Race Studio 3 CSV export (*.csv *.txt);;"
            "All files (*.*)",
        )
        if not path:
            return
        self._load_file(path)

    def open_video_file(self) -> None:
        if self._session is None:
            QMessageBox.information(
                self, "No Session",
                "Load a data file first, then attach a video."
            )
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Video File", "", "MP4 video (*.mp4);;All files (*.*)"
        )
        if not path:
            return
        try:
            attach_video(self._session, path)
            self._playback.load_session(self._session)
            self._status_label.setText(f"Video attached: {os.path.basename(path)}")
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _load_file(self, path: str) -> None:
        try:
            session = load_file(path)
        except Exception as exc:
            QMessageBox.critical(self, "Load Error", str(exc))
            return

        # Auto-detect laps
        detect_laps(session)

        self._session = session
        self._playback.load_session(session)
        self.setWindowTitle(
            f"{self.APP_TITLE} — {os.path.basename(path)}"
        )

    # ------------------------------------------------------------------
    # Playback event handlers
    # ------------------------------------------------------------------

    def _on_session_loaded(self, session: Session) -> None:
        duration = session.duration
        self._total_label.setText(self._format_time(duration))
        self._btn_play.setText("▶")
        laps = len(session.laps)
        tel = session.metadata.get("telemetry", "?")
        self._status_label.setText(
            f"Loaded  |  Duration: {self._format_time(duration)}"
            f"  |  Samples: {session.sample_count}"
            f"  |  Laps: {laps}"
            f"  |  Source: {os.path.basename(session.source_file or '')}"
        )

    def _on_time_changed(self, t: float) -> None:
        self._time_label.setText(self._format_time(t))

        # Update scrubber (avoid feedback)
        duration = self._playback.duration
        if duration > 0:
            frac = (t - self._playback.time_offset) / duration
            self._scrubber.blockSignals(True)
            self._scrubber.setValue(int(frac * 10000))
            self._scrubber.blockSignals(False)

        # Update play/pause label
        self._btn_play.setText("❚❚" if self._playback.playing else "▶")

        # Live speed readout
        if self._session:
            from ..data.session import CH_SPEED
            spd = self._session.value_at_time(CH_SPEED, t)
            if spd is not None:
                self._speed_label.setText(f"{spd:.0f} km/h")

    def _on_laps_updated(self) -> None:
        if self._session:
            n = len(self._session.laps)
            self._status_label.setText(
                f"Detected {n} lap{'s' if n != 1 else ''}"
            )

    # ------------------------------------------------------------------
    # Scrubber / rate
    # ------------------------------------------------------------------

    def _on_scrubber_pressed(self) -> None:
        self._playback.pause()

    def _on_scrubber_moved(self, value: int) -> None:
        frac = value / 10000.0
        self._playback.seek_fraction(frac)

    def _on_rate_changed(self, index: int) -> None:
        rates = [0.5, 1.0, 2.0, 4.0, 8.0]
        self._playback.set_rate(rates[index])

    # ------------------------------------------------------------------
    # Lap / comparison
    # ------------------------------------------------------------------

    def _auto_detect_laps(self) -> None:
        if self._session is None:
            return
        detect_laps(self._session)
        self._playback.notify_laps_updated()

    def _on_compare_changed(self, lap_numbers: list[int]) -> None:
        self._track_map_panel.set_compare_laps(lap_numbers)
        self._channel_panel.set_compare_laps(lap_numbers)
        self._video_panel.set_compare_laps(lap_numbers)

    def _on_finish_line_set(self, lat: float, lon: float) -> None:
        if self._session is None:
            return
        set_finish_from_track_click(self._session, lat, lon)
        self._playback.notify_laps_updated()
        self._status_label.setText(
            f"Finish line set at {lat:.6f}, {lon:.6f}"
        )

    # ------------------------------------------------------------------
    # View
    # ------------------------------------------------------------------

    def _reset_layout(self) -> None:
        self._central_splitter.setSizes([420, 280])

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "Circuit Tools",
            "<b>Circuit Tools</b> — AIM Smartycam3 Track Analyser<br><br>"
            "Supported formats: .mp4 (Smartycam3 video + telemetry), "
            ".vbo (VBOX), .csv (Race Studio 3 export)<br><br>"
            "UI modelled after Vbox Circuit Tools.",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_time(t: float) -> str:
        t = max(0.0, float(t))
        minutes = int(t // 60)
        seconds = t % 60
        return f"{minutes}:{seconds:06.3f}"

    # ------------------------------------------------------------------
    # Keyboard shortcuts
    # ------------------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Space:
            self._playback.toggle_play()
        elif key == Qt.Key.Key_Left:
            self._playback.step_frame(-1)
        elif key == Qt.Key.Key_Right:
            self._playback.step_frame(1)
        elif key == Qt.Key.Key_Home:
            self._playback.seek(0.0)
        elif key == Qt.Key.Key_End:
            self._playback.seek(self._playback.duration)
        else:
            super().keyPressEvent(event)
