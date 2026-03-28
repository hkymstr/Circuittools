# Circuit Tools — Architecture & Feature Specification

> Reference document for developers adding new features or modifying behaviour.
> Keep this file up-to-date whenever a module's public API changes.

---

## 1. Overview

Circuit Tools is a desktop application for analysing motorsport session data
recorded by an AIM Smartycam3 (or any compatible device).  The UI is modelled
after Vbox Circuit Tools: four synchronized dockable panels driven by a single
playback timeline.

**Primary supported formats**
| File type | Extension | Notes |
|-----------|-----------|-------|
| AIM Smartycam3 video + embedded telemetry | `.mp4` | **Single file contains both H.264 video and all telemetry channels** (GPS, 3-axis accel, CAN/ECU data). Written directly to the MicroSD card. |
| VBOX data logger | `.vbo` | Text-based, Racelogic format |
| Race Studio 3 CSV export | `.csv` / `.txt` | AIM export from Race Studio 3 software |

> **Smartycam3 `.mp4` workflow:** Drop the `.mp4` from the SD card directly into File → Open.
> The app loads video playback and all telemetry from the same file.  No separate data file needed.
> If the embedded telemetry track format differs from the assumed layout, run the diagnostic tool
> (see §7) to inspect the actual MP4 atom tree.

---

## 2. File / Directory Layout

```
Circuittools/
├── main.py                        Entry point; creates QApplication + MainWindow
├── requirements.txt               Python package dependencies
├── SPEC.md                        This document
└── src/
    ├── data/
    │   ├── session.py             Data models: Session, Lap, channel constants
    │   ├── lap_detector.py        Lap detection algorithms
    │   └── parsers/
    │       ├── base.py            Abstract BaseParser
    │       ├── csv_parser.py      CSV / Race Studio 3 parser
    │       ├── vbo_parser.py      VBOX .vbo parser
    │       ├── mp4_parser.py      MP4 embedded-telemetry parser + CLI diagnostic
    │       └── loader.py          Auto-detecting load_file() + attach_video()
    └── ui/
        ├── style.py               Qt stylesheet + colour constants
        ├── playback.py            PlaybackController (shared timeline)
        ├── main_window.py         QMainWindow: menus, toolbar, dock layout
        └── panels/
            ├── video_panel.py     Video playback + telemetry overlay
            ├── track_map_panel.py GPS track map (pyqtgraph)
            ├── channel_panel.py   Stacked channel graphs (pyqtgraph)
            └── lap_panel.py       Lap timing table
```

---

## 3. Data Layer

### 3.1 `Session` (`src/data/session.py`)

Central data container.  All panels read from it; parsers write to it.

```python
session.channels: dict[str, np.ndarray]  # equal-length float64 arrays
session.laps:     list[Lap]
session.metadata: dict[str, str]         # free-form key/value from file header
session.video_path: str | None           # path to associated .mp4
session.video_offset: float              # seconds; data_time = video_time + offset
```

**Canonical channel names** (constants in `session.py`):

| Constant | Name | Unit |
|----------|------|------|
| `CH_TIME` | `"time"` | seconds from session start |
| `CH_LAT` | `"latitude"` | decimal degrees |
| `CH_LON` | `"longitude"` | decimal degrees |
| `CH_SPEED` | `"speed"` | km/h |
| `CH_HEADING` | `"heading"` | degrees 0–360 |
| `CH_AX` | `"accel_x"` | G, longitudinal (+ = braking) |
| `CH_AY` | `"accel_y"` | G, lateral (+ = right) |
| `CH_AZ` | `"accel_z"` | G, vertical |
| `CH_RPM` | `"rpm"` | rpm |
| `CH_GEAR` | `"gear"` | integer |
| `CH_THROTTLE` | `"throttle"` | % 0–100 |
| `CH_BRAKE` | `"brake"` | % 0–100 |
| `CH_HEIGHT` | `"height"` | metres ASL |

Parsers **must** use these constants to ensure all panels find the right data.
Unknown columns are stored under their original string names and will still
appear in the channel panel (just without predefined scaling or colour).

### 3.2 `Lap` (`src/data/session.py`)

```python
lap.number:     int
lap.start_idx:  int    # index into session.channels arrays
lap.end_idx:    int
lap.start_time: float  # seconds
lap.end_time:   float
lap.lap_time:   float  # computed property
lap.lap_time_str: str  # e.g. "1:23.456"
```

### 3.3 Adding a New Parser

1. Create `src/data/parsers/my_format_parser.py`.
2. Subclass `BaseParser` and implement `can_parse(path)` and `parse(path)`.
3. `parse()` must populate `session.channels[CH_TIME]` at minimum.
4. Register the parser in `src/data/parsers/loader.py` by adding it to `_PARSERS`.

```python
# loader.py
from .my_format_parser import MyFormatParser
_PARSERS = [Mp4Parser(), VboParser(), CsvParser(), MyFormatParser()]
```

---

## 4. Playback Layer (`src/ui/playback.py`)

`PlaybackController` is a `QObject` that holds the single source of truth for
the current playback position.

**Signals**

| Signal | Payload | Description |
|--------|---------|-------------|
| `session_loaded` | `Session` | Emitted once after `load_session()` |
| `time_changed` | `float` | Current time in seconds; ~30 Hz during playback |
| `laps_updated` | — | Lap list was re-detected |
| `lap_selected` | `int` | User focused a specific lap (0 = full session) |

**Key methods**

```python
controller.load_session(session)    # replace active session
controller.play() / pause()
controller.toggle_play()
controller.seek(t)                  # absolute seconds
controller.seek_fraction(f)         # 0.0–1.0 within current view window
controller.step_frame(±1)           # advance/rewind one sample
controller.set_rate(float)          # 0.5 / 1.0 / 2.0 / 4.0 / 8.0
controller.select_lap(lap_number)   # zoom to a lap (0 = full session)
controller.notify_laps_updated()    # call after modifying session.laps
```

All panels receive `session_loaded` and `time_changed` from this controller.
**Do not** have panels call each other directly.

---

## 5. UI Layer

### 5.1 Colour Palette (`src/ui/style.py`)

| Constant | Hex | Use |
|----------|-----|-----|
| `BG_DARK` | `#1c1c1c` | Main background |
| `BG_PANEL` | `#242424` | Panel interiors |
| `BG_TOOLBAR` | `#2a2a2a` | Toolbars, headers |
| `ACCENT_GREEN` | `#00c040` | Best lap, track line, GPS |
| `ACCENT_ORANGE` | `#ff6600` | Playback cursor, current position |
| `ACCENT_BLUE` | `#2080ff` | Reference/comparison lap |
| `ACCENT_RED` | `#e03030` | Over-limit, worst values |

Import colour constants rather than hard-coding hex strings in panel code.

### 5.2 Adding a New Panel

1. Create `src/ui/panels/my_panel.py` as a `QWidget` subclass.
2. Accept `playback: PlaybackController` in `__init__`.
3. Connect to `playback.session_loaded` and `playback.time_changed`.
4. In `main_window.py`, wrap in a `QDockWidget` and add with `addDockWidget()`.

```python
# main_window.py  (_build_ui)
self._my_panel = MyPanel(self._playback)
my_dock = QDockWidget("My Panel", self)
my_dock.setWidget(self._my_panel)
self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, my_dock)
```

### 5.3 Video Sync

The `VideoPanel` uses `QMediaPlayer` for playback.

```
data_time = video_time + session.video_offset
```

`video_offset` is adjusted via the ◀/▶ buttons in the video panel toolbar.
It is saved in the `Session` object and persists as long as the session is open.
To persist across runs, serialise `session.video_offset` to a sidecar file
(not yet implemented — good first feature to add).

---

## 6. Lap Detection (`src/data/lap_detector.py`)

Two strategies are available:

**Finish-line crossing** (preferred when circuit is known):
```python
detect_laps(session, finish_lat=51.5123, finish_lon=-0.2345)
```

**Auto-detect** (no prior knowledge):
```python
detect_laps(session)   # uses GPS density to find start/finish area
```

**From a map click:**
```python
set_finish_from_track_click(session, click_lat, click_lon)
```

After calling any of these, call `playback.notify_laps_updated()` to refresh
all panels.

---

## 7. MP4 Telemetry (`src/data/parsers/mp4_parser.py`)

### Diagnosing an Unknown MP4 Format

Run the built-in CLI tool to print the MP4 atom tree:

```bash
python -m src.data.parsers.mp4_parser /path/to/file.mp4
```

Look for non-standard track types in the output (anything other than
`vide`, `soun`, `text`).  The AIM Smartycam3 typically has a `data` or
proprietary track containing telemetry.

### Updating the AIM Binary Record Format

If the sample file reveals a different binary layout, update these constants
in `mp4_parser.py`:

```python
_AIM_RECORD_FMT  = "<IiihhhhhhBB"   # struct format string
_AIM_RECORD_SIZE = struct.calcsize(_AIM_RECORD_FMT)
```

Current assumed layout (little-endian, 26 bytes per sample):

| Offset | Type | Field | Scale |
|--------|------|-------|-------|
| 0 | `uint32` | timestamp_ms | ÷ 1000 → seconds |
| 4 | `int32` | latitude | ÷ 1e7 → degrees |
| 8 | `int32` | longitude | ÷ 1e7 → degrees |
| 12 | `int16` | speed_kmh | ÷ 10 → km/h |
| 14 | `int16` | heading | ÷ 10 → degrees |
| 16 | `int16` | altitude_m | ÷ 10 → metres |
| 18 | `int16` | accel_x | ÷ 1000 → G |
| 20 | `int16` | accel_y | ÷ 1000 → G |
| 22 | `int16` | accel_z | ÷ 1000 → G |
| 24 | `uint8` | satellites | — |
| 25 | `uint8` | fix_type | 0=none, 1=2D, 2=3D |

---

## 8. Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Space` | Play / Pause |
| `←` / `→` | Step one sample backward / forward |
| `Home` | Jump to session start |
| `End` | Jump to session end |
| `Ctrl+O` | Open data file |
| `Ctrl+Shift+O` | Open video file |
| `Ctrl+D` | Auto-detect laps |

---

## 9. Planned / Suggested Features

- [ ] **Sector timing** — divide each lap into sectors via multiple finish-line clicks
- [ ] **Export lap times** — CSV export of the lap table
- [ ] **Video offset persistence** — save `video_offset` to a sidecar `.json` file
- [ ] **Sector map colouring** — colour track segments by sector delta
- [ ] **Live telemetry** — connect to Smartycam3 over WiFi for real-time data
- [ ] **G-G diagram** — scatter plot of lateral vs longitudinal G
- [ ] **Channel maths** — define derived channels (e.g., combined G magnitude)
- [ ] **Dark/light theme toggle**
- [ ] **Session notes** — freeform text note per session

---

## 10. Dependencies

```
PyQt6>=6.4.0          Qt6 GUI framework + multimedia
pyqtgraph>=0.13.0     High-performance 2D plotting
numpy>=1.23.0         Array maths
pandas>=1.5.0         CSV parsing
scipy>=1.10.0         Signal processing (future use)
```

Install with:
```bash
pip install -r requirements.txt
```
