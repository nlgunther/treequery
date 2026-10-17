import os
from datetime import datetime, timedelta

import pytest

from conftest import make_tree, names, set_mtime
from treequery.walk import (FileEntry, FileVerdict, TreeRules, TreeSettings, WalkStats, explain_scope, is_cloud_only,
                            walk_tree)


def walk(tmp, **overrides):
    cfg = TreeSettings(roots=(str(tmp),), exclude_dirs=overrides.pop("exclude_dirs", ()), **overrides)
    rules = TreeRules.from_settings(cfg)
    stats = WalkStats()
    return list(walk_tree(cfg.roots, rules, stats)), stats


def test_everything_when_no_rules(tree):
    found, _ = walk(tree)
    assert "a/skipme/deeper/deep.txt" in names(found) and len(found) == 9


def test_exclude_prunes_whole_subtree_by_default(tree):
    found, stats = walk(tree, exclude_dirs=("skipme",))
    assert names(found) >= {"a/one.txt", "a/keep/two.pdf"}
    assert not {"a/skipme/inside.txt", "a/skipme/deeper/deep.txt"} & names(found)
    assert stats.dirs_pruned == 1


def test_no_prune_skips_only_that_folders_own_files(tree):
    found, _ = walk(tree, exclude_dirs=("skipme",), prune=False)
    assert "a/skipme/inside.txt" not in names(found)          # own file skipped
    assert "a/skipme/deeper/deep.txt" in names(found)         # subfolder still searched


def test_no_prune_still_applies_other_rules_to_subfolders(tree):
    found, _ = walk(tree, exclude_dirs=("skipme",), prune=False, skip_extension_pattern=r"\.txt$")
    assert "a/skipme/deeper/deep.txt" not in names(found)


def test_exclude_is_case_insensitive(tree):
    found, _ = walk(tree, exclude_dirs=("SKIPME",))
    assert "a/skipme/inside.txt" not in names(found)


def test_shallow_dir_gives_only_top_level_files_unless_kept(tree):
    found, _ = walk(tree, shallow_dirs=("^downloads$",))
    assert {"Downloads/d1.txt"} <= names(found) and "Downloads/sub/d2.txt" not in names(found)
    found, _ = walk(tree, shallow_dirs=("^downloads$",), keep_dirs=("Downloads",))
    assert "Downloads/sub/d2.txt" in names(found)


def test_max_depth(tree):
    assert names(walk(tree, max_depth=0)[0]) == {"top.txt"}
    assert "a/one.txt" in names(walk(tree, max_depth=1)[0])
    assert "a/skipme/inside.txt" not in names(walk(tree, max_depth=1)[0])


def test_skip_hidden(tree):
    assert ".hidden/h.txt" in names(walk(tree)[0])
    assert ".hidden/h.txt" not in names(walk(tree, skip_hidden=True)[0])


def test_extension_rules(tree):
    assert "b/mail.eml" not in names(walk(tree, skip_extension_pattern=r"\.eml$")[0])
    assert names(walk(tree, only_ext=("pdf",))[0]) == {"a/keep/two.pdf"}
    assert names(walk(tree, only_ext=(".PDF",))[0]) == {"a/keep/two.pdf"}      # dot and case don't matter


def test_name_pattern_and_size(tree):
    assert "top.txt" not in names(walk(tree, skip_name_pattern="^top")[0])
    make_tree(tree, {"big.bin": "x" * 5000})
    assert names(walk(tree, min_size="1KB")[0]) == {"big.bin"}
    assert "big.bin" not in names(walk(tree, max_size="1KB")[0])


def test_dates_cutoff_before_and_future_guard(tree):
    old, new = datetime(2012, 1, 1), datetime.now() - timedelta(days=1)
    set_mtime(tree / "top.txt", old)
    set_mtime(tree / "a" / "one.txt", new)
    assert "top.txt" not in names(walk(tree, cutoff="2013-01-01")[0])
    assert names(walk(tree, modified_before="2013-01-01")[0]) == {"top.txt"}
    set_mtime(tree / "top.txt", datetime.now() + timedelta(days=30))
    found, stats = walk(tree)
    assert "top.txt" not in names(found) and stats.verdicts[FileVerdict.FUTURE] == 1
    assert "top.txt" in names(walk(tree, guard_future=False)[0])


def test_missing_root_fails_loudly(tmp_path):
    cfg = TreeSettings(roots=(str(tmp_path / "nope"),))
    with pytest.raises(NotADirectoryError):
        list(walk_tree(cfg.roots, TreeRules.from_settings(cfg), WalkStats()))


def test_bad_regex_or_date_fails_at_construction():
    with pytest.raises(ValueError, match="Bad regex"):      # was re.error; now one exception type for all bad settings
        TreeSettings(skip_name_pattern="(")
    with pytest.raises(ValueError):
        TreeSettings(cutoff="yesterday")


def test_cloud_only_attribute_detection_and_policy():
    assert is_cloud_only(0x400000) and is_cloud_only(0x1000) and not is_cloud_only(0x20)
    rules = TreeRules.from_settings(TreeSettings(cloud_only="skip"))
    entry = FileEntry("p", "t", "p", "pdf", 1, datetime(2020, 1, 1), cloud_only=True)
    assert rules.file_verdict(entry) is FileVerdict.CLOUD_SKIPPED
    assert TreeRules.from_settings(TreeSettings(cloud_only="list")).file_verdict(entry) is FileVerdict.KEEP


def test_entry_fields_and_relative_dir(tree):
    found, _ = walk(tree)
    deep = next(e for e in found if e.name == "deep.txt")
    assert (deep.ext, deep.size, deep.relative_dir) == ("txt", 4, os.path.join("a", "skipme", "deeper"))


def test_explain_scope_names_the_rule(tree):
    rules = TreeRules.from_settings(TreeSettings(roots=(str(tree),), exclude_dirs=("skipme",), skip_hidden=True))
    deep = str(tree / "a" / "skipme" / "deeper" / "deep.txt")
    assert "exclude_dirs" in explain_scope(rules, str(tree), deep)[0]
    assert "hidden" in explain_scope(rules, str(tree), str(tree / ".hidden" / "h.txt"))[0]
    assert explain_scope(rules, str(tree), str(tree / "a" / "one.txt"))[0].startswith("reached")
