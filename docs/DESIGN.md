# treequery design notes

Why it is built the way it is, for whoever extends it next.

## The funnel

Cost per file rises at each stage, so each stage runs only on survivors of the one before:

| Stage | Module | Cost | Decides on |
|---|---|---|---|
| 1 Prune folders | `walk.py` | none | folder names, depth, hidden |
| 2 Filter files | `walk.py` | one `stat` | extension, name pattern, size, dates, cloud flag |
| 3 Query cheap tiers | `query.py` | none / `stat` | `name path ext size modified` |
| 4 Read text | `text.py` via `readers` | open + parse | `text`, bare term groups |
| 5 Score | `terms.py` | regex over text already read | weights |

The unit of work that matters is *a file opened*. A search over 200,000 files with a good profile opens a few hundred.

## One plan, three users

`plan.SearchPlan` compiles the rules, query, term groups and reader once and answers the two questions everything else
depends on: *is this file in scope?* (`entries`) and *can the cheap tiers settle it?* (`triage`). `run_search`, `explain`
and `estimate` all call it, so they cannot disagree about which files get read; `tests/test_plan.py` proves the
estimator counts exactly what a dry run counts, over a grid of queries.

## The time estimate

**Problem:** know whether a run is minutes or days before starting it, and where the time goes.

**What is known and what is not.** Stage 2 already `stat`s every file, so the size of every file is known exactly; no size
distribution needs to be inferred. What is unknown is how long a read takes: some fixed cost per file (open, parse the
container) plus a cost per byte, and both depend on the format.

**Method.**
1. Walk once; keep the files whose text a real run would read (`Stage.UNDECIDED`, plus `MATCHED` when scoring is on).
   Online-only files are counted but never timed: timing one would download it.
2. Choose which to time: at least `min_per_type` of each extension (all of them, if fewer), the remainder shared out in
   proportion to bytes; inside a type, files are drawn with probability proportional to size (Efraimidis-Spirakis
   weighted sampling without replacement). A handful of huge files dominates the total, and this makes sure they are
   among the ones measured instead of extrapolated.
3. Time real reads, in random order so that hitting `--max-seconds` does not drop a whole type.
4. Fit `seconds = fixed + per_byte x size` per type by least squares, forced to be physical (a negative rate or intercept is
   replaced by the nearest valid line). Types read in full are *measured*, not modelled; types with fewer than two timings
   borrow the *pooled* line.
5. Predict every untimed file, sum by folder group. The range comes from a bootstrap: refit each type's line on timings
   resampled with replacement, add the leftover noise as sd x sqrt(files), repeat 300 times. All groups share each round's
   lines, so the grand-total range is not the sum of the group ranges.

**Validation.** `tests/test_estimate.py` runs the estimator against a synthetic machine with known costs: exact on a
noiseless machine from 30 timings of 200 files; unbiased (mean error < 3%) with 15% jitter over 40 seeds; the 80% range
covers the truth at least 60% of the time (about 75% in a larger simulation of 1,500 files, so it is slightly optimistic).

**The population is exact on purpose.** Sizes come from the stat pass, not from a fitted distribution. Tested on
a real folder (2,952 files, 935 MB; top 1% of files = 45% of the bytes): a sample-based estimate of the total bytes was
unbiased and within 25% in 92% of runs at 1,000 files; a pure power-law fit gave an infinite mean in 65-94% of samples
(fitted exponent 1.4-2.1 across samples); a lognormal converged to 1.5x the truth even when fitted to all files; a
power-law tail truncated at the sample maximum stayed about 1.25x high. The few largest files carry most of the bytes
and a sample rarely contains them. So the default is the exact `stat_census`. The `census=` argument
(`CensusSource` -> `Census`) is the extension point for an approximate population (sample folders, model the rest, or a
cached listing); the estimator then ranks files only by size, extension and group. An approximate census should say so in
`Census.notes`; the printed range does not include census error. A census cannot apply name, date or query filters to
files it did not list, so a modelled census suits unfiltered folders.

**Known limits.** Files are assumed read one at a time. Cache effects are not modelled: a file read during timing is
faster when read again, and a cold disk is slower than the sample suggests, so run the estimate on the same machine and
drive you will search. Hashing, CSV writing and the walk are excluded (the walk time is reported). If reading cost is not
roughly linear in size for some format (a scanned PDF with OCR would not be), the range will be wide; raise `--sample`.

## Decisions and their reasons

* **Three-valued verdicts.** A file whose text cannot be read (scanned PDF, `.doc`, online-only, corrupt) is neither a match nor a
  non-match. Returning `NO` would hide it; returning `YES` would pollute results. `MAYBE` propagates with Kleene logic and ends
  in `candidates_unread.csv` with the reason, so the user decides whether to OCR, convert or download.
* **Cheapest-first evaluation inside AND/OR.** The query is written for humans (`text:x AND ext:pdf`); the evaluator reorders by
  tier and short-circuits. Tests assert the number of reads, not just the answer.
* **Settings are declared once.** `option(default, help)` on a dataclass field generates the YAML key, the `--flag`, the `--help`
  text and the validation point. Adding a rule is one line plus its check. `copyindate2code.py` uses the same pattern, so
  its `Settings` can subclass `TreeSettings` unchanged.
* **Fail at load, not mid-walk.** `__post_init__` compiles every regex, parses every date/size and the query itself. A typo in line 40
  of a profile costs zero minutes, not the two hours before the walk reached it.
* **Refuse to search less than asked.** A missing root raises `NotADirectoryError` (exit 2); an unplugged drive never produces an
  empty-but-successful run.
* **Cloud-only files are listed, not read.** Reading a Google Drive "online-only" file downloads it. The default `list`
  reports them as unread; `read` is opt-in.
* **`prune` mirrors DOS `/s`.** Prune on: a skipped folder removes its subtree. Prune off: only that folder's own files are muted;
  its subfolders are still searched (`muted` set in `walk_tree`).
* **Words are alphanumeric runs.** `_` and `-` separate words so `Morgan_contract.pdf` matches `Morgan`; `Morgans` does not.
* **Scores are capped.** Ten thousand repeats of one word must not outrank a file that hits five different groups
  (`MAX_COUNTED_HITS`), and a name hit is worth `NAME_HIT_BONUS` text hits because a file called `RAL_contract.pdf` is
  almost always the right file.
* **Lazy `readers` import.** Walking and querying by name work on a machine without the PDF/docx stack.
* **Symlinks are not followed.** `os.walk` default; a link loop cannot hang a search.

## Known limits (deliberate, documented)

* Overlapping roots yield a file twice. List each tree once.
* `.doc`, `.xls`, `.rtf` and images have no reader; they can match on name/path only.
* No text cache: a second search with different terms re-reads. The cache key would be `(path, mtime, size)`; the seam is
  `FileView`'s reader, so adding it needs no change to the query or walker.
* No email source yet. It needs a new `FileEntry`-like source (messages inside mbox files) and fields such as `from:`; the
  extension steps are in [WORKFLOWS](WORKFLOWS.md#5-add-your-own-search-field-eg-email-from).
* Dates are naive local times, as the filesystem reports them.

## Extension points

| To add | Touch |
|---|---|
| Folder or file rule | field on `TreeSettings`, check in `TreeRules.file_verdict` or `_select_subdirs`, rows in `FileVerdict` |
| Query field | `FIELD_TIER`, one getter in `_atom_to_node` |
| File format | `UNSUPPORTED_HINTS`/`ODF_EXTENSIONS` in `text.py` or `readers.READER_REGISTRY` |
| Output format | new function beside `write_csvs`; `SearchResult` has everything |
| Scope decision shared by all commands | `SearchPlan` (`plan.py`) |
| Where the estimate's file list comes from (approximate populations) | a `CensusSource` passed as `estimate(census=...)`; default `stat_census` |
| A read-time model other than linear | `fit_line` in `estimate.py`; `TypeModel.line` is the only place it is used |
| New command | subparser in `cli.build_cli` (shares the generated flags via `parents=[common]`) |

## Bugs found by the 2026-10-15 test pass (and now covered)

An invalid `/regex/` in a query or `terms:` raised a raw `re.error`; a YAML syntax error, a top-level YAML list, an unquoted date
(`cutoff: 2013-01-01`), a bare number for a size, and a single string for `roots:` each produced a traceback or silently wrong
roots; `explain` on a missing file raised; `cloud_only: sometimes` was accepted; `EXT:pdf` was rejected. All now give a one-line
message (exit 2) or are accepted, with tests in `test_regressions_config.py` and `test_query_semantics.py`.
