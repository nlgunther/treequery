"""Folder-rule matrix and filesystem edge cases for walk_tree."""
import os
import sys
from datetime import datetime, timedelta

import pytest

from conftest import make_tree, names, set_mtime
from test_walk import walk
from treequery.walk import FileVerdict, TreeRules, TreeSettings, WalkStats, explain_scope, walk_tree


@pytest.fixture
def deep(tmp_path):
    make_tree(tmp_path, {"r.txt": "", "a/a1.txt": "", "a/b/b1.txt": "", "a/b/c/c1.txt": "", "a/b/c/d/d1.txt": "",
                         "x/skip/s1.txt": "", "x/skip/inner/s2.txt": "", "x/y.txt": ""})
    return tmp_path


# prune x exclude_dirs: the DOS /s analogy
@pytest.mark.parametrize("prune, expected", [
    (True, {"r.txt", "a/a1.txt", "a/b/b1.txt", "a/b/c/c1.txt", "a/b/c/d/d1.txt", "x/y.txt"}),
    (False, {"r.txt", "a/a1.txt", "a/b/b1.txt", "a/b/c/c1.txt", "a/b/c/d/d1.txt", "x/y.txt", "x/skip/inner/s2.txt"}),
])
def test_prune_matrix(deep, prune, expected):
    found, _ = walk(deep, exclude_dirs=("skip",), prune=prune)
    assert names(found) == expected


@pytest.mark.parametrize("depth, expected_deepest", [(0, "r.txt"), (1, "a/a1.txt"), (2, "a/b/b1.txt"), (3, "a/b/c/c1.txt")])
def test_max_depth_counts_roots_as_zero(deep, depth, expected_deepest):
    found, _ = walk(deep, max_depth=depth)
    assert expected_deepest in names(found)
    assert max(n.count("/") for n in names(found)) == depth


def test_exclude_is_case_insensitive_and_matches_whole_names_only(tmp_path):
    make_tree(tmp_path, {"Node_Modules/a.txt": "", "node_modules_backup/b.txt": "", "x/NODE_MODULES/c.txt": ""})
    found, _ = walk(tmp_path, exclude_dirs=("node_modules",))
    assert names(found) == {"node_modules_backup/b.txt"}


def test_shallow_dir_yields_only_its_own_files(tmp_path):
    make_tree(tmp_path, {"Downloads/a.txt": "", "Downloads/sub/b.txt": "", "Other/c.txt": "", "Other/sub/d.txt": ""})
    found, _ = walk(tmp_path, shallow_dirs=("^downloads$",))
    assert names(found) == {"Downloads/a.txt", "Other/c.txt", "Other/sub/d.txt"}


def test_keep_dirs_overrides_shallow(tmp_path):
    make_tree(tmp_path, {"Downloads/a.txt": "", "Downloads/Taxes/b.txt": ""})
    found, _ = walk(tmp_path, shallow_dirs=("^downloads$",), keep_dirs=(r"Downloads$",))
    assert "Downloads/Taxes/b.txt" in names(found)


def test_hidden_folder_is_skipped_but_hidden_root_is_not(tmp_path):
    root = tmp_path / ".config_root"
    make_tree(root, {"a.txt": "", ".sub/b.txt": ""})
    found, _ = walk(root, skip_hidden=True)
    assert names(found) == {"a.txt"}


def test_multiple_roots_and_missing_root(tmp_path):
    make_tree(tmp_path, {"one/a.txt": "", "two/b.txt": ""})
    cfg = TreeSettings(roots=(str(tmp_path / "one"), str(tmp_path / "two")), exclude_dirs=())
    found = list(walk_tree(cfg.roots, TreeRules.from_settings(cfg), WalkStats()))
    assert {e.name for e in found} == {"a.txt", "b.txt"} and {os.path.basename(e.top) for e in found} == {"one", "two"}
    with pytest.raises(NotADirectoryError, match="gone"):
        list(walk_tree([str(tmp_path / "gone")], TreeRules.from_settings(cfg), WalkStats()))


def test_a_file_given_as_a_root_is_rejected(tmp_path):
    make_tree(tmp_path, {"a.txt": ""})
    with pytest.raises(NotADirectoryError):
        list(walk_tree([str(tmp_path / "a.txt")], TreeRules.from_settings(TreeSettings()), WalkStats()))


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlink_loop_terminates_and_does_not_follow_links(tmp_path):
    make_tree(tmp_path, {"real/a.txt": ""})
    os.symlink(tmp_path, tmp_path / "real" / "loop")
    found, _ = walk(tmp_path)
    assert names(found) == {"real/a.txt"}


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions and a non-root user")
def test_unreadable_folder_is_recorded_not_fatal(tmp_path):
    make_tree(tmp_path, {"ok/a.txt": "", "locked/b.txt": ""})
    os.chmod(tmp_path / "locked", 0)
    try:
        found, stats = walk(tmp_path)
    finally:
        os.chmod(tmp_path / "locked", 0o700)
    assert names(found) == {"ok/a.txt"} and stats.errors


def test_file_vanishing_mid_walk_is_recorded(tmp_path, monkeypatch):
    make_tree(tmp_path, {"a.txt": "", "b.txt": ""})
    import treequery.walk as walk_module
    real = walk_module.make_entry

    def flaky(path, top):
        if path.endswith("a.txt"):
            raise FileNotFoundError("vanished")
        return real(path, top)
    monkeypatch.setattr(walk_module, "make_entry", flaky)
    found, stats = walk(tmp_path)
    assert names(found) == {"b.txt"} and len(stats.errors) == 1 and "vanished" in stats.errors[0]


# ---- file rules --------------------------------------------------------------------------------------------
def test_file_rule_order_and_each_verdict(tmp_path):
    now = datetime.now()
    make_tree(tmp_path, {"old.txt": "x" * 10, "tiny.txt": "", "big.txt": "x" * 5000, "ok.txt": "x" * 100,
                         "skip_me.txt": "x" * 100, "other.pdf": "x" * 100, "mid.TXT": "x" * 100})
    set_mtime(tmp_path / "old.txt", datetime(2010, 1, 1))
    set_mtime(tmp_path / "ok.txt", now - timedelta(days=1))
    kw = dict(only_ext=(".txt",), cutoff="2013-01-01", min_size="50", max_size="1KB", skip_name_pattern="^skip_")
    found, stats = walk(tmp_path, **kw)
    assert "ok.txt" in names(found) and "mid.TXT" in names(found)          # extension match ignores case and dot
    assert {"old.txt", "tiny.txt", "big.txt", "skip_me.txt", "other.pdf"}.isdisjoint(names(found))
    assert {FileVerdict.TOO_OLD, FileVerdict.TOO_SMALL, FileVerdict.TOO_BIG, FileVerdict.SKIP_NAME,
            FileVerdict.ONLY_EXT} <= set(stats.verdicts)
    assert stats.files_seen == sum(stats.verdicts.values())


def test_modified_before_and_future_guard(tmp_path):
    make_tree(tmp_path, {"past.txt": "", "future.txt": ""})
    set_mtime(tmp_path / "past.txt", datetime(2012, 1, 1))
    set_mtime(tmp_path / "future.txt", datetime.now() + timedelta(days=30))
    found, stats = walk(tmp_path)
    assert names(found) == {"past.txt"} and stats.verdicts[FileVerdict.FUTURE] == 1
    found, _ = walk(tmp_path, guard_future=False)
    assert names(found) == {"past.txt", "future.txt"}
    found, _ = walk(tmp_path, guard_future=False, modified_before="2013-01-01")
    assert names(found) == {"past.txt"}


def test_cloud_only_modes(tmp_path, monkeypatch):
    make_tree(tmp_path, {"local.txt": "", "online.txt": ""})
    import treequery.walk as walk_module
    real = walk_module.make_entry

    def fake(path, top):
        entry = real(path, top)
        return entry.__class__(**{**entry.__dict__, "cloud_only": path.endswith("online.txt")})
    monkeypatch.setattr(walk_module, "make_entry", fake)
    assert names(walk(tmp_path, cloud_only="skip")[0]) == {"local.txt"}
    assert names(walk(tmp_path, cloud_only="list")[0]) == {"local.txt", "online.txt"}
    assert names(walk(tmp_path, cloud_only="read")[0]) == {"local.txt", "online.txt"}


@pytest.mark.parametrize("bits, expected", [(0, False), (0x20, False), (0x400000, True), (0x40000, True), (0x1000, True),
                                            (0x20 | 0x400000, True)])
def test_cloud_attribute_bits(bits, expected):
    from treequery.walk import is_cloud_only
    assert is_cloud_only(bits) is expected


@pytest.mark.parametrize("field, value", [("cloud_only", "maybe"), ("skip_name_pattern", "("), ("cutoff", "2013-13-45"),
                                          ("max_size", "lots"), ("shallow_dirs", ("[",))])
def test_invalid_settings_fail_at_construction(field, value):
    with pytest.raises(ValueError):
        TreeSettings(**{field: value})


def test_entry_relative_dir(tmp_path):
    make_tree(tmp_path, {"a/b/c.txt": "", "top.txt": ""})
    found, _ = walk(tmp_path)
    rel = {e.name: e.relative_dir for e in found}
    assert rel == {"c.txt": os.path.join("a", "b"), "top.txt": "."}


# ---- explain_scope covers every folder rule -------------------------------------------------------------------
@pytest.mark.parametrize("settings, rel, fragment", [
    (dict(exclude_dirs=("skip",)), "x/skip/inner/s2.txt", "whole subtree"),
    (dict(exclude_dirs=("skip",), prune=False), "x/skip/s1.txt", "only its own files"),
    (dict(skip_hidden=True), ".hid/h.txt", "hidden folder"),
    (dict(max_depth=1), "a/b/b1.txt", "max_depth"),
    (dict(shallow_dirs=("^a$",)), "a/b/b1.txt", "shallow"),
    (dict(), "a/a1.txt", "reached"),
])
def test_explain_scope_messages(deep, settings, rel, fragment):
    settings.setdefault("exclude_dirs", ())
    rules = TreeRules.from_settings(TreeSettings(roots=(str(deep),), **settings))
    path = os.path.join(str(deep), *rel.split("/"))
    assert fragment in explain_scope(rules, str(deep), path)[0]
