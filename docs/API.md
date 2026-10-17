# treequery API reference

Everything below is importable from `treequery` unless a module is named. Settings objects are frozen dataclasses;
a bad value raises `ValueError` when the object is built, never in the middle of a walk.

```python
from treequery import ProfileSettings, run_search, explain, Query, build_groups
```

---

## Settings

### `TreeSettings` (module `treequery.walk`)

Folder and file rules. Field meanings and defaults are in the [cheatsheet](CHEATSHEET.md#settings-yaml-key--flag-with-_---).

**Raises** `ValueError` for: `cloud_only` not in `skip|list|read`; an invalid regex (`Bad regex '(': ...`); an unparsable `cutoff`,
`modified_before`, `min_size` or `max_size`.

```python
cfg = TreeSettings(roots=("D:/docs",), exclude_dirs=("backup",), prune=False, cutoff="2013-01-01", max_size="200MB")
```

### `ProfileSettings(TreeSettings)`

Adds `query`, `terms`, `output`, `max_text_chars`, `score`, `hash_results`, `log_level`.
The query and terms are parsed at construction, so a typo fails at load time.

**Raises** `ParseError` (a `ValueError`) for a bad query; `ValueError` for malformed `terms`.

### `option(default, help, *, cli=True, choices=None)`

Declare a settings field with help text. `cli=False` keeps it YAML-only. One declaration produces the dataclass field, the
`--flag` and the `--help` line: add a field and all three appear.

### `resolve_settings(settings_cls, args, config_path, default_name="treequery.yml")`

Merge defaults < YAML < parsed command line into one settings object. `default_name=None` marks a settings class as
command-line only (no file is looked for; `EstimateSettings` uses this).

**Raises** `FileNotFoundError` (explicit `config_path` missing), `ValueError` (invalid YAML, a top-level list, unknown keys,
or any setting validation error). Coerces a lone string to a one-item tuple, and an unquoted YAML date or number to text.

### `build_parser(settings_cls, description="")`, `add_settings_arguments(parser, settings_cls)`, `load_config(settings_cls, path, default_name)`

The parts of `resolve_settings`, for reuse by other tools (e.g. `copyindate2code.py`).

---

## Running a search

### `run_search(cfg, *, dry_run=False, show_progress=False, reader=None) -> SearchResult`

Run the funnel. `dry_run=True` reads no file contents and reports how many it *would* read. `reader` is a
`Callable[[FileEntry], TextResult]` for tests; the default reads real files through `readers`.

**Raises** `NotADirectoryError` if a root does not exist.

```python
result = run_search(ProfileSettings(roots=("D:/docs",), query="ext:pdf AND invoice",
                                    terms={"invoice": {"any": ["invoice", "receipt"]}}))
for c in result.candidates[:5]:
    print(c.card.total, c.entry.path)
```

### `SearchResult`

| Field | Meaning |
|---|---|
| `candidates` | `list[Candidate]`, best score first, ties by path |
| `unread` | `list[Unread]`: could not be ruled out but text could not be read |
| `rejected_by_query` | files the query decided `NO` |
| `text_reads` | files actually opened (cloud-only placeholders do not count) |
| `matched_without_reading`, `would_read` | dry-run counters |
| `stats` | `WalkStats` |
| `dry_run` | echo of the argument |

### `Candidate(entry, card, text_status, sha256="")` / `Unread(entry, status, detail)`

`card` is a `ScoreCard`. `sha256` is empty for online-only files (hashing would download them) and when hashing is off.

### `explain(cfg, path, reader=None) -> list[str]`

Lines describing the folder rule, file rule, every query leaf and the final verdict for one file. A path outside every
root, or one that cannot be `stat`ed, returns a message instead of raising.

### `duplicate_counts(candidates) -> Counter`

How many candidates share each non-empty SHA-256.

---

## The plan (shared by search, explain and estimate)

### `SearchPlan.from_settings(cfg, reader=None, run_start=None) -> SearchPlan`

Compile rules, query, term groups and reader once. `cfg` may be a `ProfileSettings` or a plain `TreeSettings`
(everything matches). Methods: `entries(stats)` (files that pass the folder and file rules), `view(entry)` (a lazy
`FileView`), `triage(view) -> Stage` (cheap tiers only; never reads), `resolve(view) -> Verdict` (reads if needed), and
the property `scores` (True when name-matched files will still be read for scoring).

### `Stage`

`REJECTED` (query already false), `MATCHED` (already true), `UNDECIDED` (only the text can settle it).

---

## Estimating run time

### `estimate(cfg, settings=None, *, reader=None, clock=time.perf_counter, census=stat_census) -> Estimate`

Estimate the reading time for `cfg`'s search. `reader` and `clock` are injectable: tests use a synthetic machine with
known timings. `census` says where the list of files comes from (see below). **Raises** `NotADirectoryError` for a missing root or `settings.folder`.

```python
from treequery import ProfileSettings, EstimateSettings, estimate, format_estimate
est = estimate(ProfileSettings(roots=("D:/docs",), query="text:invoice"), EstimateSettings(sample=40, seed=1))
print(format_estimate(est))
print(est.total.seconds, est.total.low, est.total.high)
```

### `EstimateSettings`

`folder, recursive, sample, min_per_type, max_seconds, group_depth, interval, seed`; meanings in the
[cheatsheet](CHEATSHEET.md#estimate-options-command-line-only). **Raises** `ValueError` for `sample < 1`, `min_per_type < 1`,
`interval` outside (0, 1), negative `group_depth`, or `max_seconds <= 0`.

### `Estimate`

| Field | Meaning |
|---|---|
| `groups`, `total` | `GroupEstimate(name, files, size, seconds, low, high)` per folder group, and overall |
| `models` | `TypeModel(ext, files, size, timed, line, basis)` per file type, largest first |
| `walk_seconds`, `timing_seconds`, `timed_files` | what the estimate itself cost |
| `cloud_only_files` | online-only files that were not estimated |
| `notes` | caveats that apply to this particular estimate |

### The census hook: where the file list comes from

The estimator needs to know which files a real run would read, their sizes and their report group. That is the *census*.

| Name | Meaning |
|---|---|
| `stat_census(plan, depth) -> Census` | **Default.** Walks and stats every file, applying the same decisions as `run_search`. Exact. |
| `Census(items, cloud_only=0, seconds=0.0, notes=())` | `items`: list of `Item`; `cloud_only`: online-only files skipped; `seconds`: what gathering it cost (reported as the walk time); `notes`: printed with the estimate |
| `Item(entry, group)` | a `FileEntry` (needs `path`, `ext`, `size`) and its report line |
| `CensusSource` | any callable `(plan: SearchPlan, group_depth: int) -> Census` |

```python
from treequery import Census, estimate, stat_census

def every_other_file(plan, depth):          # a stand-in for "sample folders and model the rest"
    c = stat_census(plan, depth)
    return Census(c.items[::2], c.cloud_only, c.seconds, notes=("population is approximate",))

est = estimate(cfg, census=every_other_file)
```

Nothing else changes: sampling, timing, fitting and the range use only the items. A census that is *approximate* should say
so in `notes`; the reported range covers timing-model error only, not error in the census (see DESIGN, "The population
is exact on purpose").

### Building blocks (pure functions, unit-tested alone)

`fit_line(points) -> Line(fixed, per_byte, sd)`: least squares, kept physical (no negative cost). `allocate(counts,
weights, total, minimum)`: files to time per type. `weighted_sample(items, weight, k, rng)`: size-proportional sampling
without replacement. `percentile(values, q)`. `format_duration(seconds)` and `format_size(bytes)` are in `treequery.units`.

---

## Walking (stages 1 and 2)

### `make_entry(path, top) -> FileEntry`

`stat` one file into a `FileEntry`.

### `TreeRules.from_settings(cfg, run_start=None) -> TreeRules`

Compile a `TreeSettings` once. `run_start` fixes "now" for the future-date guard (tests pass it explicitly).

### `walk_tree(roots, rules, stats) -> Iterator[FileEntry]`

Yield every file that passes the folder and file rules. Folders are visited in sorted order; symbolic links are not
followed. Unreadable folders and files that vanish mid-walk are recorded in `stats.errors`, not raised.

**Raises** `NotADirectoryError` for a root that is not a directory.

### `FileEntry`

`path, top, name, ext (lower-case, no dot), size, modified (datetime), cloud_only`, and `relative_dir`.

### `WalkStats`

`dirs_seen, dirs_pruned, files_seen, verdicts (Counter of FileVerdict), errors, files_kept`.

### `FileVerdict`

`KEEP, ONLY_EXT, SKIP_EXT, SKIP_NAME, TOO_OLD, TOO_NEW, FUTURE, TOO_SMALL, TOO_BIG, CLOUD_SKIPPED`: the first rule that
rejected a file, checked in that (cheapest-first) order.

### `explain_scope(rules, top, path) -> list[str]`

Why the walker would or would not reach `path`: folder rules only.

---

## Queries (stage 3)

### `Query.parse(source, groups=None) -> Query`

Parse the query language. An empty or blank query matches everything.

**Raises** `ParseError` for every kind of mistake (syntax, unknown field or group, bad `/regex/`, bad size or date); the
message names the query.

```python
q = Query.parse("ext:pdf AND (contractor OR invoice) AND modified>2013-05-01", build_groups(terms))
verdict = q.evaluate(view)            # Verdict.YES / NO / MAYBE
q.max_tier                            # 0, 1 or 2: the most expensive tier the query can reach
q.leaves()                            # the individual predicates, for explain
```

### `Query.evaluate(view, max_tier=2) -> Verdict`

Evaluates cheapest children first and stops at the first decisive answer. With `max_tier=1`, anything that needs text is
`MAYBE`.

### `Verdict`

`YES`, `NO`, `MAYBE`; `.negate()` leaves `MAYBE` alone. Truth tables (Kleene): `NO AND x = NO`, `YES OR x = YES`.

### `FileView(entry, reader, read_cloud_only=False)`

A file plus lazily-read text. `text_result` reads once and caches; `text_loaded` says whether it has been read.
An online-only file yields `CLOUD_ONLY` without calling the reader unless `read_cloud_only` is set.

### `ParseError(ValueError)`

---

## Terms and scoring

### `build_groups(spec) -> dict[str, TermGroup]`

Compile the `terms:` mapping. Keys per group: `weight` (int, default 1), `any` (words), `regex`, `capture` (bool).
**Raises** `ValueError` naming the group for a non-mapping, unknown key, no `any`/`regex`, or invalid regex.

### `score(groups, name, text) -> ScoreCard`

`weight x (min(text hits, MAX_COUNTED_HITS=5) + NAME_HIT_BONUS=2 if the name hits)`, summed over groups.
`ScoreCard(total, hits, captured)`: `hits` maps group to text hits, `captured` has at most 5 matches per capturing group.

---

## Text (stage 4)

### `read_text(entry, max_chars=200_000) -> TextResult`

Never raises for a bad file. `TextResult(status, text, detail)`.

| `TextStatus` | Meaning | Typical follow-up |
|---|---|---|
| `OK` | text read | |
| `NOT_READ` | name/stat decided; text never needed | |
| `NO_TEXT` | readable file, no text (scanned PDF) | OCR |
| `UNSUPPORTED` | no reader (`.doc .xls .rtf`, images) | convert / OCR |
| `CLOUD_ONLY` | online-only; not downloaded | make available offline |
| `ERROR` | corrupt or unreadable | open by hand |

Raises `ImportError` with the install command if the `readers` package is missing.

---

## Reporting

### `write_csvs(result, output) -> (candidates_path, unread_path)`

UTF-8 with BOM (Excel-friendly). Columns: `CANDIDATE_COLUMNS` and `UNREAD_COLUMNS` in `treequery.report`.
`unread_path("a/b.csv") -> "a/b_unread.csv"`.

### `format_funnel(result) -> str`

The funnel in words: folders entered/pruned, files dropped per rule, query rejections, reads, errors.

---

## Command line (`treequery.cli.main(argv=None) -> int`)

Returns `0`, or `2` for configuration problems (message on stderr). Used by the `treequery` script and `python -m treequery`.
