"""SearchPlan: the one place that decides which files are in scope and which must be read."""
import pytest

from conftest import make_tree
from test_estimate import Machine, build, cfg_for
from treequery.estimate import EstimateSettings, estimate
from treequery.plan import SearchPlan, Stage
from treequery.query import Verdict
from treequery.search import ProfileSettings, run_search
from treequery.settings import build_parser, resolve_settings
from treequery.text import TextResult, TextStatus
from treequery.walk import TreeSettings, WalkStats

TERMS = {"who": {"weight": 2, "any": ["needle"]}}


def plan_for(tmp_path, **kw):
    kw.setdefault("terms", TERMS)
    kw.setdefault("query", "who OR ext:pdf")
    return SearchPlan.from_settings(cfg_for(tmp_path, **kw), reader=lambda e: TextResult(TextStatus.OK, "needle" if e.name.startswith("hit") else ""))


@pytest.fixture
def files(tmp_path):
    make_tree(tmp_path, {"hit.txt": "", "miss.txt": "", "doc.pdf": ""})
    return tmp_path


def stage_of(plan, name):
    entry = next(e for e in plan.entries(WalkStats()) if e.name == name)
    return plan.triage(plan.view(entry))


def test_triage_never_reads_and_reports_three_stages(files):
    plan = plan_for(files)
    reads = []
    plan = SearchPlan(plan.cfg, plan.rules, plan.query, plan.groups, lambda e: reads.append(e) or TextResult(TextStatus.OK, ""))
    stages = {e.name: plan.triage(plan.view(e)) for e in plan.entries(WalkStats())}
    assert stages == {"doc.pdf": Stage.MATCHED, "hit.txt": Stage.UNDECIDED, "miss.txt": Stage.UNDECIDED} and reads == []


def test_resolve_reads_only_when_needed(files):
    plan = plan_for(files)
    entries = {e.name: e for e in plan.entries(WalkStats())}
    assert plan.resolve(plan.view(entries["hit.txt"])) is Verdict.YES
    assert plan.resolve(plan.view(entries["miss.txt"])) is Verdict.NO


def test_query_that_rejects_by_name_is_rejected_at_triage(files):
    assert stage_of(plan_for(files, query="ext:docx"), "hit.txt") is Stage.REJECTED


def test_scores_property_needs_both_the_flag_and_term_groups(files):
    assert plan_for(files, score=True).scores is True
    assert plan_for(files, score=False).scores is False
    assert plan_for(files, score=True, terms={}, query="ext:pdf").scores is False


def test_plain_tree_settings_match_everything(files):
    plan = SearchPlan.from_settings(TreeSettings(roots=(str(files),), exclude_dirs=()))
    assert {stage_of(plan, n) for n in ("hit.txt", "doc.pdf")} == {Stage.MATCHED} and plan.scores is False


def test_cloud_only_read_mode_is_passed_to_the_view(files):
    assert plan_for(files, cloud_only="read").view(next(plan_for(files).entries(WalkStats())))._read_cloud_only is True


@pytest.mark.parametrize("score", [True, False])
@pytest.mark.parametrize("query", ["who OR ext:pdf", "ext:txt AND who", "text:needle", "ext:pdf", "NOT ext:pdf"])
def test_estimate_counts_exactly_the_files_a_real_run_would_read(tmp_path, score, query):
    """The estimator and the search must agree on which files get read; both use SearchPlan."""
    build(tmp_path, n=60)
    cfg = cfg_for(tmp_path, terms=TERMS, query=query, score=score)
    dry = run_search(cfg, dry_run=True)
    expected = dry.would_read + (dry.matched_without_reading if score else 0)
    machine = Machine()
    est = estimate(cfg, EstimateSettings(sample=5, seed=1, max_seconds=1e9), reader=machine.reader, clock=machine.clock)
    assert est.total.files == expected


def test_estimate_settings_are_cli_only_and_never_load_a_profile(tmp_path, monkeypatch):
    (tmp_path / "treequery.yml").write_text("definitely_not_a_setting: 1\n")
    monkeypatch.chdir(tmp_path)
    args = build_parser(EstimateSettings).parse_args(["--sample", "7"])
    assert resolve_settings(EstimateSettings, args, None, default_name=None).sample == 7
    with pytest.raises(ValueError, match="Unknown keys"):
        resolve_settings(ProfileSettings, build_parser(ProfileSettings).parse_args([]), None)       # the default would read it
