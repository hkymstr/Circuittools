"""
Auto-detecting file loader — picks the right parser and returns a Session.
"""
from __future__ import annotations

import os
from ..session import Session
from .csv_parser import CsvParser
from .vbo_parser import VboParser
from .mp4_parser import Mp4Parser

_PARSERS = [Mp4Parser(), VboParser(), CsvParser()]


def load_file(path: str) -> Session:
    """Load a data or video file and return a populated Session."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")

    for parser in _PARSERS:
        if parser.can_parse(path):
            session = parser.parse(path)
            session.source_file = path
            return session

    raise ValueError(
        f"Unsupported file type: {os.path.splitext(path)[1]}\n"
        "Supported formats: .mp4, .vbo, .csv, .txt"
    )


def attach_video(session: Session, video_path: str) -> None:
    """Associate an .mp4 video file with an existing data session."""
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")
    session.video_path = video_path
