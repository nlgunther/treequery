"""
cli.py — ``treequery run|funnel|explain``.

Examples:
    treequery run -p regatta_basis.yml                       # everything from the profile
    treequery run -p regatta_basis.yml --roots D:\\docs --skip-hidden --no-prune
    treequery funnel -p regatta_basis.yml                    # dry run: how many files would be read?
    treequery explain -p regatta_basis.yml -f D:\\docs\\a\\b.pdf  # why is (or isn't) this file a candidate?
    treequery estimate -p regatta_basis.yml                  # how long will the text-reading take? (times a sample)
    treequery estimate -p regatta_basis.yml --folder D:\\docs\\2014 --no-recursive
"""
import argparse
import sys

from loguru import logger

from .estimate import EstimateSettings, estimate, format_estimate
from .query import ParseError
from .report import format_funnel, unread_path, write_csvs
from .search import ProfileSettings, explain, run_search
from .settings import add_settings_arguments, resolve_settings


def build_cli() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-p", "--profile", help="YAML profile (default: ./treequery.yml if present)")
    add_settings_arguments(common, ProfileSettings)
    parser = argparse.ArgumentParser(prog="treequery", description="Cost-ordered search of large file trees.",
                                     epilog="Unset options fall back to the profile, then built-in defaults.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", parents=[common], help="search and write the candidate CSV files")
    commands.add_parser("funnel", parents=[common], help="dry run: show how much each stage narrows the search")
    estimator = commands.add_parser("estimate", parents=[common], help="time a size-weighted sample and estimate the run time")
    add_settings_arguments(estimator, EstimateSettings)
    explainer = commands.add_parser("explain", parents=[common], help="explain the verdict for one file")
    explainer.add_argument("-f", "--file", required=True, help="the file to explain")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_cli().parse_args(argv)
    try:
        cfg = resolve_settings(ProfileSettings, args, args.profile)
        timing = resolve_settings(EstimateSettings, args, None, default_name=None) if args.command == "estimate" else None
    except (FileNotFoundError, ValueError, ParseError) as exc:
        print(f"treequery: {exc}", file=sys.stderr)
        return 2
    logger.remove()
    logger.add(lambda message: sys.stderr.write(message), level=cfg.log_level.upper())   # look up stderr at write time
    if not cfg.roots and not (timing and timing.folder):
        print("treequery: no roots given (use --roots or put 'roots:' in the profile)", file=sys.stderr)
        return 2
    if args.command == "estimate":
        try:
            print(format_estimate(estimate(cfg, timing)))
        except NotADirectoryError as exc:
            print(f"treequery: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.command == "explain":
        print("\n".join(explain(cfg, args.file)))
        return 0
    try:
        result = run_search(cfg, dry_run=args.command == "funnel", show_progress=sys.stderr.isatty())
    except NotADirectoryError as exc:     # an unplugged drive or a typo: say so, don't search less than asked
        print(f"treequery: {exc}", file=sys.stderr)
        return 2
    print(format_funnel(result))
    if args.command == "run":
        written = write_csvs(result, cfg.output)
        print(f"Wrote {written[0]} and {unread_path(cfg.output)}")
    return 0
