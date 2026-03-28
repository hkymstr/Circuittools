#!/usr/bin/env python3
"""
Circuit Tools — AIM Smartycam3 Track Analyser
Entry point.

Usage:
    python main.py [file]

    file  Optional path to a data file (.mp4, .vbo, .csv) to open on startup.
"""
import sys
import os

# Ensure the project root is on sys.path so `src` is importable
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from src.ui import style
from src.ui.main_window import MainWindow


def main() -> int:
    # High-DPI support
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Circuit Tools")
    app.setOrganizationName("CircuitTools")

    style.apply(app)

    window = MainWindow()
    window.show()

    # Open file passed on command line
    if len(sys.argv) > 1:
        path = sys.argv[1]
        if os.path.isfile(path):
            window._load_file(path)
        else:
            print(f"Warning: file not found: {path}", file=sys.stderr)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
