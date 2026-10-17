"""
query.py — a tiny boolean query language, evaluated cheapest-predicate-first.

Grammar (keywords are case-insensitive; two atoms side by side mean AND):

    query := or            or  := and (OR and)*        and := unary (AND? unary)*
    unary := NOT unary | ( or ) | atom
    atom  := FIELD ':' VALUE | FIELD ('>' | '<' | '>=' | '<=') VALUE | GROUP
    FIELD := name | path | ext | text | size | modified
    VALUE := word[,word...] | "phrase" | /regex/ | @GROUP | low..high   (ranges for size and modified)
    GROUP := a term-group name from the profile's ``terms:``

``name:@contractor`` means "the term group ``contractor``, matched against the file name only" (likewise
``path:@`` and ``text:@``); a bare ``contractor`` matches the name or the text.

Every predicate has a cost tier — 0 name/path/ext (free), 1 size/modified (one stat),
2 text (read the file).  ``evaluate`` runs the cheapest children first and stops at the
first decisive answer, so a file is read only when the cheap predicates cannot decide.
The answer is three-valued (YES / NO / MAYBE): MAYBE means "would need a tier we did not
run" — or, for text, "could not be read" — so unreadable files are never counted as non-matches.

Example:
    q = Query.parse('ext:pdf,docx AND (contractor OR invoice) AND modified>2013-05-01', groups)
    verdict = q.evaluate(view)          # Verdict.YES / NO / MAYBE
"""
import re
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Callable

from .terms import TermGroup
from .text import TextResult, TextStatus
from .units import parse_moment, parse_size
from .walk import FileEntry

TIER_NAME, TIER_STAT, TIER_TEXT = 0, 1, 2
FIELD_TIER = {"name": TIER_NAME, "path": TIER_NAME, "ext": TIER_NAME,
              "size": TIER_STAT, "modified": TIER_STAT, "text": TIER_TEXT}


class ParseError(ValueError):
    """The query text is not valid; the message says where and why."""


class Verdict(Enum):
    YES = "yes"
    NO = "no"
    MAYBE = "maybe"

    def negate(self) -> "Verdict":
        return {Verdict.YES: Verdict.NO, Verdict.NO: Verdict.YES, Verdict.MAYBE: Verdict.MAYBE}[self]


class FileView:
    """A file plus lazily-read text; counts how many reads the query forced."""

    def __init__(self, entry: FileEntry, reader: Callable[[FileEntry], TextResult], read_cloud_only: bool = False):
        self.entry, self._reader, self._read_cloud_only = entry, reader, read_cloud_only
        self._result: TextResult | None = None

    @property
    def text_loaded(self) -> bool:
        return self._result is not None

    @property
    def text_result(self) -> TextResult:
        if self._result is None:
            if self.entry.cloud_only and not self._read_cloud_only:
                self._result = TextResult(TextStatus.CLOUD_ONLY, "", "online-only file; not downloaded")
            else:
                self._result = self._reader(self.entry)
        return self._result


# --------------------------------------------------------------------------- #
# AST
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MatchAll:
    tier = TIER_NAME
    label = "(everything)"

    def evaluate(self, view, max_tier):
        return Verdict.YES

    def leaves(self):
        return []


@dataclass(frozen=True)
class Pred:
    """A field test.  ``test`` gets the FileView and returns bool; tier decides when it may run."""
    label: str
    tier: int
    test: Callable[[FileView], bool]

    def evaluate(self, view, max_tier):
        if self.tier > max_tier:
            return Verdict.MAYBE
        if self.tier == TIER_TEXT and view.text_result.status is not TextStatus.OK:
            return Verdict.MAYBE            # cannot judge unreadable text; never call it a non-match
        return Verdict.YES if self.test(view) else Verdict.NO

    def leaves(self):
        return [self]


@dataclass(frozen=True)
class GroupRef:
    """True if any word of a term group is in the file name (free) or text (tier 2)."""
    group: TermGroup
    tier = TIER_TEXT

    @property
    def label(self):
        return self.group.name

    def evaluate(self, view, max_tier):
        if self.group.matches(view.entry.name):
            return Verdict.YES
        if max_tier < TIER_TEXT or view.text_result.status is not TextStatus.OK:
            return Verdict.MAYBE
        return Verdict.YES if self.group.matches(view.text_result.text) else Verdict.NO

    def leaves(self):
        return [self]


@dataclass(frozen=True)
class Not:
    child: object

    @property
    def tier(self):
        return self.child.tier

    def evaluate(self, view, max_tier):
        return self.child.evaluate(view, max_tier).negate()

    def leaves(self):
        return self.child.leaves()


@dataclass(frozen=True)
class And:
    children: tuple

    @property
    def tier(self):
        return max(c.tier for c in self.children)

    def evaluate(self, view, max_tier):
        outcome = Verdict.YES
        for child in sorted(self.children, key=lambda c: c.tier):     # stable: cheapest first
            verdict = child.evaluate(view, max_tier)
            if verdict is Verdict.NO:
                return Verdict.NO
            if verdict is Verdict.MAYBE:
                outcome = Verdict.MAYBE
        return outcome

    def leaves(self):
        return [leaf for c in self.children for leaf in c.leaves()]


@dataclass(frozen=True)
class Or:
    children: tuple

    @property
    def tier(self):
        return max(c.tier for c in self.children)

    def evaluate(self, view, max_tier):
        outcome = Verdict.NO
        for child in sorted(self.children, key=lambda c: c.tier):
            verdict = child.evaluate(view, max_tier)
            if verdict is Verdict.YES:
                return Verdict.YES
            if verdict is Verdict.MAYBE:
                outcome = Verdict.MAYBE
        return outcome

    def leaves(self):
        return [leaf for c in self.children for leaf in c.leaves()]


# --------------------------------------------------------------------------- #
# Lexer
# --------------------------------------------------------------------------- #
_KEYWORDS = {"and", "or", "not"}
_ATOM = re.compile(r"^([A-Za-z_]\w*)(?:(:|>=|<=|>|<)(.*))?$", re.DOTALL)


def _read_delimited(text: str, start: int, closer: str) -> int:
    """Index just past the closing ``closer`` (a backslash escapes it); raises ParseError if unterminated."""
    i = start + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == closer:
            return i + 1
        i += 1
    raise ParseError(f"Unterminated {closer} starting at position {start} in: {text}")


def tokenize(text: str) -> list[tuple[str, str]]:
    """Split into ('(' | ')' | 'AND' | 'OR' | 'NOT' | 'ATOM', text) tokens."""
    tokens, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch in "()":
            tokens.append((ch, ch))
            i += 1
        else:
            start = i
            while i < len(text) and not text[i].isspace() and text[i] not in "()":
                if text[i] == '"' or (text[i] == "/" and i > start and text[i - 1] in ":<>="):
                    i = _read_delimited(text, i, text[i])
                else:
                    i += 1
            word = text[start:i]
            tokens.append((word.upper(), word) if word.lower() in _KEYWORDS else ("ATOM", word))
    return tokens


# --------------------------------------------------------------------------- #
# Atom -> predicate
# --------------------------------------------------------------------------- #
def _string_matcher(value: str) -> Callable[[str], bool]:
    """/regex/ (case-insensitive search), "phrase", or a,b,c alternatives; all case-insensitive substrings."""
    if len(value) >= 2 and value[0] == "/" and value[-1] == "/":
        pattern = re.compile(value[1:-1], re.IGNORECASE)
        return lambda s: bool(pattern.search(s))
    if len(value) >= 2 and value[0] == value[-1] == '"':
        needles = [value[1:-1].lower()]
    else:
        needles = [v.lower() for v in value.split(",") if v]
    if not needles:
        raise ParseError("Empty value")
    return lambda s: any(n in s.lower() for n in needles)


def _group_matcher(name: str, groups: dict[str, TermGroup]) -> Callable[[str], bool]:
    if name not in groups:
        raise ParseError(f"Unknown term group @{name}; known groups: {sorted(groups) or 'none'}")
    return groups[name].matches


def _bounds(op: str, value: str, parse: Callable, end_of_day: bool):
    """(low, high) from 'a..b', '..b', 'a..' or a comparison; None means open."""
    if op == ":" and ".." in value:
        low, high = value.split("..", 1)
        return (parse(low) if low else None), (_inclusive_end(high, parse, end_of_day) if high else None)
    if op == ":":
        point = parse(value)
        return point, (_inclusive_end(value, parse, end_of_day) if end_of_day else point)
    bound = parse(value)
    return {">": (bound, None), ">=": (bound, None), "<": (None, bound), "<=": (None, bound)}[op]


def _inclusive_end(text: str, parse: Callable, end_of_day: bool):
    moment = parse(text)
    return moment + timedelta(days=1) - timedelta(microseconds=1) if end_of_day and len(text.strip()) == 10 else moment


def _numeric_pred(field: str, op: str, value: str, label: str) -> Pred:
    is_date = field == "modified"
    parse = parse_moment if is_date else parse_size
    low, high = _bounds(op, value, parse, end_of_day=is_date)
    # '>' / '<' are strict and '>=' / '<=' are not; ':' ranges are inclusive both ends.
    strict_low, strict_high = op == ">", op == "<"
    get = (lambda v: v.entry.modified) if is_date else (lambda v: v.entry.size)

    def test(view):
        x = get(view)
        return ((low is None or (x > low if strict_low else x >= low))
                and (high is None or (x < high if strict_high else x <= high)))
    return Pred(label, TIER_STAT, test)


def _atom_to_node(atom: str, groups: dict[str, TermGroup]):
    match = _ATOM.match(atom)
    if not match:
        raise ParseError(f"Cannot read {atom!r}; expected FIELD:VALUE or a term-group name")
    field, op, value = match.groups()
    if op is not None and field.lower() in FIELD_TIER:
        field = field.lower()                  # EXT:pdf works like ext:pdf; term-group names stay case-sensitive
    if op is None:
        if field not in groups:
            raise ParseError(f"Unknown term group {field!r}; known groups: {sorted(groups) or 'none'}")
        return GroupRef(groups[field])
    if field not in FIELD_TIER:
        raise ParseError(f"Unknown field {field!r}; fields are {sorted(FIELD_TIER)}")
    if value == "":
        raise ParseError(f"{field}{op} needs a value")
    if field in ("size", "modified"):
        return _numeric_pred(field, op, value, atom)
    if op != ":":
        raise ParseError(f"{field} only supports ':' (got {op!r})")
    if field == "ext":
        wanted = {v.lower().lstrip(".") for v in value.split(",") if v}
        return Pred(atom, TIER_NAME, lambda view: view.entry.ext in wanted)
    matcher = _group_matcher(value[1:], groups) if value.startswith("@") else _string_matcher(value)
    getter = {"name": lambda v: v.entry.name, "path": lambda v: v.entry.path,
              "text": lambda v: v.text_result.text}[field]
    return Pred(atom, FIELD_TIER[field], lambda view: matcher(getter(view)))


# --------------------------------------------------------------------------- #
# Parser (recursive descent)
# --------------------------------------------------------------------------- #
class _Parser:
    def __init__(self, source: str, groups: dict[str, TermGroup]):
        self.source, self.groups, self.tokens, self.pos = source, groups, tokenize(source), 0

    def peek(self) -> str | None:
        return self.tokens[self.pos][0] if self.pos < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def parse(self):
        node = self.parse_or()
        if self.peek() is not None:
            raise ParseError(f"Unexpected {self.tokens[self.pos][1]!r} in: {self.source}")
        return node

    def parse_or(self):
        children = [self.parse_and()]
        while self.peek() == "OR":
            self.take()
            children.append(self.parse_and())
        return children[0] if len(children) == 1 else Or(tuple(children))

    def parse_and(self):
        children = [self.parse_unary()]
        while True:
            if self.peek() == "AND":
                self.take()
            elif self.peek() not in ("ATOM", "(", "NOT"):
                break
            children.append(self.parse_unary())
        return children[0] if len(children) == 1 else And(tuple(children))

    def parse_unary(self):
        kind = self.peek()
        if kind is None or kind in (")", "AND", "OR"):
            raise ParseError(f"Expected a condition in: {self.source}")
        if kind == "NOT":
            self.take()
            return Not(self.parse_unary())
        if kind == "(":
            self.take()
            node = self.parse_or()
            if self.peek() != ")":
                raise ParseError(f"Missing ')' in: {self.source}")
            self.take()
            return node
        return _atom_to_node(self.take()[1], self.groups)


@dataclass(frozen=True)
class Query:
    source: str
    root: object

    @classmethod
    def parse(cls, source: str, groups: dict[str, TermGroup] | None = None) -> "Query":
        """An empty query matches everything.  Raises ParseError on bad syntax or an unknown name."""
        if not source.strip():
            return cls(source, MatchAll())
        try:
            return cls(source, _Parser(source, groups or {}).parse())
        except ParseError:
            raise
        except (ValueError, re.error) as exc:      # bad /regex/, size or date: say which query
            raise ParseError(f"{exc} (in query: {source})") from exc

    @property
    def max_tier(self) -> int:
        return self.root.tier

    def evaluate(self, view: FileView, max_tier: int = TIER_TEXT) -> Verdict:
        return self.root.evaluate(view, max_tier)

    def leaves(self) -> list:
        return self.root.leaves()
