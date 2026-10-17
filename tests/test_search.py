
from conftest import make_tree
from treequery.search import ProfileSettings, explain, run_search
from treequery.text import TextResult, TextStatus

TERMS = {"contractor": {"weight": 5, "any": ["Morgan", "Raynal"]},
         "doctype": {"weight": 3, "any": ["contract", "invoice"]},
         "money": {"weight": 2, "regex": r"\$[\d,]+", "capture": True}}


def fake_reader(texts):
    """Reader serving texts by file name; records which files were read."""
    reads = []

    def read(entry):
        reads.append(entry.name)
        return texts.get(entry.name, TextResult(TextStatus.OK, ""))
    read.reads = reads
    return read


def make_cfg(tmp_path, **kw):
    kw.setdefault("query", "ext:txt,pdf AND (contractor OR doctype)")
    return ProfileSettings(roots=(str(tmp_path),), exclude_dirs=(), terms=TERMS, hash_results=kw.pop("hash_results", False), **kw)


def build(tmp_path):
    make_tree(tmp_path, {"a.txt": "x", "b.txt": "y", "c.pdf": "z", "d.jpg": "j", "e.doc": "w", "sub/f.txt": "same", "sub/g.txt": "same"})


def test_ranked_candidates_and_funnel_counts(tmp_path):
    build(tmp_path)
    texts = {"a.txt": TextResult(TextStatus.OK, "invoice from Morgan for $4,500"),
             "b.txt": TextResult(TextStatus.OK, "nothing relevant"),
             "c.pdf": TextResult(TextStatus.OK, "a contract"),
             "f.txt": TextResult(TextStatus.OK, "Raynal"), "g.txt": TextResult(TextStatus.OK, "none")}
    result = run_search(make_cfg(tmp_path), reader=fake_reader(texts))
    ranked = [(c.entry.name, c.card.total) for c in result.candidates]
    assert ranked[0][0] == "a.txt" and {n for n, _ in ranked} == {"a.txt", "c.pdf", "f.txt"}
    assert result.candidates[0].card.captured == ("$4,500",)
    assert result.rejected_by_query == 4         # d.jpg, e.doc by extension (free) plus b.txt, g.txt by text


def test_images_and_doc_files_are_never_read_when_extension_decides(tmp_path):
    build(tmp_path)
    reader = fake_reader({})
    run_search(make_cfg(tmp_path), reader=reader)
    assert "d.jpg" not in reader.reads and "e.doc" not in reader.reads


def test_dry_run_reads_nothing_and_counts_would_read(tmp_path):
    build(tmp_path)
    reader = fake_reader({})
    result = run_search(make_cfg(tmp_path), dry_run=True, reader=reader)
    assert reader.reads == [] and result.would_read == 5 and result.candidates == []


def test_unreadable_files_are_listed_not_dropped(tmp_path):
    build(tmp_path)
    texts = {"c.pdf": TextResult(TextStatus.NO_TEXT, "", "PDF contains no extractable text")}
    result = run_search(make_cfg(tmp_path), reader=fake_reader(texts))
    assert [(u.entry.name, u.status) for u in result.unread] == [("c.pdf", TextStatus.NO_TEXT)]
    assert "c.pdf" not in {c.entry.name for c in result.candidates}


def test_name_match_is_a_candidate_without_reading_when_scoring_off(tmp_path):
    make_tree(tmp_path, {"Morgan_contract.txt": "x"})
    reader = fake_reader({})
    cfg = make_cfg(tmp_path, query="contractor", score=False)
    result = run_search(cfg, reader=reader)
    assert [c.entry.name for c in result.candidates] == ["Morgan_contract.txt"]
    assert reader.reads == [] and result.candidates[0].text_status is TextStatus.NOT_READ


def test_duplicate_copies_share_a_hash(tmp_path):
    build(tmp_path)
    texts = {"f.txt": TextResult(TextStatus.OK, "Raynal"), "g.txt": TextResult(TextStatus.OK, "Raynal")}
    cfg = make_cfg(tmp_path, hash_results=True)
    result = run_search(cfg, reader=fake_reader(texts))
    by_name = {c.entry.name: c.sha256 for c in result.candidates}
    assert by_name["f.txt"] == by_name["g.txt"] != ""


def test_empty_query_returns_every_file(tmp_path):
    build(tmp_path)
    result = run_search(make_cfg(tmp_path, query="", score=False), reader=fake_reader({}))
    assert len(result.candidates) == 7


def test_bad_query_fails_when_settings_are_built(tmp_path):
    import pytest
    from treequery.query import ParseError
    with pytest.raises(ParseError):
        ProfileSettings(roots=(str(tmp_path),), query="nosuchgroup")


def test_explain_reports_each_stage(tmp_path):
    build(tmp_path)
    cfg = make_cfg(tmp_path, query="ext:txt AND contractor")
    lines = explain(cfg, str(tmp_path / "a.txt"), reader=fake_reader({"a.txt": TextResult(TextStatus.OK, "Morgan")}))
    text = "\n".join(lines)
    assert "reached" in text and "keep" in text and "contractor: yes" in text and "yes" in lines[-2]
    assert "not under any root" in explain(cfg, "/elsewhere/x.txt")[0]
