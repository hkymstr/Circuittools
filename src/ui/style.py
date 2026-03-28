"""
Qt stylesheet and colour palette for Circuit Tools.

Design language mirrors Vbox Circuit Tools:
  - Very dark background (#1c1c1c)
  - Mid-grey panels and toolbars (#2a2a2a / #333333)
  - White/light-grey text
  - Green accent for active / best lap  (#00c040)
  - Orange accent for current-lap cursor (#ff6600)
  - Red for worst values; blue for comparison laps
  - Monospace font for all numeric readouts
"""

# ---------------------------------------------------------------------------
# Colour constants (use these throughout the UI code)
# ---------------------------------------------------------------------------
BG_DARK      = "#1c1c1c"
BG_PANEL     = "#242424"
BG_TOOLBAR   = "#2a2a2a"
BG_HEADER    = "#1a1a1a"
BORDER       = "#3a3a3a"
BORDER_LIGHT = "#4a4a4a"

TEXT_PRIMARY   = "#e8e8e8"
TEXT_SECONDARY = "#909090"
TEXT_DIM       = "#505050"

ACCENT_GREEN  = "#00c040"   # best lap, GPS track default
ACCENT_ORANGE = "#ff6600"   # cursor / current position
ACCENT_BLUE   = "#2080ff"   # reference / comparison lap
ACCENT_RED    = "#e03030"   # worst / over-limit
ACCENT_YELLOW = "#ffc800"   # warning / highlight

# Track-map speed colourmap endpoints (slow → fast)
SPEED_COLD = "#0040ff"   # blue = slow
SPEED_HOT  = "#ff0000"   # red = fast

# Lap list row colours
LAP_BEST_BG     = "#0d2e18"
LAP_BEST_FG     = ACCENT_GREEN
LAP_SELECTED_BG = "#1a2a3a"
LAP_ALT_BG      = "#202020"


# ---------------------------------------------------------------------------
# Main stylesheet
# ---------------------------------------------------------------------------
STYLESHEET = f"""
/* ── Global ────────────────────────────────────────────────────────── */
QWidget {{
    background-color: {BG_DARK};
    color: {TEXT_PRIMARY};
    font-family: "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif;
    font-size: 11px;
}}

/* ── Main window / dock areas ──────────────────────────────────────── */
QMainWindow {{
    background-color: {BG_HEADER};
}}
QMainWindow::separator {{
    background: {BORDER};
    width: 3px;
    height: 3px;
}}

/* ── Dock widgets ──────────────────────────────────────────────────── */
QDockWidget {{
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
    color: {TEXT_PRIMARY};
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
}}
QDockWidget::title {{
    background: {BG_TOOLBAR};
    padding: 4px 8px;
    border-bottom: 1px solid {BORDER};
}}
QDockWidget::close-button,
QDockWidget::float-button {{
    background: transparent;
    border: none;
    padding: 2px;
}}
QDockWidget::close-button:hover,
QDockWidget::float-button:hover {{
    background: {BORDER_LIGHT};
    border-radius: 2px;
}}

/* ── Menu bar ──────────────────────────────────────────────────────── */
QMenuBar {{
    background-color: {BG_HEADER};
    border-bottom: 1px solid {BORDER};
    padding: 2px 0;
}}
QMenuBar::item {{
    padding: 4px 10px;
    background: transparent;
}}
QMenuBar::item:selected {{
    background: {BORDER_LIGHT};
    border-radius: 3px;
}}
QMenu {{
    background-color: {BG_TOOLBAR};
    border: 1px solid {BORDER_LIGHT};
    padding: 4px 0;
}}
QMenu::item {{
    padding: 5px 20px 5px 12px;
}}
QMenu::item:selected {{
    background: {ACCENT_BLUE};
    color: #ffffff;
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 3px 8px;
}}

/* ── Toolbar ───────────────────────────────────────────────────────── */
QToolBar {{
    background-color: {BG_TOOLBAR};
    border-bottom: 1px solid {BORDER};
    spacing: 4px;
    padding: 2px 4px;
}}
QToolBar::separator {{
    width: 1px;
    background: {BORDER_LIGHT};
    margin: 4px 3px;
}}
QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 4px 8px;
    color: {TEXT_PRIMARY};
}}
QToolButton:hover {{
    background: {BORDER_LIGHT};
    border-color: {BORDER_LIGHT};
}}
QToolButton:pressed {{
    background: {ACCENT_BLUE};
    border-color: {ACCENT_BLUE};
    color: #ffffff;
}}
QToolButton:checked {{
    background: {BG_DARK};
    border-color: {ACCENT_GREEN};
    color: {ACCENT_GREEN};
}}

/* ── Status bar ────────────────────────────────────────────────────── */
QStatusBar {{
    background: {BG_HEADER};
    border-top: 1px solid {BORDER};
    font-size: 10px;
    color: {TEXT_SECONDARY};
}}

/* ── Playback / scrubber frame ─────────────────────────────────────── */
#playback_bar {{
    background: {BG_TOOLBAR};
    border-top: 1px solid {BORDER};
    min-height: 48px;
    max-height: 48px;
}}

/* ── Slider (time scrubber) ────────────────────────────────────────── */
QSlider::groove:horizontal {{
    height: 4px;
    background: {BORDER_LIGHT};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT_ORANGE};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 12px;
    height: 12px;
    margin: -4px 0;
    background: {ACCENT_ORANGE};
    border: 2px solid {BG_DARK};
    border-radius: 6px;
}}
QSlider::handle:horizontal:hover {{
    background: #ff8833;
}}

/* ── Push buttons ──────────────────────────────────────────────────── */
QPushButton {{
    background: {BG_TOOLBAR};
    border: 1px solid {BORDER_LIGHT};
    border-radius: 4px;
    padding: 4px 12px;
    color: {TEXT_PRIMARY};
}}
QPushButton:hover {{
    background: {BORDER_LIGHT};
    border-color: #606060;
}}
QPushButton:pressed {{
    background: {ACCENT_BLUE};
    border-color: {ACCENT_BLUE};
    color: #ffffff;
}}
QPushButton#btn_play {{
    background: {ACCENT_GREEN};
    border-color: {ACCENT_GREEN};
    color: #000000;
    font-weight: bold;
    min-width: 56px;
}}
QPushButton#btn_play:hover {{
    background: #00e050;
}}

/* ── ComboBox ──────────────────────────────────────────────────────── */
QComboBox {{
    background: {BG_TOOLBAR};
    border: 1px solid {BORDER_LIGHT};
    border-radius: 3px;
    padding: 3px 8px;
    min-width: 100px;
}}
QComboBox:hover {{
    border-color: #606060;
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox QAbstractItemView {{
    background: {BG_TOOLBAR};
    border: 1px solid {BORDER_LIGHT};
    selection-background-color: {ACCENT_BLUE};
}}

/* ── Table widget (lap list) ───────────────────────────────────────── */
QTableWidget {{
    background: {BG_PANEL};
    gridline-color: {BORDER};
    border: none;
    selection-background-color: {LAP_SELECTED_BG};
    alternate-background-color: {LAP_ALT_BG};
}}
QTableWidget::item {{
    padding: 3px 6px;
    border: none;
}}
QTableWidget::item:selected {{
    background: {LAP_SELECTED_BG};
    color: #ffffff;
}}
QHeaderView::section {{
    background: {BG_TOOLBAR};
    color: {TEXT_SECONDARY};
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.4px;
    text-transform: uppercase;
    padding: 4px 6px;
    border: none;
    border-bottom: 1px solid {BORDER};
    border-right: 1px solid {BORDER};
}}
QHeaderView::section:hover {{
    background: {BORDER_LIGHT};
}}

/* ── Scroll bars ───────────────────────────────────────────────────── */
QScrollBar:vertical {{
    background: {BG_DARK};
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_LIGHT};
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{
    background: #606060;
}}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: {BG_DARK};
    height: 8px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER_LIGHT};
    border-radius: 4px;
    min-width: 20px;
}}
QScrollBar::handle:horizontal:hover {{
    background: #606060;
}}
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ── Numeric LCD / telemetry labels ────────────────────────────────── */
QLabel#tele_value {{
    font-family: "Consolas", "Courier New", monospace;
    font-size: 22px;
    font-weight: bold;
    color: {ACCENT_GREEN};
}}
QLabel#tele_unit {{
    font-size: 10px;
    color: {TEXT_SECONDARY};
}}
QLabel#tele_label {{
    font-size: 9px;
    color: {TEXT_DIM};
    letter-spacing: 0.5px;
    text-transform: uppercase;
}}

/* ── Video overlay widget ──────────────────────────────────────────── */
#video_overlay {{
    background: transparent;
}}

/* ── Splitters ─────────────────────────────────────────────────────── */
QSplitter::handle {{
    background: {BORDER};
}}
QSplitter::handle:horizontal {{
    width: 3px;
}}
QSplitter::handle:vertical {{
    height: 3px;
}}

/* ── Tab bar (channel selector) ────────────────────────────────────── */
QTabWidget::pane {{
    border: 1px solid {BORDER};
    background: {BG_PANEL};
}}
QTabBar::tab {{
    background: {BG_TOOLBAR};
    border: 1px solid {BORDER};
    border-bottom: none;
    padding: 5px 12px;
    color: {TEXT_SECONDARY};
}}
QTabBar::tab:selected {{
    background: {BG_PANEL};
    color: {TEXT_PRIMARY};
    border-top: 2px solid {ACCENT_ORANGE};
}}
QTabBar::tab:hover {{
    background: {BORDER_LIGHT};
    color: {TEXT_PRIMARY};
}}

/* ── Check box ─────────────────────────────────────────────────────── */
QCheckBox {{
    spacing: 6px;
    color: {TEXT_PRIMARY};
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {BORDER_LIGHT};
    border-radius: 2px;
    background: {BG_TOOLBAR};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT_GREEN};
    border-color: {ACCENT_GREEN};
}}

/* ── Group box ─────────────────────────────────────────────────────── */
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 4px;
    margin-top: 6px;
    padding-top: 8px;
    font-size: 10px;
    color: {TEXT_SECONDARY};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}}

/* ── Frame (panel containers) ──────────────────────────────────────── */
QFrame#panel_frame {{
    background: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 2px;
}}

/* ── Tool tip ──────────────────────────────────────────────────────── */
QToolTip {{
    background: {BG_TOOLBAR};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_LIGHT};
    padding: 3px 6px;
    font-size: 11px;
}}
"""


def apply(app) -> None:  # type: ignore[type-arg]
    """Apply the stylesheet to a QApplication."""
    app.setStyleSheet(STYLESHEET)
