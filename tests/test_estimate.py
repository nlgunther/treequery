"""The time estimator: statistics in isolation, then the whole estimate against a synthetic machine with known timings."""
import math
import os
import random
from collections import Counter

import pytest

from conftest import make_tree
from treequery.cli import main
from treequery.estimate import (Census, Estimate, EstimateSettings, Item, Line, allocate, estimate, fit_line, format_estimate,
                                percentile, stat_census, weighted_sample)
from treequery.search import ProfileSettings
from treequery.text import TextResult, TextStatus
from treequery.units import format_duration, format_size

# ---- the synthetic machine ---------------------------------------------------------------------------------------------
TRUE = {"pdf": (0.30, 2.0e-7), "txt": (0.002, 2.0e-9), "docx": (0.08, 6.0e-8)}      # (fixed seconds, seconds per byte)


class Machine:
    """A fake clock that advances by a known cost whenever the reader is called; optional multiplicative noise
    (a 15% jitter, say, which keeps the mean cost equal to the nominal cost)."""

    def __init__(self, truth=TRUE, noise=0.0, seed=0):
        self.now, self.truth, self.noise, self.rng, self.reads = 0.0, truth, noise, random.Random(seed), []

    def clock(self):
        return self.now

    def reader(self, entry):
        fixed, per_byte = self.truth[entry.ext]
        self.now += (fixed + per_byte * entry.size) * max(0.0, 1.0 + self.rng.gauss(0, self.noise))
        self.reads.append(entry.path)
        return TextResult(TextStatus.OK, "")


def build(tmp_path, n=200, seed=3, subfolders=4, exts=("pdf", "txt", "docx"), max_size=2_000_000):
    """n sparse files with heavy-tailed sizes; returns {path: (ext, size)}."""
    rng, files = random.Random(seed), {}
    for i in range(n):
        ext = rng.choices(exts, [3, 5, 2][:len(exts)])[0]
        size = int(min(rng.paretovariate(1.1) * 2_000, max_size))
        path = tmp_path / f"s{i % subfolders}" / f"f{i}.{ext}"
        path.parent.mkdir(exist_ok=True)
        with open(path, "wb") as fh:
            fh.truncate(size)
        files[str(path)] = (ext, size)
    return files


def true_seconds(files, truth=TRUE, only=lambda path: True):
    return sum(truth[e][0] + truth[e][1] * s for p, (e, s) in files.items() if only(p))


def cfg_for(tmp_path, **kw):
    kw.setdefault("query", "text:needle")
    return ProfileSettings(roots=(str(tmp_path),), exclude_dirs=(), hash_results=False, **kw)


def run(tmp_path, machine=None, settings=None, **cfg_kw):
    machine = machine or Machine()
    settings = settings or EstimateSettings(sample=30, seed=1, max_seconds=1e9)
    return estimate(cfg_for(tmp_path, **cfg_kw), settings, reader=machine.reader, clock=machine.clock), machine


# ---- fit_line ------------------------------------------------------------------------------------------------------------
def test_fit_recovers_an_exact_line():
    line = fit_line([(0, 0.1), (1_000_000, 0.6), (3_000_000, 1.6)])
    assert line.fixed == pytest.approx(0.1) and line.per_byte == pytest.approx(5e-7) and line.sd == pytest.approx(0, abs=1e-9)


def test_fit_keeps_the_line_physical():
    decreasing = fit_line([(1000, 5.0), (2000, 3.0), (3000, 1.0)])           # bigger "faster": noise, not physics
    assert decreasing.per_byte == 0 and decreasing.fixed == pytest.approx(3.0)
    negative_intercept = fit_line([(1000, 0.1), (2000, 0.5), (3000, 0.9)])    # raw intercept is -0.3
    assert negative_intercept.fixed == 0 and negative_intercept.per_byte > 0


@pytest.mark.parametrize("points, fixed", [([], 0.0), ([(500, 0.4)], 0.4), ([(100, 0.2), (100, 0.4)], 0.3)])
def test_fit_degenerate_inputs_use_a_per_file_cost(points, fixed):
    line = fit_line(points)
    assert line.per_byte == 0 and line.fixed == pytest.approx(fixed)


def test_fit_reports_residual_spread():
    assert fit_line([(0, 0.0), (1, 1.0), (2, 0.0), (3, 1.0)]).sd > 0.3


# ---- allocate / weighted_sample / percentile ---------------------------------------------------------------------------
def test_allocate_gives_each_type_its_minimum_then_follows_bytes():
    take = allocate({"pdf": 100, "txt": 100}, {"pdf": 90.0, "txt": 10.0}, total=20, minimum=3)
    assert sum(take.values()) == 20 and take["txt"] >= 3 and take["pdf"] > take["txt"] * 3


def test_allocate_never_exceeds_the_files_available():
    assert allocate({"a": 2, "b": 50}, {"a": 1e9, "b": 1.0}, total=30, minimum=3) == {"a": 2, "b": 28}


def test_allocate_minimum_beats_total_when_there_are_many_types():
    take = allocate({e: 10 for e in "abcde"}, {e: 1.0 for e in "abcde"}, total=5, minimum=3)
    assert all(v == 3 for v in take.values())


def test_weighted_sample_is_distinct_deterministic_and_size_biased():
    items = list(range(50))
    first = weighted_sample(items, lambda i: 1.0 + 100 * (i == 7), 5, random.Random(0))
    assert len(set(first)) == 5 and first == weighted_sample(items, lambda i: 1.0 + 100 * (i == 7), 5, random.Random(0))
    hits = Counter(7 in weighted_sample(items, lambda i: 1.0 + 100 * (i == 7), 3, random.Random(s)) for s in range(400))
    assert hits[True] > 360                        # weight 101 against 49 others of weight 1: almost always included


def test_weighted_sample_asking_for_more_than_exists_returns_everything():
    assert sorted(weighted_sample([1, 2, 3], lambda i: 1.0, 10, random.Random(0))) == [1, 2, 3]


@pytest.mark.parametrize("q, expected", [(0, 1), (1, 5), (0.5, 3), (0.25, 2), (0.125, 1.5)])
def test_percentile_interpolates(q, expected):
    assert percentile([5, 1, 3, 2, 4], q) == pytest.approx(expected)


@pytest.mark.parametrize("kwargs", [dict(sample=0), dict(min_per_type=0), dict(interval=0), dict(interval=1),
                                    dict(group_depth=-1), dict(max_seconds=0)])
def test_invalid_estimate_settings(kwargs):
    with pytest.raises(ValueError):
        EstimateSettings(**kwargs)


# ---- estimate(): accuracy on a noiseless machine --------------------------------------------------------------------------
def test_noiseless_machine_is_estimated_exactly_from_a_small_sample(tmp_path):
    files = build(tmp_path)
    est, machine = run(tmp_path)
    assert est.total.seconds == pytest.approx(true_seconds(files), rel=1e-9)
    assert est.total.low <= est.total.seconds <= est.total.high or est.total.high == pytest.approx(est.total.seconds)
    assert len(machine.reads) == est.timed_files <= 30 + 3          # only the sample was read, never all 200 files


def test_sampling_everything_gives_the_measured_total_and_a_point_interval(tmp_path):
    files = build(tmp_path, n=40)
    est, machine = run(tmp_path, settings=EstimateSettings(sample=1000, seed=1, max_seconds=1e9))
    assert est.total.seconds == pytest.approx(true_seconds(files))
    assert est.total.low == pytest.approx(est.total.high) == pytest.approx(est.total.seconds)
    assert all(m.basis == "measured" for m in est.models) and len(machine.reads) == 40


def test_each_type_is_recovered_with_its_true_rate(tmp_path):
    build(tmp_path)
    est, _ = run(tmp_path)
    by_ext = {m.ext: m for m in est.models}
    for ext, (fixed, per_byte) in TRUE.items():
        assert by_ext[ext].line.per_byte == pytest.approx(per_byte, rel=1e-6) or by_ext[ext].basis == "measured"
    assert by_ext["pdf"].mb_per_second == pytest.approx(1 / 2.0e-7 / 1024**2, rel=1e-6)


def test_same_seed_same_answer_and_different_seed_different_sample(tmp_path):
    build(tmp_path)
    one, m1 = run(tmp_path, settings=EstimateSettings(sample=20, seed=5, max_seconds=1e9), machine=Machine(noise=0.01, seed=2))
    two, m2 = run(tmp_path, settings=EstimateSettings(sample=20, seed=5, max_seconds=1e9), machine=Machine(noise=0.01, seed=2))
    three, m3 = run(tmp_path, settings=EstimateSettings(sample=20, seed=6, max_seconds=1e9), machine=Machine(noise=0.01, seed=2))
    assert one.total == two.total and m1.reads == m2.reads
    assert set(m1.reads) != set(m3.reads)


def test_large_files_are_sampled_far_more_often_than_small_ones(tmp_path):
    """Size-weighted sampling: the few files that dominate the total are (nearly) always timed."""
    files = build(tmp_path)
    order = sorted(files, key=lambda p: -files[p][1])
    top, bottom, rounds = order[:10], order[len(order) // 2:], 30
    hits = Counter()
    for seed in range(rounds):
        _, machine = run(tmp_path, settings=EstimateSettings(sample=40, seed=seed, max_seconds=1e9))
        hits.update(machine.reads)
    top_rate = sum(hits[p] for p in top) / (len(top) * rounds)
    bottom_rate = sum(hits[p] for p in bottom) / (len(bottom) * rounds)
    assert top_rate > 0.6 and top_rate > 3 * bottom_rate


@pytest.mark.slow_statistics
def test_noisy_machine_is_unbiased_and_intervals_are_honest(tmp_path):
    """Over many seeds the mean error is small and the 80% range covers the truth most of the time."""
    files = build(tmp_path, n=300, seed=11)
    truth, ratios, covered, rounds = true_seconds(files), [], 0, 40
    for seed in range(rounds):
        machine = Machine(noise=0.15, seed=seed)
        est = estimate(cfg_for(tmp_path), EstimateSettings(sample=40, seed=seed, max_seconds=1e9),
                       reader=machine.reader, clock=machine.clock)
        ratios.append(est.total.seconds / truth)
        covered += est.total.low <= truth <= est.total.high
    assert abs(sum(ratios) / rounds - 1) < 0.03
    assert covered / rounds >= 0.6                         # nominal 0.8; the bootstrap is a little optimistic with 40 timings


# ---- estimate(): which files count -------------------------------------------------------------------------------------
def test_files_decided_by_name_are_not_counted_when_nothing_needs_their_text(tmp_path):
    build(tmp_path, n=30)
    est, machine = run(tmp_path, query="ext:pdf", score=False)
    assert est.total.files == 0 and est.total.seconds == 0 and machine.reads == []


def test_scoring_makes_name_matched_files_count_because_they_will_be_read(tmp_path):
    files = build(tmp_path, n=60)
    terms = {"who": {"weight": 1, "any": ["needle"]}}
    pdfs = sum(1 for e, _ in files.values() if e == "pdf")
    est, _ = run(tmp_path, query="ext:pdf", terms=terms, score=True)
    assert est.total.files == pdfs
    est, _ = run(tmp_path, query="ext:pdf", terms=terms, score=False)
    assert est.total.files == 0


def test_file_rules_apply_before_counting(tmp_path):
    files = build(tmp_path, n=60)
    est, _ = run(tmp_path, only_ext=("txt",))
    assert est.total.files == sum(1 for e, _ in files.values() if e == "txt")
    est, _ = run(tmp_path, only_ext=("txt",), max_size="1KB")
    assert est.total.files == sum(1 for e, s in files.values() if e == "txt" and s <= 1024)


def test_empty_tree_is_zero_not_a_crash(tmp_path):
    est, machine = run(tmp_path)
    assert est.total.files == 0 and est.total.seconds == 0 and est.groups == [] and machine.reads == []
    assert "TOTAL" in format_estimate(est)


def test_cloud_only_files_are_never_timed_and_are_reported(tmp_path, monkeypatch):
    build(tmp_path, n=30)
    import treequery.walk as walk_module
    real = walk_module.make_entry
    monkeypatch.setattr(walk_module, "make_entry", lambda path, top: (lambda e: e.__class__(**{**e.__dict__, "cloud_only": e.ext == "pdf"}))(real(path, top)))
    est, machine = run(tmp_path)
    assert est.cloud_only_files > 0 and not any(p.endswith(".pdf") for p in machine.reads)
    assert any("online-only" in note for note in est.notes)


def test_a_reader_that_raises_still_counts_its_time(tmp_path):
    build(tmp_path, n=20)
    machine = Machine()

    def failing(entry):
        machine.now += 0.5
        raise RuntimeError("corrupt")
    est = estimate(cfg_for(tmp_path), EstimateSettings(sample=1000, seed=1, max_seconds=1e9), reader=failing, clock=machine.clock)
    assert est.total.seconds == pytest.approx(0.5 * est.total.files)


def test_timing_stops_at_max_seconds_and_says_so(tmp_path):
    build(tmp_path, n=100)
    est, machine = run(tmp_path, settings=EstimateSettings(sample=60, seed=1, max_seconds=2.0))
    assert est.timing_seconds >= 2.0 and est.timed_files < 60 and any("limit" in n for n in est.notes)
    assert est.total.seconds > 0                          # still produces an estimate from what was timed


def test_types_with_too_little_data_borrow_the_pooled_line(tmp_path):
    build(tmp_path, n=80)
    make_tree(tmp_path, {"lonely.xlsx": "x"})
    truth = dict(TRUE, xlsx=(0.1, 1e-8))
    est, _ = run(tmp_path, machine=Machine(truth), settings=EstimateSettings(sample=25, min_per_type=1, seed=2, max_seconds=1e9))
    assert {m.ext: m.basis for m in est.models}["xlsx"] == "measured"             # one file: read in full


# ---- the census hook ---------------------------------------------------------------------------------------------------
def test_default_census_is_the_stat_pass(tmp_path):
    build(tmp_path, n=60)
    a, _ = run(tmp_path)
    m = Machine()
    b = estimate(cfg_for(tmp_path), EstimateSettings(sample=30, seed=1, max_seconds=1e9), reader=m.reader, clock=m.clock,
                 census=stat_census)
    assert (a.total.files, a.total.seconds) == (b.total.files, b.total.seconds)


def test_a_custom_census_replaces_the_walk_and_its_notes_are_reported(tmp_path):
    files = build(tmp_path, n=80)
    seen = []

    def half(plan, depth):
        seen.append(depth)
        kept = [i for i in stat_census(plan, depth).items][::2]
        return Census(kept, notes=("population is approximate",), seconds=1.5)

    m = Machine()
    est = estimate(cfg_for(tmp_path), EstimateSettings(sample=30, seed=1, max_seconds=1e9), reader=m.reader, clock=m.clock,
                   census=half)
    assert seen == [1] and est.total.files == 40 and est.walk_seconds == 1.5
    assert "population is approximate" in est.notes and "population is approximate" in format_estimate(est)
    assert est.total.files < len(files)


def test_a_census_needs_only_entries_sizes_and_groups(tmp_path):
    """The estimator never touches the disk for sizes: a population built from numbers alone is enough."""
    real = build(tmp_path, n=20)
    base = stat_census(__import__("treequery").SearchPlan.from_settings(cfg_for(tmp_path)), 0).items
    fake = [Item(i.entry, "(model)") for i in base for _ in range(5)]        # 5x the files, same entries
    m = Machine()
    est = estimate(cfg_for(tmp_path), EstimateSettings(sample=30, seed=1, max_seconds=1e9, group_depth=0), reader=m.reader,
                   clock=m.clock, census=lambda plan, depth: Census(fake))
    assert est.total.files == 5 * len(real) and [g.name for g in est.groups] == ["(model)"]


def test_census_cloud_only_count_is_carried_into_the_estimate(tmp_path):
    build(tmp_path, n=10)
    est = estimate(cfg_for(tmp_path), EstimateSettings(sample=5, seed=1, max_seconds=1e9), reader=Machine().reader,
                   clock=Machine().clock, census=lambda plan, depth: Census(stat_census(plan, depth).items, cloud_only=7))
    assert est.cloud_only_files == 7 and any("7 online-only" in n for n in est.notes)


# ---- folders, recursion, grouping --------------------------------------------------------------------------------------
def test_folder_option_limits_the_estimate_and_recursion_toggles_subfolders(tmp_path):
    make_tree(tmp_path, {"a/x.txt": "x", "a/b/y.txt": "y", "a/b/c/z.txt": "z", "other/w.txt": "w"})
    kw = dict(settings=EstimateSettings(sample=100, seed=1, max_seconds=1e9, folder=str(tmp_path / "a")))
    assert run(tmp_path, **kw)[0].total.files == 3
    kw["settings"] = EstimateSettings(sample=100, seed=1, max_seconds=1e9, folder=str(tmp_path / "a"), recursive=False)
    assert run(tmp_path, **kw)[0].total.files == 1
    assert run(tmp_path)[0].total.files == 4


def test_a_missing_folder_is_an_error_not_an_empty_estimate(tmp_path):
    with pytest.raises(NotADirectoryError):
        run(tmp_path, settings=EstimateSettings(folder=str(tmp_path / "ghost")))


@pytest.mark.parametrize("depth, labels", [(0, 1), (1, 3), (2, 4)])
def test_group_depth_controls_the_rows(tmp_path, depth, labels):
    make_tree(tmp_path, {"top.txt": "t", "a/x.txt": "x", "a/b/y.txt": "y", "c/z.txt": "z"})
    est, _ = run(tmp_path, settings=EstimateSettings(sample=100, seed=1, max_seconds=1e9, group_depth=depth))
    assert len(est.groups) == labels or (depth == 1 and len(est.groups) == 3 + 1) or (depth == 2 and len(est.groups) == 4 + 0)
    assert sum(g.files for g in est.groups) == est.total.files == 4
    assert sum(g.seconds for g in est.groups) == pytest.approx(est.total.seconds)


def test_group_rows_name_their_folders(tmp_path):
    make_tree(tmp_path, {"top.txt": "t", "a/x.txt": "x"})
    est, _ = run(tmp_path, settings=EstimateSettings(sample=100, seed=1, max_seconds=1e9, group_depth=1))
    names = [g.name for g in est.groups]
    assert any(n.endswith(os.sep + "a") for n in names) and any("files directly here" in n for n in names)


# ---- report ------------------------------------------------------------------------------------------------------------
def test_report_shows_table_models_and_caveats(tmp_path):
    build(tmp_path)
    est, _ = run(tmp_path, machine=Machine(noise=0.01))
    text = format_estimate(est)
    for fragment in ("Folder", "TOTAL", "80% range", "Speed by file type", ".pdf", "MB/s", "one at a time", "hashing"):
        assert fragment in text


@pytest.mark.parametrize("seconds, text", [(0.2, "200 ms"), (3.14, "3.1 s"), (42, "42 s"), (75, "1 min 15 s"),
                                           (3599, "59 min 59 s"), (7500, "2 h 05 min"), (-5, "0 ms")])
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text


@pytest.mark.parametrize("size, text", [(0, "0 B"), (1023, "1023 B"), (1024, "1.0 KB"), (5 * 1024**2, "5.0 MB"), (3 * 1024**4, "3072.0 GB")])
def test_format_size(size, text):
    assert format_size(size) == text


# ---- command line ------------------------------------------------------------------------------------------------------
def test_cli_estimate_prints_the_table(tmp_path, capsys):
    build(tmp_path, n=40)
    assert main(["estimate", "--roots", str(tmp_path), "--query", "text:needle", "--sample", "10", "--seed", "1",
                 "--log-level", "ERROR"]) == 0
    out = capsys.readouterr().out
    assert "TOTAL" in out and "Based on timing" in out


def test_cli_estimate_folder_needs_no_roots(tmp_path, capsys):
    make_tree(tmp_path, {"a/x.txt": "needle"})
    assert main(["estimate", "--folder", str(tmp_path / "a"), "--query", "text:needle", "--log-level", "ERROR"]) == 0
    assert "TOTAL" in capsys.readouterr().out


def test_cli_estimate_errors_exit_2(tmp_path, capsys):
    assert main(["estimate", "--log-level", "ERROR"]) == 2                                   # nothing to estimate
    assert main(["estimate", "--folder", str(tmp_path / "ghost"), "--log-level", "ERROR"]) == 2
    assert main(["estimate", "--roots", str(tmp_path), "--sample", "0", "--log-level", "ERROR"]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_estimate_flags_do_not_read_the_profile(tmp_path, monkeypatch, capsys):
    """Estimate options are command-line only; a treequery.yml in the folder must not be asked about them."""
    make_tree(tmp_path, {"d/a.txt": "needle"})
    (tmp_path / "treequery.yml").write_text(f"roots: ['{(tmp_path / 'd').as_posix()}']\nquery: 'text:needle'\n")
    monkeypatch.chdir(tmp_path)
    assert main(["estimate", "--seed", "1", "--log-level", "ERROR"]) == 0
    assert "TOTAL" in capsys.readouterr().out


def test_estimate_object_is_plain_data(tmp_path):
    build(tmp_path, n=20)
    est, _ = run(tmp_path)
    assert isinstance(est, Estimate) and isinstance(est.models[0].line, Line) and not math.isnan(est.total.seconds)
