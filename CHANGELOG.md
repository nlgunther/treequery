# Changelog

## 0.2.1: 2026-10-15
- New: `estimate(census=...)` hook. `stat_census` (the old private `_collect`) is the default; `Census`, `Item` and
  `CensusSource` are public so an approximate population (sampled folders, a cached listing) can be plugged in. No change
  to default behaviour. Tests 347 -> 351.
- Docs: UV_GUIDE section 5a: keeping treequery in `personal\nlgcode` with a path source to readers (tested).
- Docs: DESIGN records why the default population is exact (power-law, lognormal and truncated-tail experiments).

## 0.2.0: 2026-10-15
- New: `treequery estimate` / `estimate()`: time a size-weighted sample of real reads, fit a per-type cost line,
  estimate the reading time per folder with a bootstrap range; `--folder`, `--no-recursive`, `--group-depth`, `--sample`.
- Refactor: `SearchPlan` (`plan.py`) is the single decision point for scope and "must this be read?", shared by
  `run_search`, `explain` and `estimate`; `make_entry` is public; explicit `__all__`; unused imports removed.
- Settings classes can be command-line only (`default_name=None`).
- Docs: UV_GUIDE, estimate sections everywhere; tests 270 -> 347.

## 0.1.1: 2026-10-15
- Tests: 72 -> 270 (99% branch coverage): edge cases, three-valued logic tables, hypothesis properties, real-format files,
  subprocess CLI, executable documentation.
- Fixed: bad `/regex/` (query or terms) is a `ParseError`/`ValueError`, not `re.error`.
- Fixed: YAML syntax error, top-level YAML list, unquoted dates, bare numbers for sizes and a lone string for list settings.
- Fixed: `explain` on a missing file; `cloud_only` and pattern values validated at load.
- Changed: query field names are case-insensitive (`EXT:pdf`).
- Docs: CHEATSHEET, API, WORKFLOWS, DESIGN, TEST_GUIDE.

## 0.1.0: 2026-10-14
- First release: walk, query language, term scoring, CSV reports, `run|funnel|explain` CLI.
