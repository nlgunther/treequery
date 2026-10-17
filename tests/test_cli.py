import csv

import pytest

pytest.importorskip("readers")

from conftest import make_tree
from treequery.cli import main

PROFILE = """
exclude_dirs: [skipme]
query: "ext:txt AND (contractor OR doctype)"
terms:
  contractor: {weight: 5, any: [Morgan]}
  doctype: {weight: 3, any: [invoice]}
score: true
"""


def setup(tmp_path):
    make_tree(tmp_path, {"data/a.txt": "invoice from Morgan", "data/b.txt": "nothing", "data/skipme/c.txt": "Morgan invoice"})
    profile = tmp_path / "p.yml"
    profile.write_text(PROFILE)
    return profile, tmp_path / "data", tmp_path / "out.csv"


def test_run_writes_both_csvs(tmp_path, capsys):
    profile, data, out = setup(tmp_path)
    assert main(["run", "-p", str(profile), "--roots", str(data), "--output", str(out)]) == 0
    rows = list(csv.DictReader(open(out, encoding="utf-8-sig")))
    assert [r["name"] for r in rows] == ["a.txt"] and rows[0]["hits"] == "contractor=1; doctype=1"
    assert (tmp_path / "out_unread.csv").exists()
    assert "Wrote" in capsys.readouterr().out


def test_command_line_overrides_profile_and_no_prune_flag_exists(tmp_path):
    profile, data, out = setup(tmp_path)
    assert main(["run", "-p", str(profile), "--roots", str(data), "--output", str(out), "--exclude-dirs"]) == 0
    assert len(list(csv.DictReader(open(out, encoding="utf-8-sig")))) == 2    # excludes cleared -> skipme searched
    assert main(["run", "-p", str(profile), "--roots", str(data), "--output", str(out), "--no-prune"]) == 0


def test_funnel_reads_nothing_and_prints_stages(tmp_path, capsys):
    profile, data, _ = setup(tmp_path)
    assert main(["funnel", "-p", str(profile), "--roots", str(data)]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out and "Folders:" in out


def test_explain_prints_a_verdict(tmp_path, capsys):
    profile, data, _ = setup(tmp_path)
    assert main(["explain", "-p", str(profile), "--roots", str(data), "-f", str(data / "skipme" / "c.txt")]) == 0
    assert "pruned" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["run", "-p", "nope.yml", "--roots", "."], ["run"], ["run", "--roots", ".", "--query", "bogus:1"]])
def test_bad_invocations_exit_2_with_a_message(argv, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(argv) == 2
    assert "treequery:" in capsys.readouterr().err


def test_missing_root_is_a_clean_error_not_a_traceback(tmp_path, capsys):
    assert main(["run", "--roots", str(tmp_path / "unplugged_drive")]) == 2
    assert "Not a directory" in capsys.readouterr().err
