"""
PlaybackController — shared time-axis synchronisation across all panels.

All panels receive signals from this controller and update their display
accordingly.  Emits:
  time_changed(float)       current playback position in seconds
  session_loaded(Session)   a new session has been opened
  laps_updated()            lap list has been re-detected
  lap_selected(int)         user selected a lap by number (0 = full session)
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from ..data.session import Session, Lap


class PlaybackController(QObject):

    time_changed = pyqtSignal(float)
    play_state_changed = pyqtSignal(bool)  # True = playing, False = paused
    session_loaded = pyqtSignal(object)    # Session
    laps_updated = pyqtSignal()
    lap_selected = pyqtSignal(int)

    # Playback rates: 1× real-time, 2×, 4×, ½×
    RATES = (0.5, 1.0, 2.0, 4.0, 8.0)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._session: Session | None = None
        self._current_time: float = 0.0
        self._playing: bool = False
        self._rate: float = 1.0
        self._active_lap: int = 0    # 0 = full session

        self._timer = QTimer(self)
        self._timer.setInterval(33)   # ~30 fps update
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def session(self) -> Session | None:
        return self._session

    @property
    def current_time(self) -> float:
        return self._current_time

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def rate(self) -> float:
        return self._rate

    @property
    def duration(self) -> float:
        if self._session is None:
            return 0.0
        if self._active_lap != 0:
            lap = self._get_lap(self._active_lap)
            if lap:
                return lap.lap_time
        return self._session.duration

    @property
    def time_offset(self) -> float:
        """Start time of the current view window (0 for full session)."""
        if self._active_lap != 0:
            lap = self._get_lap(self._active_lap)
            if lap:
                return lap.start_time
        return 0.0

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    def load_session(self, session: Session) -> None:
        self._session = session
        self._current_time = 0.0
        self._playing = False
        self._active_lap = 0
        self._timer.stop()
        self.session_loaded.emit(session)
        self.time_changed.emit(self._current_time)

    # ------------------------------------------------------------------
    # Playback controls
    # ------------------------------------------------------------------

    def play(self) -> None:
        if self._session is None:
            return
        self._playing = True
        self._timer.start()
        self.play_state_changed.emit(True)

    def pause(self) -> None:
        self._playing = False
        self._timer.stop()
        self.play_state_changed.emit(False)

    def toggle_play(self) -> None:
        if self._playing:
            self.pause()
        else:
            self.play()

    def seek(self, t: float) -> None:
        """Seek to absolute session time t."""
        if self._session is None:
            return
        t = max(0.0, min(t, self._session.duration))
        self._current_time = t
        self.time_changed.emit(t)

    def seek_fraction(self, fraction: float) -> None:
        """Seek to a fraction [0, 1] of the current view window."""
        self.seek(self.time_offset + fraction * self.duration)

    def set_rate(self, rate: float) -> None:
        self._rate = rate

    def step_frame(self, direction: int = 1) -> None:
        """Advance or rewind by one data sample."""
        if self._session is None:
            return
        t = self._current_time
        idx = self._session.idx_at_time(t) + direction
        idx = max(0, min(idx, self._session.sample_count - 1))
        self.seek(float(self._session.time[idx]))

    # ------------------------------------------------------------------
    # Lap management
    # ------------------------------------------------------------------

    def select_lap(self, lap_number: int) -> None:
        """Focus the view on a specific lap (0 = full session)."""
        self._active_lap = lap_number
        if lap_number != 0:
            lap = self._get_lap(lap_number)
            if lap:
                self.seek(lap.start_time)
        else:
            self.seek(0.0)
        self.lap_selected.emit(lap_number)

    def notify_laps_updated(self) -> None:
        self.laps_updated.emit()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        if self._session is None:
            return
        dt = self._timer.interval() / 1000.0 * self._rate
        new_time = self._current_time + dt
        end_time = self.time_offset + self.duration
        if new_time >= end_time:
            new_time = end_time
            self.pause()
        self._current_time = new_time
        self.time_changed.emit(new_time)

    def _get_lap(self, lap_number: int) -> Lap | None:
        if self._session is None:
            return None
        for lap in self._session.laps:
            if lap.number == lap_number:
                return lap
        return None
