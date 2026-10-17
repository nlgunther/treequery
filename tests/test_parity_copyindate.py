"""
Parity with copyindate2code.copy_tree's file selection.

``legacy_selection`` is that function's selection logic (exclude / shallow / keep / skip-extension /
future-guard / cutoff) with the copying and target comparison removed.  If treequery selects the same
files, copyindate2code can replace its walk with ``walk_tree`` and keep every setting name.
"""
import os
import re
from datetime import datetime, timedelta

from conftest import make_tree, set_mtime
from treequery.walk import TreeRules, TreeSettings, WalkStats, walk_tree


def legacy_selection(src_dir, exclude, shallow, keep, skip_pattern, cutoff, run_start):
    skip = re.compile(skip_pattern)
    shallow_c = tuple(re.compile(p, re.IGNORECASE) for p in shallow)
    keep_c = tuple(re.compile(p, re.IGNORECASE) for p in keep)
    chosen = set()
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in exclude]
        folder = os.path.basename(root)
        if any(p.search(folder) for p in shallow_c) and not any(p.search(root) for p in keep_c):
            dirs[:] = []
        for name in files:
            path = os.path.join(root, name)
            if skip.search(os.path.splitext(path)[-1]):
                continue
            stamp = datetime.fromtimestamp(os.path.getmtime(path))
            if stamp > run_start or (cutoff and cutoff > stamp):
                continue
            chosen.add(path)
    return chosen


def test_same_files_as_copyindate2code(tmp_path):
    make_tree(tmp_path, {
        "PortFiles/a.txt": "a", "PortFiles/old.txt": "old", "PortFiles/mail.eml": "m",
        "PortFiles/venv/lib.py": "v", "PortFiles/x/y/z.txt": "z",
        "PortFiles/PWPWeb/page.html": "p", "PortFiles/NLGFiles/NLGMassFiles/m.odt": "m",
        "PortFiles/NLGFiles/NLGMassFiles/sub/s.odt": "s", "PortFiles/NLGFiles/n.txt": "n",
        "Downloads/d.txt": "d", "Downloads/Kindle/k.txt": "k", "Downloads/other/o.txt": "o",
        "Holidays/h.txt": "h", "Holidays/2020/h2.txt": "h2",
    })
    set_mtime(tmp_path / "PortFiles" / "old.txt", datetime(2019, 1, 1))
    exclude = ("System Volume Information", "RECYCLE.BIN", "NLGMassFiles", "PWPWeb")
    shallow, keep = ("Downloads", "Holidays"), ("Kindle",)
    pattern, cutoff, run_start = r"\.eml$|venv", datetime(2020, 1, 1), datetime.now() + timedelta(seconds=5)

    expected = legacy_selection(str(tmp_path), exclude, shallow, keep, pattern, cutoff, run_start)
    cfg = TreeSettings(roots=(str(tmp_path),), exclude_dirs=exclude, shallow_dirs=shallow, keep_dirs=keep,
                       skip_extension_pattern=pattern, cutoff="2020-01-01")
    rules = TreeRules.from_settings(cfg, run_start=run_start)
    actual = {e.path for e in walk_tree(cfg.roots, rules, WalkStats())}

    assert actual == expected
    assert actual  # guard against both sides being empty
