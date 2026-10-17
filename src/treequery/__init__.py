"""
treequery — cost-ordered search of large file trees.

The funnel: prune folders, filter files by name and stat, evaluate a boolean query
cheapest-predicate-first, and read a file's text only when nothing cheaper can decide.

    from treequery import ProfileSettings, run_search
    result = run_search(ProfileSettings(roots=("D:/docs",), query="ext:pdf AND invoice",
                                        terms={"invoice": {"any": ["invoice"]}}))
"""
from .estimate import Census, CensusSource, Estimate, EstimateSettings, Item, estimate, format_estimate, stat_census
from .plan import SearchPlan, Stage
from .query import ParseError, Query, Verdict
from .search import Candidate, ProfileSettings, SearchResult, Unread, explain, run_search
from .settings import option, resolve_settings
from .terms import ScoreCard, TermGroup, build_groups, score
from .text import TextResult, TextStatus, read_text
from .walk import FileEntry, FileVerdict, TreeRules, TreeSettings, WalkStats, walk_tree

__version__ = "0.2.1"

__all__ = ["Candidate", "Census", "CensusSource", "Estimate", "EstimateSettings", "FileEntry", "FileVerdict", "Item", "ParseError", "ProfileSettings", "Query",
           "ScoreCard", "SearchPlan", "SearchResult", "Stage", "TermGroup", "TextResult", "TextStatus", "TreeRules",
           "TreeSettings", "Unread", "Verdict", "WalkStats", "build_groups", "estimate", "explain", "format_estimate", "option",
           "read_text", "resolve_settings", "run_search", "score", "stat_census", "walk_tree"]
