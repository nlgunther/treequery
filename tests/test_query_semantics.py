"""Query evaluation semantics: three-valued logic, cost ordering, and parser properties."""
import itertools
import re
from datetime import datetime

import pytest
from hypothesis import given, settings, strategies as st

from test_query import GROUPS, CountingReader, entry
from treequery.query import FileView, ParseError, Query, Verdict, tokenize
from treequery.text import TextStatus

Y, N, M = Verdict.YES, Verdict.NO, Verdict.MAYBE


def run(source, text="", status=TextStatus.OK, e=None, max_tier=2):
    reader = CountingReader(text, status)
    verdict = Query.parse(source, GROUPS).evaluate(FileView(e or entry(), reader), max_tier)
    return verdict, reader.calls


# ---- Kleene three-valued truth tables, driven through real queries --------------------------------
# name:morgan -> YES for Morgan_contract.pdf ; name:zzz -> NO ; text:x with unreadable text -> MAYBE
ATOM = {Y: "name:morgan", N: "name:zzz", M: "text:x"}
AND_TABLE = {(Y, Y): Y, (Y, N): N, (Y, M): M, (N, Y): N, (N, N): N, (N, M): N, (M, Y): M, (M, N): N, (M, M): M}
OR_TABLE = {(Y, Y): Y, (Y, N): Y, (Y, M): Y, (N, Y): Y, (N, N): N, (N, M): M, (M, Y): Y, (M, N): M, (M, M): M}


@pytest.mark.parametrize("a, b", list(itertools.product((Y, N, M), repeat=2)))
def test_and_or_truth_tables(a, b):
    unreadable = dict(text="", status=TextStatus.ERROR)
    assert run(f"{ATOM[a]} AND {ATOM[b]}", **unreadable)[0] is AND_TABLE[(a, b)]
    assert run(f"{ATOM[a]} OR {ATOM[b]}", **unreadable)[0] is OR_TABLE[(a, b)]


@pytest.mark.parametrize("a", (Y, N, M))
def test_not_table(a):
    assert run(f"NOT {ATOM[a]}", status=TextStatus.ERROR)[0] is a.negate()


@pytest.mark.parametrize("status", [TextStatus.NO_TEXT, TextStatus.UNSUPPORTED, TextStatus.CLOUD_ONLY, TextStatus.ERROR])
def test_every_unreadable_status_gives_maybe_never_no(status):
    assert run("text:invoice", status=status)[0] is M
    assert run("invoice", e=entry("plain.pdf"), status=status)[0] is M     # a bare group falls back to text


# ---- cost ordering: the point of the whole design --------------------------------------------------
@pytest.mark.parametrize("source, reads", [
    ("ext:pdf AND text:invoice", 1),
    ("ext:docx AND text:invoice", 0),                  # cheap NO short-circuits
    ("text:invoice AND ext:docx", 0),                  # order written does not matter
    ("ext:pdf OR text:invoice", 0),                    # cheap YES short-circuits
    ("size>1GB AND text:invoice", 0),
    ("name:morgan AND size>1KB AND modified>2014-01-01 AND text:invoice", 1),
    ("NOT ext:pdf AND text:invoice", 0),
    ("(ext:docx OR ext:doc) AND text:invoice", 0),
])
def test_text_is_read_only_when_cheaper_predicates_cannot_decide(source, reads):
    assert run(source, text="invoice")[1] == reads


def test_text_is_read_at_most_once_per_file():
    assert run("text:a OR text:b OR text:c OR invoice", text="nothing")[1] == 1


def test_max_tier_zero_never_reads_or_stats():
    assert run("size>1 AND ext:pdf", max_tier=0) == (M, 0)
    assert run("ext:pdf", max_tier=0) == (Y, 0)


# ---- field semantics ----------------------------------------------------------------------------------
@pytest.mark.parametrize("source, expected", [
    ("ext:PDF", Y), ("ext:.pdf,docx", Y), ("ext:docx", N), ("ext:", None),
    ("name:MORGAN", Y), ("name:\"morgan_contract\"", Y), ("name:/^morgan.*pdf$/", Y), ("name:/^contract/", N),
    ("path:docs", Y), ("path:/^C:/docs/", Y),
    ("size:2KB", Y), ("size:1KB..2KB", Y), ("size:..1KB", N), ("size:3KB..", N), ("size>=2KB", Y), ("size>2KB", N),
    ("modified:2014-06-01", Y), ("modified:2014-06-01..2014-06-01", Y), ("modified>2014-06-01", N),
    ("modified>=2014-06-01", Y), ("modified<2014-06-02", Y), ("modified<2014-06-01", N),
    ("modified:2014", None),
])
def test_field_predicates(source, expected):
    if expected is None:
        with pytest.raises(ParseError):
            Query.parse(source)
    else:
        assert run(source)[0] is expected


def test_date_with_time_is_exact_not_whole_day():
    e = entry(modified=datetime(2014, 6, 1, 15, 30))
    assert run("modified:2014-06-01", e=e)[0] is Y                      # bare date = the whole day
    assert run("modified:2014-06-01T15:30:00", e=e)[0] is Y
    assert run("modified:2014-06-01T15:31:00", e=e)[0] is N


def test_escaped_closer_inside_quotes_and_regex():
    assert run(r'name:"a\"b"', e=entry('a"b.txt'))[0] in (Y, N)       # parses; the escape is consumed, not an error
    assert run(r"name:/a\/b/", e=entry("a_b.txt"))[0] in (Y, N)


def test_unknown_group_field_and_operator_messages():
    for source, fragment in [("nonsuch", "Unknown term group"), ("colour:red", "Unknown field"),
                             ("name>3", "only supports ':'"), ("name:", "needs a value"),
                             ("name:@nosuch", "Unknown term group @nosuch"), ("(ext:pdf", "Missing ')'"),
                             ("ext:pdf)", "Unexpected"), ("AND ext:pdf", "Expected a condition"),
                             ("ext:pdf AND", "Expected a condition"), ("NOT", "Expected a condition"),
                             ('name:"abc', "Unterminated")]:
        with pytest.raises(ParseError, match=re.escape(fragment)):
            Query.parse(source, GROUPS)


@pytest.mark.parametrize("source", ["", "   ", "\t\n"])
def test_blank_query_matches_everything_without_reading(source):
    assert run(source) == (Y, 0)


def test_keywords_are_case_insensitive_and_implicit_and_works():
    assert run("EXT:pdf and NOT name:zzz")[0] is Y
    assert run("ext:pdf name:morgan")[0] is Y
    assert run("ext:pdf name:zzz")[0] is N
    assert run("ext:docx or ext:pdf")[0] is Y


def test_precedence_and_binds_tighter_than_or():
    # a OR b AND c  ==  a OR (b AND c)
    e = entry()                                   # pdf, morgan
    assert run("ext:pdf OR name:zzz AND name:zzz", e=e)[0] is Y
    assert run("(ext:pdf OR name:zzz) AND name:zzz", e=e)[0] is N
    assert run("NOT ext:pdf OR name:morgan", e=e)[0] is Y


# ---- properties -----------------------------------------------------------------------------------------
ATOMS = ["ext:pdf", "ext:docx", "name:morgan", "name:zzz", "size>1KB", "size>1GB", "modified>2014-01-01", "contractor"]


@st.composite
def expressions(draw, depth=3):
    if depth == 0 or draw(st.booleans()):
        return draw(st.sampled_from(ATOMS))
    kind = draw(st.sampled_from(["AND", "OR", "NOT", "paren"]))
    a = draw(expressions(depth=depth - 1))
    if kind == "NOT":
        return f"NOT ({a})"
    if kind == "paren":
        return f"({a})"
    return f"({a}) {kind} ({draw(expressions(depth=depth - 1))})"


@settings(max_examples=150, deadline=None)
@given(expressions())
def test_generated_queries_parse_and_double_negation_is_identity(expr):
    plain = run(expr, text="invoice Morgan")[0]
    assert run(f"NOT (NOT ({expr}))", text="invoice Morgan")[0] is plain


@settings(max_examples=150, deadline=None)
@given(expressions(), expressions())
def test_de_morgan_holds_in_three_valued_logic(a, b):
    kw = dict(text="", status=TextStatus.ERROR)
    assert run(f"NOT (({a}) AND ({b}))", **kw)[0] is run(f"(NOT ({a})) OR (NOT ({b}))", **kw)[0]
    assert run(f"NOT (({a}) OR ({b}))", **kw)[0] is run(f"(NOT ({a})) AND (NOT ({b}))", **kw)[0]


@settings(max_examples=150, deadline=None)
@given(expressions(), expressions())
def test_and_or_are_commutative(a, b):
    kw = dict(text="", status=TextStatus.ERROR)
    assert run(f"({a}) AND ({b})", **kw)[0] is run(f"({b}) AND ({a})", **kw)[0]
    assert run(f"({a}) OR ({b})", **kw)[0] is run(f"({b}) OR ({a})", **kw)[0]


@settings(max_examples=150, deadline=None)
@given(expressions())
def test_cheap_pass_never_contradicts_full_evaluation(expr):
    """A decisive cheap answer must equal the full answer; only MAYBE may change when more tiers run."""
    cheap = run(expr, text="invoice Morgan", max_tier=1)[0]
    full = run(expr, text="invoice Morgan")[0]
    assert cheap is M or cheap is full


@settings(max_examples=200, deadline=None)
@given(st.text(max_size=60))
def test_arbitrary_text_never_crashes_the_parser(source):
    """Garbage in gives ParseError or a Query; never another exception type."""
    try:
        Query.parse(source, GROUPS)
    except ParseError:
        pass


@settings(max_examples=100, deadline=None)
@given(st.text(alphabet="()ANDORT:abc/\" ", max_size=40))
def test_tokenizer_never_raises_anything_but_parse_error(source):
    try:
        tokenize(source)
    except ParseError:
        pass
