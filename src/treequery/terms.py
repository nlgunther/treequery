"""
terms.py — named groups of search words, and the score they give a file.

A group is data (YAML), not code, so a new purpose needs a new profile, not a new function.

Example (YAML):
    terms:
      contractor: {weight: 5, any: [Morgan, "New Season", Loomis]}
      money:      {weight: 2, regex: '\\$\\s?\\d{1,3}(,\\d{3})*(\\.\\d\\d)?', capture: true}
"""
import re
from dataclasses import dataclass, field

MAX_COUNTED_HITS = 5     # one file repeating a word 200 times should not outrank a file hitting 5 groups
NAME_HIT_BONUS = 2       # a hit in the file name is worth this many text hits
_GROUP_KEYS = {"weight", "any", "regex", "capture"}


@dataclass(frozen=True)
class TermGroup:
    name: str
    weight: int
    patterns: tuple          # compiled, case-insensitive
    capture: bool = False    # keep the matched strings (e.g. dollar amounts) for the report

    def count(self, text: str) -> int:
        return sum(len(p.findall(text)) for p in self.patterns)

    def matches(self, text: str) -> bool:
        return any(p.search(text) for p in self.patterns)

    def captured(self, text: str, limit: int = 5) -> list[str]:
        found = [m.group(0) for p in self.patterns for m in p.finditer(text)]
        return found[:limit]


def build_groups(spec: dict) -> dict[str, TermGroup]:
    """
    Validate and compile the ``terms:`` section.  Raises ValueError on any malformed group.

    Example:
        build_groups({"doctype": {"weight": 3, "any": ["invoice", "contract"]}})
    """
    groups = {}
    for name, body in (spec or {}).items():
        if not isinstance(body, dict):
            raise ValueError(f"terms.{name}: expected a mapping with 'any' and/or 'regex'")
        unknown = set(body) - _GROUP_KEYS
        if unknown:
            raise ValueError(f"terms.{name}: unknown keys {sorted(unknown)}")
        if not body.get("any") and not body.get("regex"):
            raise ValueError(f"terms.{name}: needs 'any' (words) or 'regex'")
        # Word edges are letters/digits only: in file names '_' and '-' are separators ("Morgan_contract.pdf").
        try:
            words = [re.compile(r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])", re.IGNORECASE)
                     for w in body.get("any", ())]
            regexes = [re.compile(r, re.IGNORECASE) for r in ([body["regex"]] if body.get("regex") else [])]
        except re.error as exc:
            raise ValueError(f"terms.{name}: bad regex: {exc}") from exc
        groups[name] = TermGroup(name, int(body.get("weight", 1)), tuple(words + regexes), bool(body.get("capture")))
    return groups


@dataclass(frozen=True)
class ScoreCard:
    total: int = 0
    hits: dict = field(default_factory=dict)       # group name -> text hit count
    captured: tuple = ()


def score(groups: dict[str, TermGroup], name: str, text: str) -> ScoreCard:
    """
    Weighted score: for each group, weight x (capped text hits + bonus if the file name hits).

    Example:
        score(groups, "RAL_contract.pdf", "RAL Builders ... $4,500")   # -> ScoreCard(total=..., hits={...})
    """
    total, hits, captured = 0, {}, []
    for group in groups.values():
        in_text, in_name = group.count(text), group.matches(name)
        if in_text or in_name:
            hits[group.name] = in_text
            total += group.weight * (min(in_text, MAX_COUNTED_HITS) + (NAME_HIT_BONUS if in_name else 0))
        if group.capture:
            captured.extend(group.captured(text))
    return ScoreCard(total, hits, tuple(captured))
