"""Regression tests for bad user input: every mistake must give a one-line message and exit code 2, never a traceback."""
import pytest

from treequery.cli import main
from treequery.query import ParseError, Query
from treequery.search import ProfileSettings
from treequery.settings import build_parser, load_config, resolve_settings
from treequery.terms import build_groups
from treequery.walk import TreeSettings


def profile(tmp_path, text):
    path = tmp_path / "p.yml"
    path.write_text(text, encoding="utf-8")
    return str(path)


# ---- bad regexes surface as ParseError / ValueError, not re.error ------------------------------
@pytest.mark.parametrize("query", ["name:/(/", "text:/[a-/", "NOT name:/*/"])
def test_bad_regex_in_query_is_a_parse_error(query):
    with pytest.raises(ParseError, match="in query"):
        Query.parse(query)


def test_bad_regex_in_terms_names_the_group():
    with pytest.raises(ValueError, match="terms.money: bad regex"):
        build_groups({"money": {"regex": "("}})


@pytest.mark.parametrize("query", ["size>abc", "modified>yesterday", "size:5MB..x"])
def test_bad_size_or_date_is_a_parse_error_naming_the_query(query):
    with pytest.raises(ParseError, match=query.split(">")[0].split(":")[0]):
        Query.parse(query)


# ---- YAML looseness ---------------------------------------------------------------------------
def test_unquoted_yaml_date_and_bare_number_are_accepted(tmp_path):
    p = profile(tmp_path, f"roots: [{tmp_path.as_posix()}]\ncutoff: 2013-01-01\nmin_size: 5000\n")
    args = build_parser(ProfileSettings).parse_args([])
    cfg = resolve_settings(ProfileSettings, args, p)
    assert cfg.cutoff == "2013-01-01" and cfg.min_size == "5000"


def test_a_lone_string_for_a_list_setting_is_one_item_not_characters(tmp_path):
    p = profile(tmp_path, f"roots: {tmp_path.as_posix()}\n")
    cfg = resolve_settings(ProfileSettings, build_parser(ProfileSettings).parse_args([]), p)
    assert cfg.roots == (tmp_path.as_posix(),)


def test_yaml_syntax_error_is_a_value_error(tmp_path):
    with pytest.raises(ValueError, match="not valid YAML"):
        load_config(TreeSettings, profile(tmp_path, "a: ["), "x.yml")


def test_yaml_top_level_list_is_rejected_clearly(tmp_path):
    with pytest.raises(ValueError, match="top level"):
        load_config(TreeSettings, profile(tmp_path, "- a\n- b\n"), "x.yml")


def test_empty_yaml_file_means_all_defaults(tmp_path):
    assert load_config(TreeSettings, profile(tmp_path, ""), "x.yml") == {}


@pytest.mark.parametrize("text, message", [
    ("a: [", "not valid YAML"),
    ("rots: [x]\n", "Unknown keys"),
    ("query: 'name:/(/'\n", "in query"),
    ("terms: {x: {weigth: 1, any: [a]}}\n", "unknown keys"),
    ("cutoff: not-a-date\n", "Not an ISO date"),
    ("min_size: 5 parsecs\n", "Not a size"),
    ("cloud_only: sometimes\n", "cloud_only must be"),
    ("skip_name_pattern: '('\n", "Bad regex"),
])
def test_cli_reports_bad_profiles_with_exit_code_2(tmp_path, capsys, text, message):
    code = main(["funnel", "-p", profile(tmp_path, f"roots: [{tmp_path.as_posix()}]\n{text}"), "--log-level", "ERROR"])
    err = capsys.readouterr().err
    assert code == 2 and "treequery:" in err and message in err and "Traceback" not in err


def test_missing_explicit_profile_is_exit_2(tmp_path, capsys):
    assert main(["run", "-p", str(tmp_path / "nope.yml")]) == 2
    assert "Config file not found" in capsys.readouterr().err


def test_explain_of_a_missing_file_does_not_traceback(tmp_path, capsys):
    p = profile(tmp_path, f"roots: [{tmp_path.as_posix()}]\n")
    assert main(["explain", "-p", p, "-f", str(tmp_path / "ghost.txt"), "--log-level", "ERROR"]) == 0
    assert "cannot stat the file" in capsys.readouterr().out


def test_cli_flag_overrides_yaml_which_overrides_default(tmp_path):
    p = profile(tmp_path, f"roots: [{tmp_path.as_posix()}]\nmax_depth: 3\nskip_hidden: true\n")
    parser = build_parser(ProfileSettings)
    cfg = resolve_settings(ProfileSettings, parser.parse_args(["--max-depth", "1", "--no-skip-hidden"]), p)
    assert (cfg.max_depth, cfg.skip_hidden, cfg.prune) == (1, False, True)     # CLI, CLI, built-in default
    cfg = resolve_settings(ProfileSettings, parser.parse_args([]), p)
    assert (cfg.max_depth, cfg.skip_hidden) == (3, True)                       # YAML
