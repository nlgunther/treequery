"""The installed entry points, run as real subprocesses (what Ken types at a prompt)."""
import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import make_tree

pytest.importorskip("readers")


SRC = str(Path(__file__).resolve().parents[1] / "src")


def run(*args, cwd=None):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [SRC, os.environ.get("PYTHONPATH", "")])),
           "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-m", "treequery", *args], capture_output=True, text=True, cwd=cwd, env=env)


@pytest.fixture
def project(tmp_path):
    make_tree(tmp_path, {"data/a.txt": "Raynal invoice", "data/b.txt": "nothing", "data/.hid/c.txt": "Raynal"})
    return tmp_path


def test_help_lists_commands_and_generated_flags():
    out = run("--help")
    assert out.returncode == 0 and all(w in out.stdout for w in ("run", "funnel", "explain"))
    sub = run("run", "--help").stdout
    for flag in ("--roots", "--exclude-dirs", "--no-prune", "--skip-hidden", "--cutoff", "--cloud-only", "--query", "--max-depth"):
        assert flag in sub
    assert "--terms" not in sub                       # YAML-only


def test_no_command_is_a_usage_error():
    assert run().returncode == 2


def test_no_roots_exits_2_with_a_hint(project):
    out = run("funnel", cwd=project)
    assert out.returncode == 2 and "no roots" in out.stderr


def test_flags_only_run_without_any_profile(project):
    data = project / "data"
    out = run("run", "--roots", str(data), "--query", "text:raynal", "--output", str(project / "o.csv"),
              "--skip-hidden", "--log-level", "ERROR")
    assert out.returncode == 0, out.stderr
    rows = list(csv.DictReader(open(project / "o.csv", encoding="utf-8-sig")))
    assert [r["name"] for r in rows] == ["a.txt"]


def test_default_profile_name_is_picked_up_from_cwd(project):
    (project / "treequery.yml").write_text(f"roots: ['{(project / 'data').as_posix()}']\nquery: 'text:raynal'\noutput: d.csv\nlog_level: ERROR\n")
    assert run("run", cwd=project).returncode == 0 and (project / "d.csv").exists()


def test_funnel_writes_nothing(project):
    out = run("funnel", "--roots", str(project / "data"), "--output", str(project / "never.csv"), "--log-level", "ERROR")
    assert out.returncode == 0 and "dry run" in out.stdout and not (project / "never.csv").exists()


def test_bad_choice_is_rejected_by_argparse(project):
    assert run("run", "--roots", str(project), "--cloud-only", "maybe").returncode == 2


def test_explain_prints_each_stage(project):
    f = project / "data" / "a.txt"
    out = run("explain", "--roots", str(project / "data"), "--query", "text:raynal", "-f", str(f), "--log-level", "ERROR")
    assert out.returncode == 0
    for fragment in ("Folder rules:", "File rules: keep", "text:raynal: yes", "Query"):
        assert fragment in out.stdout
