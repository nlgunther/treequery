"""units.py — parse human-written sizes and dates once, for settings and queries alike."""
import re
from datetime import datetime

_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(b|kb|mb|gb|tb)?\s*$", re.IGNORECASE)
_FACTOR = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}


def parse_size(text: str) -> int:
    """
    Bytes from '5MB', '1.5 kb' or '300' (plain numbers are bytes).

    Example:
        parse_size("5MB")   # -> 5242880
    """
    match = _SIZE.match(text)
    if not match:
        raise ValueError(f"Not a size: {text!r} (examples: 300, 5KB, 1.5MB)")
    return int(float(match.group(1)) * _FACTOR[(match.group(2) or "b").lower()])


def parse_moment(text: str) -> datetime:
    """
    Date or date-time from ISO text.

    Example:
        parse_moment("2013-05-13")   # -> datetime(2013, 5, 13, 0, 0)
    """
    try:
        return datetime.fromisoformat(text.strip())
    except ValueError as exc:
        raise ValueError(f"Not an ISO date: {text!r} (example: 2013-05-13)") from exc


def format_duration(seconds: float) -> str:
    """
    Short human duration.

    Example:
        format_duration(75)      # -> '1 min 15 s'
        format_duration(7500)    # -> '2 h 05 min'
    """
    seconds = max(0.0, seconds)
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.0f} s" if seconds >= 10 else f"{seconds:.1f} s"
    minutes, sec = divmod(round(seconds), 60)
    if minutes < 60:
        return f"{minutes} min {sec:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


def format_size(size: float) -> str:
    """
    Short human byte count (binary units, matching parse_size).

    Example:
        format_size(5 * 1024**2)    # -> '5.0 MB'
    """
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size) < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
