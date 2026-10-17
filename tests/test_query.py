from datetime import datetime

import pytest

from treequery.query import FileView, ParseError, Query, Verdict, tokenize
from treequery.terms import build_groups
from treequery.text import TextResult, TextStatus
from treequery.walk import FileEntry

GROUPS = build_groups({
    "contractor": {"weight": 5, "any": ["Morgan", "New Season"]},
    "invoice": {"any": ["invoice", "receipt"]},
})


def entry(name="Morgan_contract.pdf", size=2048, modified=datetime(2014, 6, 1), path=None, cloud=False):
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return FileEntry(path or f"C:/docs/{name}", "C:/docs", name, ext, size, modified, cloud)


class CountingReader:
    def __init__(self, text="", status=TextStatus.OK):
        self.calls, self.result = 0, TextResult(status, text)

    def __call__(self, _entry):
        self.calls += 1
        return self.result


def verdict(query, e=None, text="", status=TextStatus.OK, max_tier=2):
    reader = CountingReader(text, status)
    view = FileView(e or entry(), reader)
    return Query.parse(query, GROUPS).evaluate(view, max_tier), reader.calls


# ---- grammar --------------------------------------------------------------
def test_tokenizer_keeps_quotes_and_regex_whole():
    kinds = [k for k, _ in tokenize('name:"new season" AND (text:/a b/ OR ext:pdf)')]
    assert kinds == ["ATOM", "AND", "(", "ATOM", "OR", "ATOM", ")"]


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_query_matches_everything(bad):
    assert verdict(bad)[0] is Verdict.YES


@pytest.mark.parametrize("bad", ["ext:", "foo", "size~3", "(ext:pdf", "ext:pdf)", "AND", "name:/x", "bogus:1",
                                 "ext>pdf", "NOT"])
def test_malformed_queries_raise_parse_error(bad):
    with pytest.raises(ParseError):
        Query.parse(bad, GROUPS)


def test_precedence_and_binds_tighter_than_or():
    # ext:txt OR (ext:pdf AND name:zzz): the pdf is not named zzz, so only the OR's other side could save it
    assert verdict("ext:txt OR ext:pdf AND name:zzz")[0] is Verdict.NO
    assert verdict("(ext:txt OR ext:pdf) AND name:Morgan")[0] is Verdict.YES


def test_implicit_and_and_not():
    assert verdict("ext:pdf name:morgan")[0] is Verdict.YES
    assert verdict("ext:pdf NOT name:morgan")[0] is Verdict.NO
    assert verdict("not ext:txt")[0] is Verdict.YES


def test_string_forms():
    assert verdict('name:"morgan_contract"')[0] is Verdict.YES
    assert verdict("name:/^morgan.*pdf$/")[0] is Verdict.YES
    assert verdict("name:zzz,contract")[0] is Verdict.YES            # comma = any of
    assert verdict("ext:.PDF,docx")[0] is Verdict.YES                # dot and case ignored
    assert verdict("path:docs")[0] is Verdict.YES


def test_size_comparisons_and_ranges():
    assert verdict("size>1KB")[0] is Verdict.YES
    assert verdict("size<1KB")[0] is Verdict.NO
    assert verdict("size:1KB..4KB")[0] is Verdict.YES
    assert verdict("size:3KB..")[0] is Verdict.NO
    assert verdict("size>=2KB")[0] is Verdict.YES and verdict("size>2KB")[0] is Verdict.NO


def test_modified_comparisons_ranges_and_whole_day():
    assert verdict("modified>2013-05-01")[0] is Verdict.YES
    assert verdict("modified<2013-05-01")[0] is Verdict.NO
    assert verdict("modified:2014-01-01..2014-12-31")[0] is Verdict.YES
    assert verdict("modified:2014-06-01")[0] is Verdict.YES            # a bare date means that whole day
    assert verdict("modified:2014-06-02")[0] is Verdict.NO


def test_group_matches_name_or_text():
    assert verdict("contractor")[0] is Verdict.YES                      # 'Morgan' is in the name
    assert verdict("invoice", text="see attached invoice")[0] is Verdict.YES
    assert verdict("invoice", text="nothing here")[0] is Verdict.NO


# ---- cost ordering and three-valued logic ---------------------------------
def test_cheap_false_means_text_is_never_read():
    result, reads = verdict("ext:txt AND text:invoice")
    assert (result, reads) == (Verdict.NO, 0)


def test_cheap_true_in_or_means_text_is_never_read():
    assert verdict("ext:pdf OR text:invoice") == (Verdict.YES, 0)


def test_text_is_read_once_even_if_used_twice():
    result, reads = verdict("text:alpha AND text:beta", text="alpha beta")
    assert (result, reads) == (Verdict.YES, 1)


def test_dry_run_tier_limit_gives_maybe_not_a_read():
    assert verdict("ext:pdf AND text:invoice", max_tier=1) == (Verdict.MAYBE, 0)
    assert verdict("ext:txt AND text:invoice", max_tier=1) == (Verdict.NO, 0)


def test_group_name_hit_decides_without_reading_even_in_dry_run():
    assert verdict("contractor", max_tier=1) == (Verdict.YES, 0)
    assert verdict("invoice", max_tier=1) == (Verdict.MAYBE, 0)


@pytest.mark.parametrize("status", [TextStatus.NO_TEXT, TextStatus.UNSUPPORTED, TextStatus.ERROR, TextStatus.CLOUD_ONLY])
def test_unreadable_text_is_maybe_never_a_silent_no_or_not_yes(status):
    assert verdict("text:invoice", status=status)[0] is Verdict.MAYBE
    assert verdict("NOT text:invoice", status=status)[0] is Verdict.MAYBE     # NOT of unknown stays unknown


def test_cloud_only_file_is_not_read_unless_allowed():
    reader = CountingReader("invoice")
    view = FileView(entry(cloud=True), reader)
    assert Query.parse("text:invoice", GROUPS).evaluate(view) is Verdict.MAYBE and reader.calls == 0
    allowed = FileView(entry(cloud=True), reader, read_cloud_only=True)
    assert Query.parse("text:invoice", GROUPS).evaluate(allowed) is Verdict.YES and reader.calls == 1


def test_leaves_lists_each_predicate():
    labels = [leaf.label for leaf in Query.parse("ext:pdf AND (invoice OR text:x)", GROUPS).leaves()]
    assert labels == ["ext:pdf", "invoice", "text:x"]


def test_at_group_matches_one_field_only():
    named = entry("Morgan_notes.txt")
    assert verdict("name:@contractor", e=named) == (Verdict.YES, 0)
    assert verdict("name:@contractor", e=entry("plain.txt")) == (Verdict.NO, 0)        # name-only: never reads
    assert verdict("text:@invoice", e=entry("plain.txt"), text="an invoice")[0] is Verdict.YES
    assert verdict("path:@contractor", e=entry("plain.txt", path="C:/Morgan/plain.txt"))[0] is Verdict.YES


def test_unknown_at_group_is_a_parse_error():
    with pytest.raises(ParseError, match="nosuch"):
        Query.parse("name:@nosuch", GROUPS)
