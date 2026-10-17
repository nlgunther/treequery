"""
walk.py — stage 1 and 2 of the funnel: prune the tree, then filter files by name and stat.

No file is opened here.  The rule names (``exclude_dirs``, ``shallow_dirs``, ``keep_dirs``,
``skip_extension_pattern``, ``cutoff``) match copyindate2code.Settings so that script can
adopt ``walk_tree`` for its selection step without renaming anything.

Example:
    rules = TreeRules.from_settings(TreeSettings(roots=("D:/docs",), skip_hidden=True))
    for entry in walk_tree(rules.roots, rules, WalkStats()):
        print(entry.path, entry.size)
"""
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Iterator

from .settings import option
from .units import parse_moment, parse_size

# Windows attributes Google Drive / OneDrive set on files that exist only online.
# Reading such a file downloads it, which can be gigabytes, so we never do it by accident.
_CLOUD_ATTRIBUTES = 0x400000 | 0x40000 | 0x1000   # RECALL_ON_DATA_ACCESS | RECALL_ON_OPEN | OFFLINE
DEFAULT_EXCLUDE_DIRS = (".git", "__pycache__", "node_modules", "System Volume Information",
                        "$RECYCLE.BIN", "RECYCLE.BIN")


@dataclass(frozen=True)
class TreeSettings:
    roots: tuple = option((), "folders to search")
    exclude_dirs: tuple = option(DEFAULT_EXCLUDE_DIRS, "folder names never entered (case-insensitive)")
    prune: bool = option(True, "a skipped folder takes its whole subtree with it; "
                               "--no-prune skips only that folder's own files and still descends into its subfolders")
    shallow_dirs: tuple = option((), "case-insensitive regexes on folder name; only their top-level files are searched")
    keep_dirs: tuple = option((), "case-insensitive regexes on the full path; overrides --shallow-dirs")
    max_depth: int | None = option(None, "do not descend below this depth (roots are depth 0)")
    skip_hidden: bool = option(False, "skip folders whose names start with '.'")
    only_ext: tuple = option((), "if given, search only these extensions (no dot), e.g. pdf docx")
    skip_extension_pattern: str = option("", "regex tested against each file's extension; matches are skipped")
    skip_name_pattern: str = option("", "regex tested against each file name; matches are skipped")
    cutoff: str = option("", "ISO date; files modified earlier are skipped ('' disables)")
    modified_before: str = option("", "ISO date; files modified later are skipped ('' disables)")
    min_size: str = option("", "skip smaller files, e.g. 10KB ('' disables)")
    max_size: str = option("", "skip larger files, e.g. 50MB ('' disables)")
    guard_future: bool = option(True, "skip files modified after the run started (clock-skew guard)")
    cloud_only: str = option("list", "files that exist only online: skip them, list them unread, or read them (downloads!)",
                             choices=("skip", "list", "read"))

    def __post_init__(self):
        if self.cloud_only not in ("skip", "list", "read"):
            raise ValueError(f"cloud_only must be skip, list or read (got {self.cloud_only!r})")
        for pattern in (*self.shallow_dirs, *self.keep_dirs, self.skip_extension_pattern, self.skip_name_pattern):
            try:
                re.compile(pattern)   # fail now, not mid-walk
            except re.error as exc:
                raise ValueError(f"Bad regex {pattern!r}: {exc}") from exc
        for text, parse in ((self.cutoff, parse_moment), (self.modified_before, parse_moment),
                            (self.min_size, parse_size), (self.max_size, parse_size)):
            if text:
                parse(text)


class FileVerdict(Enum):
    KEEP = "keep"
    ONLY_EXT = "extension not in only_ext"
    SKIP_EXT = "skip_extension_pattern"
    SKIP_NAME = "skip_name_pattern"
    TOO_OLD = "older than cutoff"
    TOO_NEW = "newer than modified_before"
    FUTURE = "modified after run started"
    TOO_SMALL = "below min_size"
    TOO_BIG = "above max_size"
    CLOUD_SKIPPED = "online-only (cloud_only=skip)"


@dataclass(frozen=True)
class FileEntry:
    path: str
    top: str               # the root this file was found under
    name: str
    ext: str               # lower-case, no dot
    size: int
    modified: datetime
    cloud_only: bool

    @property
    def relative_dir(self) -> str:
        """Folder of this file relative to its root ('.' for the root itself)."""
        return os.path.relpath(os.path.dirname(self.path), self.top)


@dataclass
class WalkStats:
    dirs_seen: int = 0
    dirs_pruned: int = 0
    files_seen: int = 0
    verdicts: Counter = field(default_factory=Counter)
    errors: list = field(default_factory=list)

    @property
    def files_kept(self) -> int:
        return self.verdicts[FileVerdict.KEEP]


def _compile_all(patterns) -> tuple:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


@dataclass(frozen=True)
class TreeRules:
    roots: tuple
    exclude: frozenset
    prune: bool
    shallow: tuple
    keep: tuple
    max_depth: int | None
    skip_hidden: bool
    only_ext: frozenset
    skip_extension: re.Pattern | None
    skip_name: re.Pattern | None
    cutoff: datetime | None
    modified_before: datetime | None
    min_size: int | None
    max_size: int | None
    run_start: datetime | None     # None disables the future-mtime guard
    cloud_only: str

    @classmethod
    def from_settings(cls, cfg: TreeSettings, run_start: datetime | None = None) -> "TreeRules":
        run_start = run_start or datetime.now()
        compile_or_none = lambda p: re.compile(p) if p else None
        maybe = lambda text, parse: parse(text) if text else None
        return cls(tuple(cfg.roots), frozenset(n.casefold() for n in cfg.exclude_dirs), cfg.prune,
                   _compile_all(cfg.shallow_dirs), _compile_all(cfg.keep_dirs), cfg.max_depth, cfg.skip_hidden,
                   frozenset(e.lower().lstrip(".") for e in cfg.only_ext),
                   compile_or_none(cfg.skip_extension_pattern), compile_or_none(cfg.skip_name_pattern),
                   maybe(cfg.cutoff, parse_moment), maybe(cfg.modified_before, parse_moment),
                   maybe(cfg.min_size, parse_size), maybe(cfg.max_size, parse_size),
                   run_start if cfg.guard_future else None, cfg.cloud_only)

    def is_excluded(self, folder_name: str) -> bool:
        return folder_name.casefold() in self.exclude

    def is_shallow(self, dirpath: str) -> bool:
        return (any(p.search(os.path.basename(dirpath)) for p in self.shallow)
                and not any(p.search(dirpath) for p in self.keep))

    def file_verdict(self, entry: FileEntry) -> FileVerdict:
        """First rule that rejects the file, or KEEP.  Order is cheapest-first."""
        if self.only_ext and entry.ext not in self.only_ext:
            return FileVerdict.ONLY_EXT
        if self.skip_extension and self.skip_extension.search(os.path.splitext(entry.name)[-1]):
            return FileVerdict.SKIP_EXT
        if self.skip_name and self.skip_name.search(entry.name):
            return FileVerdict.SKIP_NAME
        if self.cloud_only == "skip" and entry.cloud_only:
            return FileVerdict.CLOUD_SKIPPED
        if self.run_start and entry.modified > self.run_start:
            return FileVerdict.FUTURE
        if self.cutoff and entry.modified < self.cutoff:
            return FileVerdict.TOO_OLD
        if self.modified_before and entry.modified > self.modified_before:
            return FileVerdict.TOO_NEW
        if self.min_size is not None and entry.size < self.min_size:
            return FileVerdict.TOO_SMALL
        if self.max_size is not None and entry.size > self.max_size:
            return FileVerdict.TOO_BIG
        return FileVerdict.KEEP


def is_cloud_only(file_attributes: int) -> bool:
    """True when Windows says the file's data is not on this disk."""
    return bool(file_attributes & _CLOUD_ATTRIBUTES)


def make_entry(path: str, top: str) -> FileEntry:
    st = os.stat(path)
    name = os.path.basename(path)
    return FileEntry(path, top, name, os.path.splitext(name)[-1].lower().lstrip("."), st.st_size,
                     datetime.fromtimestamp(st.st_mtime), is_cloud_only(getattr(st, "st_file_attributes", 0)))


def _select_subdirs(rules: TreeRules, dirpath: str, dirs: list, muted: set, stats: WalkStats) -> None:
    """Edit ``dirs`` in place (the os.walk contract): drop what must not be entered."""
    kept = []
    for name in sorted(dirs):
        if rules.skip_hidden and name.startswith("."):
            stats.dirs_pruned += 1
        elif rules.is_excluded(name):
            if rules.prune:
                stats.dirs_pruned += 1
            else:
                muted.add(os.path.join(dirpath, name))   # descend, but ignore this folder's own files
                kept.append(name)
        else:
            kept.append(name)
    dirs[:] = kept


def walk_tree(roots, rules: TreeRules, stats: WalkStats) -> Iterator[FileEntry]:
    """
    Yield every file under ``roots`` that passes the folder and file rules.

    Raises NotADirectoryError for a missing root rather than quietly searching nothing.
    """
    for top in roots:
        top = os.path.normpath(top)
        if not os.path.isdir(top):
            raise NotADirectoryError(f"Not a directory: {top}")
        muted: set = set()
        for dirpath, dirs, names in os.walk(top, onerror=lambda exc: stats.errors.append(str(exc))):
            stats.dirs_seen += 1
            _select_subdirs(rules, dirpath, dirs, muted, stats)
            depth = 0 if dirpath == top else os.path.relpath(dirpath, top).count(os.sep) + 1
            if rules.is_shallow(dirpath) or (rules.max_depth is not None and depth >= rules.max_depth):
                stats.dirs_pruned += len(dirs)
                dirs[:] = []
            if dirpath in muted:
                continue
            for name in sorted(names):
                stats.files_seen += 1
                try:
                    entry = make_entry(os.path.join(dirpath, name), top)
                except OSError as exc:
                    stats.errors.append(f"{os.path.join(dirpath, name)}: {exc}")
                    continue
                verdict = rules.file_verdict(entry)
                stats.verdicts[verdict] += 1
                if verdict is FileVerdict.KEEP:
                    yield entry


def explain_scope(rules: TreeRules, top: str, path: str) -> list[str]:
    """
    Why the walker would or would not reach ``path`` — the folder rules only.

    Example:
        explain_scope(rules, "D:/docs", "D:/docs/.git/config")
        # -> ["pruned: hidden folder '.git'"]   (when skip_hidden is on)
    """
    rel_dir = os.path.relpath(os.path.dirname(path), top)
    parts = [] if rel_dir == "." else rel_dir.split(os.sep)
    parent = top
    for depth, name in enumerate(parts, start=1):
        if rules.is_shallow(parent):
            return [f"pruned: '{os.path.basename(parent) or parent}' is a shallow folder; its subfolders are not entered"]
        if rules.max_depth is not None and depth > rules.max_depth:
            return [f"pruned: depth {depth} is below max_depth {rules.max_depth}"]
        if rules.skip_hidden and name.startswith("."):
            return [f"pruned: hidden folder '{name}'"]
        if rules.is_excluded(name) and rules.prune:
            return [f"pruned: '{name}' is in exclude_dirs and prune is on, so its whole subtree is skipped"]
        if rules.is_excluded(name) and depth == len(parts):
            return [f"skipped: '{name}' is in exclude_dirs; prune is off, so only its own files are skipped"]
        parent = os.path.join(parent, name)
    return ["reached: no folder rule excludes this file's location"]
