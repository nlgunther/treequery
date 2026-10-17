"""report.py — turn a SearchResult into CSV files and a plain-text funnel summary."""
import csv
import os

from .search import SearchResult, duplicate_counts
from .walk import FileVerdict

CANDIDATE_COLUMNS = ["score", "path", "name", "ext", "size", "modified", "text_status", "hits", "captured",
                     "sha256", "copies"]
UNREAD_COLUMNS = ["path", "ext", "size", "modified", "text_status", "detail"]


def unread_path(output: str) -> str:
    """candidates.csv -> candidates_unread.csv"""
    stem, ext = os.path.splitext(output)
    return f"{stem}_unread{ext or '.csv'}"


def write_csvs(result: SearchResult, output: str) -> tuple[str, str]:
    """Write the ranked candidates and the unreadable-file list; returns both paths."""
    copies = duplicate_counts(result.candidates)
    rows = [[c.card.total, c.entry.path, c.entry.name, c.entry.ext, c.entry.size, c.entry.modified.isoformat(timespec="seconds"),
             c.text_status.value, "; ".join(f"{k}={v}" for k, v in c.card.hits.items()), " | ".join(c.card.captured),
             c.sha256, copies[c.sha256] if c.sha256 else ""] for c in result.candidates]
    unread_rows = [[u.entry.path, u.entry.ext, u.entry.size, u.entry.modified.isoformat(timespec="seconds"),
                    u.status.value, u.detail] for u in result.unread]
    other = unread_path(output)
    for path, header, body in ((output, CANDIDATE_COLUMNS, rows), (other, UNREAD_COLUMNS, unread_rows)):
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:   # BOM so Excel opens UTF-8 correctly
            writer = csv.writer(fh)
            writer.writerow(header)
            writer.writerows(body)
    return output, other


def format_funnel(result: SearchResult) -> str:
    """The funnel in words: where files were dropped and how many reads the query needed."""
    s = result.stats
    lines = [f"Folders: {s.dirs_seen} entered, {s.dirs_pruned} pruned",
             f"Files:   {s.files_seen} seen, {s.files_kept} passed the folder/file rules"]
    lines += [f"           dropped, {v.value}: {n}" for v, n in sorted(s.verdicts.items(), key=lambda kv: kv[0].value)
              if v is not FileVerdict.KEEP]
    lines.append(f"Query:   {result.rejected_by_query} rejected")
    if result.dry_run:
        lines.append(f"         {result.matched_without_reading} match without reading; "
                     f"{result.would_read} would need their text read (dry run: nothing was read)")
    else:
        lines.append(f"         {len(result.candidates)} candidates; {result.text_reads} files read; "
                     f"{len(result.unread)} undecidable (unreadable text, see the _unread list)")
    if s.errors:
        lines.append(f"Errors:  {len(s.errors)} (first: {s.errors[0]})")
    return "\n".join(lines)
