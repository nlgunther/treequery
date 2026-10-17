"""Run the python/yaml blocks in docs/WORKFLOWS.md against a demo tree so the documentation cannot rot."""
import re
from pathlib import Path

import pytest

pytest.importorskip("readers")

from conftest import make_tree

DOC = Path(__file__).resolve().parents[1] / "docs" / "WORKFLOWS.md"
BLOCK = re.compile(r"```(python|yaml)\n(.*?)```", re.DOTALL)


@pytest.fixture
def demo(tmp_path, monkeypatch):
    make_tree(tmp_path, {
        "demo/DavisCA/Regatta/RAL_contract.txt": "RAL Builders contract total $4,500.00",
        "demo/DavisCA/Regatta/groceries.txt": "milk",
        "demo/DavisCA/invoice_2014.txt": "invoice paid $1,200",
        "demo/DavisCA/Photos/IMG.txt": "invoice $1",
        "demo/Drive/Moreno_wall.txt": "Lewis Moreno wall $5,000",
    })
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_every_block_in_workflows_runs(demo, capsys):
    blocks = BLOCK.findall(DOC.read_text(encoding="utf-8"))
    assert len(blocks) >= 6
    namespace = {}
    for kind, body in blocks:
        if kind == "yaml":
            first = body.splitlines()[0]
            assert first.startswith("# "), "name the yaml file in a first-line comment"
            Path(first[2:].strip()).write_text(body, encoding="utf-8")
        else:
            exec(compile(body, str(DOC), "exec"), namespace)          # noqa: S102 - our own documentation
    import csv
    names = {r["name"] for r in csv.DictReader(open("demo/candidates.csv", encoding="utf-8-sig"))}
    assert {"RAL_contract.txt", "Moreno_wall.txt", "invoice_2014.txt"} <= names and "groceries.txt" not in names
    out = capsys.readouterr().out
    assert "Folder rules:" in out and "Query 'contractor'" in out                     # workflow 3
    assert "files would be read" in out                                              # workflow 2
    assert "files to read:" in out and "Regatta's own files: 2" in out               # workflow 6
