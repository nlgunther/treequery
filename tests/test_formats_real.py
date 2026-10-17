"""The text stage and a whole search against REAL files of every supported format (needs readers + its extras)."""
import csv
import os

import pytest

pytest.importorskip("readers")
reportlab_canvas = pytest.importorskip("reportlab.pdfgen.canvas")
docx = pytest.importorskip("docx")
openpyxl = pytest.importorskip("openpyxl")
odf_opendocument = pytest.importorskip("odf.opendocument")

from odf.opendocument import OpenDocumentSpreadsheet, OpenDocumentText
from odf.table import Table, TableCell, TableRow
from odf.text import P

from treequery.cli import main
from treequery.report import write_csvs
from treequery.search import ProfileSettings, run_search
from treequery.text import TextStatus, read_text
from treequery.walk import make_entry

SENTENCE = "Invoice from Philip Raynal for patio structure, total $4,500.00"


def make_pdf(path, text=SENTENCE):
    c = reportlab_canvas.Canvas(str(path))
    if text:
        c.drawString(72, 720, text)
    c.save()


def make_docx(path, text=SENTENCE):
    document = docx.Document()
    document.add_paragraph(text)
    document.save(str(path))


def make_xlsx(path, text=SENTENCE):
    book = openpyxl.Workbook()
    book.active.append(["vendor", text])
    book.save(str(path))


def make_odt(path, text=SENTENCE):
    doc = OpenDocumentText()
    doc.text.addElement(P(text=text))
    doc.save(str(path))


def make_ods(path, text=SENTENCE):
    doc = OpenDocumentSpreadsheet()
    table = Table(name="Sheet1")
    row = TableRow()
    cell = TableCell(valuetype="string")
    cell.addElement(P(text=text))
    row.addElement(cell)
    table.addElement(row)
    doc.spreadsheet.addElement(table)
    doc.save(str(path))


def make_txt(path, text=SENTENCE):
    path.write_text(text, encoding="utf-8")


MAKERS = {"pdf": make_pdf, "docx": make_docx, "xlsx": make_xlsx, "odt": make_odt, "ods": make_ods, "txt": make_txt}


@pytest.mark.parametrize("ext", sorted(MAKERS))
def test_each_format_yields_its_text(tmp_path, ext):
    path = tmp_path / f"doc.{ext}"
    MAKERS[ext](path)
    result = read_text(make_entry(str(path), str(tmp_path)))
    assert result.status is TextStatus.OK, result.detail
    assert "Raynal" in result.text and "4,500" in result.text


def test_text_is_truncated_to_max_chars(tmp_path):
    make_txt(tmp_path / "long.txt", "x" * 5000)
    result = read_text(make_entry(str(tmp_path / "long.txt"), str(tmp_path)), max_chars=100)
    assert len(result.text) == 100


def test_scanned_style_pdf_with_no_text_layer_is_no_text(tmp_path):
    make_pdf(tmp_path / "blank.pdf", text="")
    result = read_text(make_entry(str(tmp_path / "blank.pdf"), str(tmp_path)))
    assert result.status in (TextStatus.NO_TEXT, TextStatus.OK) and result.text.strip() == ""


@pytest.mark.parametrize("ext", ["pdf", "docx", "xlsx", "odt", "ods"])
def test_corrupt_file_is_an_error_not_an_exception(tmp_path, ext):
    path = tmp_path / f"broken.{ext}"
    path.write_bytes(b"this is not really a document \x00\x01\x02")
    result = read_text(make_entry(str(path), str(tmp_path)))
    assert result.status in (TextStatus.ERROR, TextStatus.NO_TEXT) and result.text == ""


@pytest.mark.parametrize("ext, fragment", [("doc", "legacy Word"), ("xls", "legacy Excel"), ("rtf", "rich text"),
                                           ("jpg", "OCR"), ("heic", "OCR")])
def test_unsupported_extensions_carry_a_hint(tmp_path, ext, fragment):
    path = tmp_path / f"old.{ext}"
    path.write_bytes(b"x")
    result = read_text(make_entry(str(path), str(tmp_path)))
    assert result.status is TextStatus.UNSUPPORTED and fragment in result.detail


def test_empty_file_does_not_crash(tmp_path):
    (tmp_path / "empty.txt").write_bytes(b"")
    result = read_text(make_entry(str(tmp_path / "empty.txt"), str(tmp_path)))
    assert result.status in (TextStatus.OK, TextStatus.NO_TEXT) and result.text == ""


def test_unicode_file_names_and_text_round_trip(tmp_path):
    make_txt(tmp_path / "Señor_Müller_契約.txt", "Contrato: José Müller 契約書 $1,200")
    cfg = ProfileSettings(roots=(str(tmp_path),), exclude_dirs=(), query="text:müller", hash_results=False)
    found = run_search(cfg).candidates
    assert [c.entry.name for c in found] == ["Señor_Müller_契約.txt"]


# ---- the whole funnel on a realistic mixed tree ---------------------------------------------------------
def build_mixed_tree(tmp_path):
    tree = tmp_path / "Regatta"
    for sub in ("contracts", "scans", "misc/.git", "misc/node_modules"):
        (tree / sub).mkdir(parents=True)
    make_pdf(tree / "contracts" / "RAL_contract.pdf")
    make_docx(tree / "contracts" / "Raynal_invoice.docx")
    make_xlsx(tree / "costs.xlsx", "Raynal patio 4500")
    make_ods(tree / "costs.ods", "Raynal wall 5000")
    make_odt(tree / "notes.odt", "Lewis Moreno wall $5,000")
    make_txt(tree / "grocery.txt", "milk eggs")
    make_pdf(tree / "scans" / "scan0001.pdf", text="")                    # image-only PDF: undecidable
    (tree / "scans" / "invoice_old.doc").write_bytes(b"legacy")           # unsupported, but the name matches
    (tree / "scans" / "IMG_0001.jpg").write_bytes(b"jpeg")
    make_txt(tree / "misc" / ".git" / "config", "Raynal")                 # must be pruned
    make_txt(tree / "misc" / "node_modules" / "x.txt", "Raynal")          # must be pruned
    return tree


def profile_for(tree, **kw):
    terms = {"who": {"weight": 5, "any": ["Raynal", "Moreno", "RAL"]},
             "doc": {"weight": 3, "any": ["invoice", "contract"]},
             "money": {"weight": 2, "regex": r"\$[\d,]+(\.\d\d)?", "capture": True}}
    base = dict(roots=(str(tree),), terms=terms, query="who OR doc", hash_results=True)
    base.update(kw)
    return ProfileSettings(**base)


def test_full_search_finds_every_readable_match_and_lists_the_rest(tmp_path):
    result = run_search(profile_for(build_mixed_tree(tmp_path)))
    found = {c.entry.name for c in result.candidates}
    # invoice_old.doc is a candidate on its NAME alone, so its unreadable text never matters
    assert found == {"RAL_contract.pdf", "Raynal_invoice.docx", "costs.xlsx", "costs.ods", "notes.odt", "invoice_old.doc"}
    unread = {u.entry.name: u.status for u in result.unread}
    assert unread == {"scan0001.pdf": TextStatus.NO_TEXT, "IMG_0001.jpg": TextStatus.UNSUPPORTED}
    assert "config" not in found and "x.txt" not in found
    assert "grocery.txt" not in found


def test_scores_rank_name_and_text_hits_and_capture_amounts(tmp_path):
    result = run_search(profile_for(build_mixed_tree(tmp_path)))
    by_name = {c.entry.name: c for c in result.candidates}
    assert by_name["RAL_contract.pdf"].card.total > by_name["costs.xlsx"].card.total
    assert any("4,500" in cap for cap in by_name["RAL_contract.pdf"].card.captured)
    assert by_name["notes.odt"].card.captured == ("$5,000",)
    assert result.candidates == sorted(result.candidates, key=lambda c: (-c.card.total, c.entry.path.lower()))


def test_identical_copies_are_grouped_by_hash(tmp_path):
    tree = build_mixed_tree(tmp_path)
    (tree / "backup").mkdir()
    (tree / "backup" / "notes_copy.odt").write_bytes((tree / "notes.odt").read_bytes())
    result = run_search(profile_for(tree))
    out = tmp_path / "out.csv"
    write_csvs(result, str(out))
    rows = list(csv.DictReader(open(out, encoding="utf-8-sig")))
    dupes = [r for r in rows if r["name"] in ("notes.odt", "notes_copy.odt")]
    assert len(dupes) == 2 and {r["copies"] for r in dupes} == {"2"} and len({r["sha256"] for r in dupes}) == 1


def test_dry_run_reads_nothing_and_reports_the_cost(tmp_path):
    tree = build_mixed_tree(tmp_path)
    result = run_search(profile_for(tree, query="ext:pdf,docx AND who"), dry_run=True)
    assert result.text_reads == 0 and result.candidates == []
    assert result.matched_without_reading == 2        # RAL_contract.pdf, Raynal_invoice.docx: the name decides
    assert result.would_read == 1                     # scan0001.pdf: name is silent, so its text would be read


def test_cli_end_to_end_on_real_files(tmp_path, capsys):
    tree = build_mixed_tree(tmp_path)
    out = tmp_path / "found.csv"
    p = tmp_path / "p.yml"
    p.write_text(f"""
roots: ['{tree.as_posix()}']
query: "who OR doc"
output: '{out.as_posix()}'
terms:
  who: {{weight: 5, any: [Raynal, Moreno, RAL]}}
  doc: {{weight: 3, any: [invoice, contract]}}
""")
    assert main(["run", "-p", str(p), "--log-level", "ERROR"]) == 0
    text = capsys.readouterr().out
    assert "candidates" in text and os.path.exists(out) and os.path.exists(tmp_path / "found_unread.csv")
