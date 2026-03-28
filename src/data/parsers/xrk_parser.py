"""
AIM .xrk / .drk data file parser.

The .xrk format is AIM's native data logger format produced by devices such
as the Solo 2 DL, MX-Series loggers, and EVO5.  It is a binary format that
can be paired with a Smartycam3 .mp4 file for combined analysis in Race
Studio 3.

Official reading method (Windows only):
  AIM provides a proprietary DLL  (xdrk.dll / xdrk64.dll) with a C API.
  On Linux/macOS there is no official SDK.

Current support status: PARTIAL
  • File detection is implemented (.xrk, .xdr, .drk extensions)
  • Actual binary parsing requires either:
      a) The official Windows DLL via ctypes (Windows only), or
      b) A complete reverse-engineered parser (not yet available).

Workaround (recommended):
  Export your session from Race Studio 3 as CSV, then load the CSV file.

To add full .xrk support:
  1. Implement xdrk.dll interop in _parse_with_dll() below (Windows only).
  2. Or implement the binary format directly in _parse_binary() — the format
     is documented at: https://github.com/gotzl/xdrk  (community effort)
"""
from __future__ import annotations

import os
from .base import BaseParser
from ..session import Session


class XrkParser(BaseParser):

    def can_parse(self, path: str) -> bool:
        ext = os.path.splitext(path)[1].lower()
        return ext in (".xrk", ".xdr", ".drk")

    def parse(self, path: str) -> Session:
        # --- Attempt 1: xdrk DLL (Windows only) ---
        try:
            return self._parse_with_dll(path)
        except NotImplementedError:
            pass

        # --- Attempt 2: community binary parser ---
        try:
            return self._parse_binary(path)
        except NotImplementedError:
            pass

        raise NotImplementedError(
            f"Cannot parse {os.path.basename(path)}.\n\n"
            "AIM .xrk/.drk files require the AIM xdrk library (Windows only) "
            "or a manual export from Race Studio 3 as CSV.\n\n"
            "Workaround: In Race Studio 3, right-click the session and choose "
            "'Export → CSV', then open that CSV file here."
        )

    # ------------------------------------------------------------------

    def _parse_with_dll(self, path: str) -> Session:
        """
        Use AIM's official xdrk.dll via ctypes.
        Only works on Windows with the DLL installed alongside Race Studio 3.
        """
        import sys
        if sys.platform != "win32":
            raise NotImplementedError

        # Typical Race Studio 3 installation path
        dll_paths = [
            r"C:\Program Files (x86)\Race Studio 3\xdrk64.dll",
            r"C:\Program Files\Race Studio 3\xdrk64.dll",
        ]
        import ctypes
        dll = None
        for p in dll_paths:
            if os.path.isfile(p):
                dll = ctypes.CDLL(p)
                break
        if dll is None:
            raise NotImplementedError("xdrk64.dll not found")

        # The xdrk API is documented in AIM's developer kit.
        # Functions: open_file(), get_laps_count(), get_lap_info(),
        #            get_channels_count(), get_channel_name(),
        #            get_channel_samples(), close_file()
        raise NotImplementedError("xdrk DLL integration not yet implemented")

    def _parse_binary(self, path: str) -> Session:
        """
        Community reverse-engineered binary parser.
        See https://github.com/gotzl/xdrk for format details.
        """
        raise NotImplementedError("Binary .xrk parser not yet implemented")
