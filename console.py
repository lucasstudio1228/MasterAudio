"""Bật UTF-8 cho stdout/stderr (Windows console mặc định là cp1252)."""
import sys


def enable_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


enable_utf8()
