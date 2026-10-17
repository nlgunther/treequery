"""
search.py — runs the funnel: walk -> file rules -> cheap query tiers -> read text last -> score.

``run_search`` is the one entry point the CLI (and any script) calls.

Example:
    cfg = ProfileSettings(roots=("D:/docs",), query="ext:pdf AND invoice",
                          terms={"invoice": {"any": ["invoice", "receipt"]}})
    result = run_search(cfg)
    for c in result.candidates[:5]:
        print(c.card.total, c.entry.path)
"""
import hashlib
import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from loguru import logger
from tqdm import tqdm

from .plan import SearchPlan, Stage
from .query import FileView, Query, Verdict
from .settings import option
from .terms import ScoreCard, build_groups, score
from .text import TextResult, TextStatus
from .walk import FileEntry, TreeSettings, WalkStats, explain_scope, make_entry


@dataclass(frozen=True)
class ProfileSettings(TreeSettings):
    query: str = option("", "boolean query, e.g. 'ext:pdf,docx AND (contractor OR invoice)'; empty matches every file")
    terms: dict = option({}, "named word groups used by the query and the score (YAML only)", cli=False)
    output: str = option("candidates.csv", "CSV of ranked candidates; unreadable files go to <name>_unread.csv")
    max_text_chars: int = option(200_000, "read at most this many characters of each file")
    score: bool = option(True, "rank candidates by weighted term hits (reads text of every candidate)")
    hash_results: bool = option(True, "add a SHA-256 to each candidate so duplicate copies group together")
    log_level: str = option("INFO", "loguru level: DEBUG, INFO, WARNING, ...")

    def __post_init__(self):
        super().__post_init__()
        Query.parse(self.query, build_groups(self.terms))   # bad query or terms fail at load time


@dataclass(frozen=True)
class Candidate:
    entry: FileEntry
    card: ScoreCard
    text_status: TextStatus
    sha256: str = ""


@dataclass(frozen=True)
class Unread:
    """A file the cheap tiers could not rule out but whose text could not be read."""
    entry: FileEntry
    status: TextStatus
    detail: str


@dataclass
class SearchResult:
    dry_run: bool
    stats: WalkStats
    candidates: list = field(default_factory=list)
    unread: list = field(default_factory=list)
    rejected_by_query: int = 0
    matched_without_reading: int = 0     # dry run: decided YES by name/stat alone
    would_read: int = 0                  # dry run: undecided, would need the file's text
    text_reads: int = 0


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
    except OSError as exc:
        logger.warning("Could not hash {}: {}", path, exc)
        return ""
    return digest.hexdigest()


def run_search(cfg: ProfileSettings, *, dry_run: bool = False, show_progress: bool = False,
               reader: Callable[[FileEntry], TextResult] | None = None) -> SearchResult:
    """
    Run the funnel.  ``dry_run`` reads no file contents and reports how many it *would* read.
    ``reader`` is injectable for tests; the default reads real files via the ``readers`` package.
    """
    plan = SearchPlan.from_settings(cfg, reader)
    result = SearchResult(dry_run=dry_run, stats=WalkStats())
    logger.info("Searching {} root(s); query: {!r}", len(cfg.roots), cfg.query or "(everything)")

    entries = tqdm(plan.entries(result.stats), unit="file", desc="searching", disable=not show_progress)
    for entry in entries:
        view = plan.view(entry)
        stage = plan.triage(view)
        if stage is Stage.REJECTED:
            result.rejected_by_query += 1
        elif dry_run:
            if stage is Stage.MATCHED:
                result.matched_without_reading += 1
            else:
                result.would_read += 1
        else:
            _settle(plan, entry, view, stage, result)
        result.text_reads += view.text_loaded and view.text_result.status is not TextStatus.CLOUD_ONLY
    _finish(result, cfg)
    return result


def _settle(plan: SearchPlan, entry: FileEntry, view: FileView, stage: Stage, result: SearchResult) -> None:
    """Real run: record the file as a candidate, an unread file or a rejection."""
    verdict = Verdict.YES if stage is Stage.MATCHED else plan.resolve(view)
    if verdict is Verdict.NO:
        result.rejected_by_query += 1
    elif verdict is Verdict.MAYBE:
        if view.text_loaded:      # the text was needed and could not be read
            result.unread.append(Unread(entry, view.text_result.status, view.text_result.detail))
    else:
        result.candidates.append(_to_candidate(entry, view, plan.groups, plan.cfg))


def _to_candidate(entry: FileEntry, view: FileView, groups: dict, cfg: ProfileSettings) -> Candidate:
    if cfg.score and groups:
        return Candidate(entry, score(groups, entry.name, view.text_result.text), view.text_result.status)
    return Candidate(entry, ScoreCard(), view.text_result.status if view.text_loaded else TextStatus.NOT_READ)


def _finish(result: SearchResult, cfg: ProfileSettings) -> None:
    result.candidates.sort(key=lambda c: (-c.card.total, c.entry.path.lower()))
    if cfg.hash_results and not result.dry_run:
        # An online-only file has no local bytes to hash; hashing would download it.
        result.candidates = [Candidate(c.entry, c.card, c.text_status, "" if c.entry.cloud_only else _sha256(c.entry.path))
                             for c in result.candidates]
    logger.info("Done: {} candidates, {} unreadable, {} text reads", len(result.candidates), len(result.unread), result.text_reads)


def duplicate_counts(candidates: list) -> Counter:
    """How many candidates share each SHA-256 (empty hashes are ignored)."""
    return Counter(c.sha256 for c in candidates if c.sha256)


def explain(cfg: ProfileSettings, path: str, reader: Callable[[FileEntry], TextResult] | None = None) -> list[str]:
    """Human-readable account of why one file is or is not a candidate."""
    plan = SearchPlan.from_settings(cfg, reader)
    path = os.path.normpath(path)
    tops = [t for t in map(os.path.normpath, cfg.roots) if os.path.normcase(path).startswith(os.path.normcase(t) + os.sep)]
    if not tops:
        return [f"{path}: not under any root ({list(cfg.roots)})"]
    lines = [f"File: {path}", "Folder rules: " + explain_scope(plan.rules, tops[0], path)[0]]
    try:
        entry = make_entry(path, tops[0])
    except OSError as exc:
        return lines + [f"File rules: cannot stat the file ({exc.strerror or exc})"]
    lines.append(f"File rules: {plan.rules.file_verdict(entry).value}")
    view = plan.view(entry)
    for leaf in plan.query.leaves():
        lines.append(f"  {leaf.label}: {leaf.evaluate(view, 2).value}")
    lines.append(f"Query {cfg.query!r}: {plan.resolve(view).value}")
    if view.text_loaded:
        lines.append(f"Text: {view.text_result.status.value} {view.text_result.detail}".rstrip())
    return lines
