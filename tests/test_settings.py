import argparse
from dataclasses import dataclass

import pytest

from treequery.settings import add_settings_arguments, load_config, option, resolve_settings


@dataclass(frozen=True)
class Demo:
    roots: tuple = option((), "folders")
    deep: bool = option(True, "descend")
    limit: int | None = option(None, "max")
    mode: str = option("a", "mode", choices=("a", "b"))
    table: dict = option({}, "yaml only", cli=False)


def parse(argv, config=None):
    parser = argparse.ArgumentParser()
    add_settings_arguments(parser, Demo)
    return resolve_settings(Demo, parser.parse_args(argv), config)


def test_defaults_when_nothing_given():
    assert parse([]) == Demo()


def test_yaml_overrides_default_and_cli_overrides_yaml(tmp_path):
    cfg = tmp_path / "p.yml"
    cfg.write_text("roots: [x, y]\nlimit: 3\ndeep: false\n")
    assert parse([], str(cfg)).roots == ("x", "y")           # list -> tuple
    assert parse([], str(cfg)).limit == 3
    merged = parse(["--limit", "9", "--deep"], str(cfg))
    assert (merged.limit, merged.deep, merged.roots) == (9, True, ("x", "y"))


def test_no_flag_negates_bool():
    assert parse(["--no-deep"]).deep is False


def test_optional_int_and_choices():
    assert parse(["--limit", "5"]).limit == 5
    with pytest.raises(SystemExit):
        parse(["--mode", "zzz"])


def test_yaml_only_field_has_no_flag_but_loads(tmp_path):
    cfg = tmp_path / "p.yml"
    cfg.write_text("table: {k: 1}\n")
    assert parse([], str(cfg)).table == {"k": 1}
    with pytest.raises(SystemExit):
        parse(["--table", "x"])


def test_unknown_yaml_key_is_an_error(tmp_path):
    cfg = tmp_path / "p.yml"
    cfg.write_text("bogus: 1\n")
    with pytest.raises(ValueError, match="bogus"):
        load_config(Demo, str(cfg), "treequery.yml")


def test_missing_explicit_config_raises_but_missing_default_is_fine(tmp_path, monkeypatch):
    with pytest.raises(FileNotFoundError):
        load_config(Demo, str(tmp_path / "nope.yml"), "treequery.yml")
    monkeypatch.chdir(tmp_path)
    assert load_config(Demo, None, "treequery.yml") == {}
