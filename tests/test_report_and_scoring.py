"""CSV output format and scoring arithmetic."""
import csv
from datetime import datetime

import pytest

from treequery.report import CANDIDATE_COLUMNS, UNREAD_COLUMNS, format_funnel, unread_path, write_csvs
from treequery.search import Candidate, SearchResult, Unread, duplicate_counts
from treequery.terms import MAX_COUNTED_HITS, NAME_HIT_BONUS, ScoreCard, build_groups, score
from treequery.text import TextStatus
from treequery.walk import FileEntry, FileVerdict, WalkStats


def make_entry(name="a.txt", path=None):
    return FileEntry(path or f"/r/{name}", "/r", name, name.rsplit(".", 1)[-1], 10, datetime(2014, 5, 1, 12, 30), False)


# ---- scoring -------------------------------------------------------------------------------------------------------
G = build_groups({"who": {"weight": 5, "any": ["Morgan"]}, "money": {"weight": 2, "regex": r"\$\d+", "capture": True}})


@pytest.mark.parametrize("name, text, total", [
    ("x.txt", "", 0),
    ("x.txt", "Morgan", 5),
    ("x.txt", "Morgan Morgan Morgan", 15),
    ("x.txt", "Morgan " * 50, 5 * MAX_COUNTED_HITS),                       # repeats are capped
    ("Morgan.txt", "", 5 * NAME_HIT_BONUS),                                 # a name hit counts as two text hits
    ("Morgan.txt", "Morgan", 5 * (1 + NAME_HIT_BONUS)),
    ("x.txt", "Morgan paid $100 and $200", 5 + 2 * 2),                      # groups add up
    ("x.txt", "MORGAN morgan", 10),                                          # case-insensitive
    ("x.txt", "Morgans", 0),                                                 # whole words only
    ("x.txt", "Morgan_contract", 5),                                         # '_' separates words
])
def test_score_arithmetic(name, text, total):
    assert score(G, name, text).total == total


def test_capture_keeps_at_most_five_matches_per_group_in_order():
    card = score(G, "x.txt", " ".join(f"${n}" for n in range(1, 9)))
    assert card.captured == ("$1", "$2", "$3", "$4", "$5")


def test_no_groups_scores_zero():
    assert score({}, "x", "y") == ScoreCard()


@pytest.mark.parametrize("spec, fragment", [
    ({"a": ["x"]}, "expected a mapping"), ({"a": {}}, "needs 'any'"), ({"a": {"any": []}}, "needs 'any'"),
    ({"a": {"any": ["x"], "wieght": 1}}, "unknown keys"), ({"a": {"regex": "["}}, "bad regex"),
])
def test_malformed_term_groups(spec, fragment):
    with pytest.raises(ValueError, match=fragment):
        build_groups(spec)


def test_term_words_are_escaped_not_treated_as_regex():
    groups = build_groups({"g": {"any": ["a.b", "C++"]}})
    assert groups["g"].matches("see a.b here") and not groups["g"].matches("see axb here")
    assert groups["g"].matches("learn C++ today")


# ---- CSV ---------------------------------------------------------------------------------------------------------------
def result_with(*candidates, unread=()):
    return SearchResult(dry_run=False, stats=WalkStats(), candidates=list(candidates), unread=list(unread))


def test_csv_has_bom_headers_and_survives_awkward_characters(tmp_path):
    awkward = 'Raynal, "RAL"\nBuilders.txt'
    candidate = Candidate(make_entry(awkward, path=f"/r/{awkward}"), ScoreCard(7, {"who": 2}, ("$1,500",)), TextStatus.OK, "ab" * 32)
    out = tmp_path / "c.csv"
    write_csvs(result_with(candidate), str(out))
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")                                   # BOM so Excel reads UTF-8
    rows = list(csv.DictReader(open(out, encoding="utf-8-sig", newline="")))
    assert list(rows[0]) == CANDIDATE_COLUMNS
    assert rows[0]["name"] == awkward and rows[0]["captured"] == "$1,500" and rows[0]["hits"] == "who=2"
    assert rows[0]["modified"] == "2014-05-01T12:30:00" and rows[0]["score"] == "7"


def test_unread_csv_is_written_even_when_empty(tmp_path):
    out = tmp_path / "c.csv"
    _, other = write_csvs(result_with(), str(out))
    assert other == str(tmp_path / "c_unread.csv")
    assert open(other, encoding="utf-8-sig").read().strip().split(",") == UNREAD_COLUMNS


def test_unread_rows_carry_status_and_reason(tmp_path):
    unread = Unread(make_entry("scan.pdf"), TextStatus.NO_TEXT, "needs OCR")
    _, other = write_csvs(result_with(unread=[unread]), str(tmp_path / "c.csv"))
    row = next(csv.DictReader(open(other, encoding="utf-8-sig")))
    assert row["text_status"] == "no_text" and row["detail"] == "needs OCR"


@pytest.mark.parametrize("given, expected", [("c.csv", "c_unread.csv"), ("out/x.y.csv", "out/x.y_unread.csv"), ("noext", "noext_unread.csv")])
def test_unread_path(given, expected):
    assert unread_path(given) == expected


def test_duplicate_counts_ignore_empty_hashes():
    cs = [Candidate(make_entry(f"{i}.txt"), ScoreCard(), TextStatus.OK, h) for i, h in enumerate(["a", "a", "b", ""])]
    assert duplicate_counts(cs) == {"a": 2, "b": 1}


def test_funnel_text_mentions_every_drop_reason():
    stats = WalkStats(dirs_seen=10, dirs_pruned=3, files_seen=100)
    stats.verdicts.update({FileVerdict.KEEP: 40, FileVerdict.TOO_OLD: 50, FileVerdict.SKIP_EXT: 10})
    stats.errors.append("boom")
    text = format_funnel(SearchResult(dry_run=True, stats=stats, rejected_by_query=30, matched_without_reading=4, would_read=6))
    for fragment in ("10 entered, 3 pruned", "100 seen, 40 passed", "older than cutoff: 50", "skip_extension_pattern: 10",
                     "30 rejected", "6 would need their text read", "nothing was read", "Errors:  1"):
        assert fragment in text


# ---- degraded environments -----------------------------------------------------------------------------------
def test_unhashable_file_gets_an_empty_hash_not_a_crash(tmp_path):
    from treequery.search import _sha256
    assert _sha256(str(tmp_path / "missing.bin")) == ""


def test_missing_readers_package_gives_an_actionable_message(monkeypatch, tmp_path):
    import builtins
    from treequery.text import read_text
    from treequery.walk import make_entry
    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "readers":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", refuse)
    (tmp_path / "a.txt").write_text("x")
    with pytest.raises(ImportError, match="pip install -e path/to/readers"):
        read_text(make_entry(str(tmp_path / "a.txt"), str(tmp_path)))
