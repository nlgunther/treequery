"""
text.py — stage 3: read a file's text, and say plainly when we could not.

Wraps Ken's shared ``readers`` package (imported lazily, so the walk and query stages work
without it).  Every outcome is a ``TextStatus`` so unreadable files can be listed for
follow-up (OCR, conversion, download) instead of silently vanishing from the results.

Example:
    result = read_text(entry, max_chars=200_000)
    if result.status is TextStatus.OK:
        print(result.text[:200])
"""
from dataclasses import dataclass
from enum import Enum

from .walk import FileEntry


class TextStatus(Enum):
    OK = "ok"
    NOT_READ = "not_read"            # decided by name/stat alone; the text was never needed
    NO_TEXT = "no_text"              # e.g. a scanned PDF: needs OCR
    UNSUPPORTED = "unsupported"      # no reader for this format
    CLOUD_ONLY = "cloud_only"        # online-only; reading would download it
    ERROR = "error"


@dataclass(frozen=True)
class TextResult:
    status: TextStatus
    text: str = ""
    detail: str = ""


#: Extensions with no reader, and the one-line reason shown in the follow-up list.
UNSUPPORTED_HINTS = {
    "doc": "legacy Word format: convert to docx/odt to search",
    "xls": "legacy Excel format: convert to xlsx/ods to search",
    "rtf": "rich text: convert to docx/odt to search",
    **{ext: "image: OCR needed" for ext in ("jpg", "jpeg", "png", "tif", "tiff", "gif", "bmp", "heic")},
}
#: ODF formats read by readers.OdtReader (a spreadsheet's content.xml has the same paragraph elements).
ODF_EXTENSIONS = frozenset({"ods", "odp", "ott", "ots"})


def _readers():
    try:
        import readers
        return readers
    except ImportError as exc:
        raise ImportError("The text stage needs the 'readers' package: pip install -e path/to/readers[pdf,docx,excel]") from exc


def _convert(result, max_chars: int) -> TextResult:
    """Map readers.ReadResult to TextResult (dispatch dict, so a new status is one line)."""
    status = _readers().ReadStatus
    if result.ok:
        return TextResult(TextStatus.OK, result.text[:max_chars])
    outcome = {status.EMPTY_FILE: TextStatus.NO_TEXT, status.UNSUPPORTED_FORMAT: TextStatus.UNSUPPORTED}
    return TextResult(outcome.get(result.status, TextStatus.ERROR), "", result.error or result.status.value)


def read_text(entry: FileEntry, max_chars: int = 200_000) -> TextResult:
    """Text of ``entry``; never raises for a bad file, always says why it has no text."""
    if entry.ext in UNSUPPORTED_HINTS:
        return TextResult(TextStatus.UNSUPPORTED, "", UNSUPPORTED_HINTS[entry.ext])
    readers = _readers()
    reader = readers.OdtReader if entry.ext in ODF_EXTENSIONS else None
    result = reader.read(entry.path) if reader else readers.read_file(entry.path)
    return _convert(result, max_chars)
