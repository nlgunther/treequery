"""
plan.py — everything a search needs, compiled once: the rules, the query, the term groups and the reader.

``run_search``, ``explain`` and ``estimate`` all start from a ``SearchPlan``, so the three can never disagree about
which files are in scope or which of them would have to be read.

Example:
    plan = SearchPlan.from_settings(cfg)
    for entry in plan.entries(WalkStats()):
        stage = plan.triage(plan.view(entry))      # Stage.REJECTED / MATCHED / UNDECIDED, with no file read
"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from functools import partial
from typing import Callable, Iterator

from .query import FileView, Query, TIER_STAT, Verdict
from .terms import build_groups
from .text import TextResult, read_text
from .walk import FileEntry, TreeRules, TreeSettings, WalkStats, walk_tree

Reader = Callable[[FileEntry], TextResult]


class Stage(Enum):
    """What the cheap tiers (name, path, ext, size, date) could say about a file."""
    REJECTED = "rejected"      # the query is already false
    MATCHED = "matched"        # the query is already true
    UNDECIDED = "undecided"    # only the file's text can settle it


@dataclass(frozen=True)
class SearchPlan:
    cfg: TreeSettings
    rules: TreeRules
    query: Query
    groups: dict
    reader: Reader

    @classmethod
    def from_settings(cls, cfg, reader: Reader | None = None, run_start: datetime | None = None) -> "SearchPlan":
        """``cfg`` is a ProfileSettings (query + terms) or a plain TreeSettings (everything matches)."""
        groups = build_groups(getattr(cfg, "terms", {}))
        query = Query.parse(getattr(cfg, "query", ""), groups)
        reader = reader or partial(read_text, max_chars=getattr(cfg, "max_text_chars", 200_000))
        return cls(cfg, TreeRules.from_settings(cfg, run_start=run_start or datetime.now()), query, groups, reader)

    @property
    def scores(self) -> bool:
        """True when every candidate's text must be read for scoring, even if its name already matched."""
        return bool(getattr(self.cfg, "score", False) and self.groups)

    def entries(self, stats: WalkStats) -> Iterator[FileEntry]:
        """Files that pass the folder and file rules (stages 1 and 2)."""
        return walk_tree(self.cfg.roots, self.rules, stats)

    def view(self, entry: FileEntry) -> FileView:
        return FileView(entry, self.reader, read_cloud_only=self.cfg.cloud_only == "read")

    def triage(self, view: FileView) -> Stage:
        """Stage 3: run only the tiers that cost nothing or one stat.  Never reads a file."""
        verdict = self.query.evaluate(view, max_tier=TIER_STAT)
        return {Verdict.NO: Stage.REJECTED, Verdict.YES: Stage.MATCHED, Verdict.MAYBE: Stage.UNDECIDED}[verdict]

    def resolve(self, view: FileView) -> Verdict:
        """Stage 4: the final verdict, reading the file's text if the cheap tiers could not decide."""
        return self.query.evaluate(view)
