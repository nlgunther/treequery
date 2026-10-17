"""
settings.py — one frozen dataclass drives defaults, YAML keys and CLI flags.

Precedence is built-in defaults < YAML file < command line, and an unknown YAML
key is an error, not a shrug.  Generic: any settings dataclass can use it, so
copyindate2code's own ``Settings`` could move onto these functions unchanged.

Example:
    @dataclass(frozen=True)
    class Mine:
        roots: tuple = option((), "folders to search")
        deep: bool = option(False, "descend everywhere")
    cfg = resolve_settings(Mine, build_parser(Mine).parse_args(["--deep"]), None)
"""
import argparse
import os
import types
from dataclasses import MISSING, field, fields
from typing import Any, Union, get_args, get_origin, get_type_hints

import yaml


def option(default: Any, help: str, *, cli: bool = True, choices: tuple | None = None) -> Any:
    """A dataclass field that remembers its help text; ``cli=False`` keeps it YAML-only (e.g. nested dicts)."""
    meta = {"help": help, "cli": cli, "choices": choices}
    if isinstance(default, (dict, list)):
        return field(default_factory=lambda d=default: type(d)(d), metadata=meta)
    return field(default=default, metadata=meta)


def _base_type(hint: Any) -> type:
    """int | None -> int; tuple[str, ...] -> tuple; bool -> bool."""
    if get_origin(hint) in (Union, types.UnionType):
        hint = next(a for a in get_args(hint) if a is not type(None))
    return get_origin(hint) or hint


def _default_of(f) -> Any:
    return f.default_factory() if f.default is MISSING else f.default


def add_settings_arguments(parser: argparse.ArgumentParser, settings_cls: type) -> None:
    """Add one ``--flag`` per CLI-enabled field; unset flags stay None so YAML can fill them."""
    hints = get_type_hints(settings_cls)
    for f in fields(settings_cls):
        if not f.metadata.get("cli", True):
            continue
        flag, kind = "--" + f.name.replace("_", "-"), _base_type(hints[f.name])
        text = f"{f.metadata['help']} [default: {_default_of(f)}]"
        if kind is bool:
            parser.add_argument(flag, action=argparse.BooleanOptionalAction, default=None, help=text)
        elif kind is tuple:
            parser.add_argument(flag, nargs="*", default=None, help=text)
        else:
            parser.add_argument(flag, type=kind, default=None, choices=f.metadata.get("choices"), help=text)


def build_parser(settings_cls: type, description: str = "") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description,
                                     epilog="Unset options fall back to the profile file, then built-in defaults.")
    add_settings_arguments(parser, settings_cls)
    return parser


def load_config(settings_cls: type, path: str | None, default_name: str | None) -> dict:
    """Settings from a YAML file; a missing *default* file is fine, a missing explicit one is not.
    ``default_name=None`` means this settings class is not file-configurable (command-line only)."""
    explicit = path is not None
    path = path or default_name
    if path is None:
        return {}
    if not os.path.isfile(path):
        if explicit:
            raise FileNotFoundError(f"Config file not found: {path}")
        return {}
    with open(path, encoding="utf-8") as fh:
        try:
            config = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError(f"{path}: expected 'key: value' settings at the top level, got {type(config).__name__}")
    unknown = set(config) - {f.name for f in fields(settings_cls)}
    if unknown:
        raise ValueError(f"Unknown keys in {path}: {sorted(unknown)}")
    return config


def resolve_settings(settings_cls: type, args: argparse.Namespace, config_path: str | None,
                     default_name: str | None = "treequery.yml"):
    """Merge defaults < YAML < command line into one frozen settings object."""
    values = load_config(settings_cls, config_path, default_name)
    for f in fields(settings_cls):
        cli_value = getattr(args, f.name, None)
        if cli_value is not None:
            values[f.name] = cli_value
    hints = get_type_hints(settings_cls)
    return settings_cls(**{k: _coerce(hints[k], v) for k, v in values.items()})


def _coerce(hint: Any, value: Any) -> Any:
    """YAML is loose: a lone string for a list field, an unquoted date or a bare number for a text field."""
    kind = _base_type(hint)
    if kind is tuple:
        return (value,) if isinstance(value, str) else tuple(value)
    if kind is str and value is not None and not isinstance(value, str):
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    return value
