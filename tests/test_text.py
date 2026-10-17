import zipfile
from datetime import datetime

import pytest

pytest.importorskip("readers")

from treequery.text import TextStatus, read_text
from treequery.walk import FileEntry

ODF = ('<?xml version="1.0"?><office:document-content '
       'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
       'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:text>'
       '<text:p>{}</text:p></office:text></office:body></office:document-content>')


def entry_for(path):
    name = path.name
    return FileEntry(str(path), str(path.parent), name, name.rsplit(".", 1)[-1].lower(), path.stat().st_size,
                     datetime.now(), False)


def odf_file(tmp_path, name, body):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("content.xml", ODF.format(body))
    return path


def test_plain_text_is_read_and_truncated(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("hello world", encoding="utf-8")
    result = read_text(entry_for(path), max_chars=5)
    assert (result.status, result.text) == (TextStatus.OK, "hello")


def test_ods_is_read_through_the_odt_reader(tmp_path):
    result = read_text(entry_for(odf_file(tmp_path, "costs.ods", "RAL Builders $4,500")))
    assert result.status is TextStatus.OK and "RAL Builders" in result.text


def test_odt_is_read(tmp_path):
    assert "Morgan" in read_text(entry_for(odf_file(tmp_path, "n.odt", "Morgan"))).text


@pytest.mark.parametrize("name, hint", [("old.doc", "legacy Word"), ("scan.jpg", "OCR")])
def test_unsupported_formats_say_how_to_proceed(tmp_path, name, hint):
    path = tmp_path / name
    path.write_bytes(b"x")
    result = read_text(entry_for(path))
    assert result.status is TextStatus.UNSUPPORTED and hint in result.detail


def test_blank_pdf_is_no_text_not_a_crash(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(100, 100)
    path = tmp_path / "scan.pdf"
    with open(path, "wb") as fh:
        writer.write(fh)
    assert read_text(entry_for(path)).status is TextStatus.NO_TEXT


def test_corrupt_file_is_an_error_status(tmp_path):
    path = tmp_path / "bad.odt"
    path.write_bytes(b"not a zip")
    assert read_text(entry_for(path)).status is TextStatus.ERROR
