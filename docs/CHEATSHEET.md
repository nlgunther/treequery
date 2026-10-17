# treequery cheatsheet

## Commands

```
treequery funnel  -p profile.yml          # dry run: how much does each stage narrow the search? reads nothing
treequery run     -p profile.yml          # search; write candidates.csv + candidates_unread.csv
treequery explain -p profile.yml -f FILE  # why is (or isn't) this one file a candidate?
treequery estimate -p profile.yml         # time a size-weighted sample; estimate the reading time (reads ~30 files)
python -m treequery ...                   # same thing without installing the script
```

Precedence: **built-in defaults < YAML profile < command line**. With no `-p`, `./treequery.yml` is used if it exists.
Exit codes: `0` ok, `2` bad profile / bad query / missing root / usage (one-line message on stderr, never a traceback).

## Estimate options (command line only)

| Flag | Default | Meaning |
|---|---|---|
| `--folder PATH` | all roots | estimate only this folder (needs no `roots`) |
| `--recursive / --no-recursive` | recursive | include subfolders of `--folder`, or its own files only |
| `--sample N` | 30 | files to time; more is slower and tighter |
| `--min-per-type N` | 3 | time at least this many of each file type (all of them, if fewer) |
| `--max-seconds S` | 60 | stop timing after S seconds whatever `--sample` says |
| `--group-depth N` | 1 | one row per folder N levels below each root; 0 = one total |
| `--interval P` | 0.8 | width of the range shown (0.8 = 10th to 90th percentile) |
| `--seed N` | random | repeatable sample |

Reading the output: **Estimated** is the point estimate for the text-reading of the files a run would read (name-only
matches are excluded unless `score` is on, because scoring reads them). **Range** is a bootstrap interval; in simulation
it holds the truth about 75% of the time when 80% is asked, so treat it as "about". `[measured]` types were read in full,
`[fitted]` types come from the sample, `[pooled]` types had too few timings and borrowed the average.
It excludes the folder listing (shown separately), hashing and writing the CSVs, and assumes one file at a time.

## Settings (YAML key = flag with `_` -> `-`)

| Setting | Default | Meaning |
|---|---|---|
| `roots` | `()` | folders to search |
| `exclude_dirs` | `.git __pycache__ node_modules ...` | folder names never entered (case-insensitive, whole name) |
| `prune` | `true` | skipped folder takes its subtree with it (`--no-prune`: skip only its own files, still descend) |
| `shallow_dirs` | `()` | regexes on folder *name*: only top-level files of that folder |
| `keep_dirs` | `()` | regexes on full path: overrides `shallow_dirs` |
| `max_depth` | none | roots are depth 0; files at that depth are still searched |
| `skip_hidden` | `false` | skip folders starting with `.` (never the root itself) |
| `only_ext` | `()` | allow-list, no dot: `[pdf, docx]` |
| `skip_extension_pattern` | `""` | regex tested against the **extension only** (e.g. `.eml`) |
| `skip_name_pattern` | `""` | regex tested against the file **name** |
| `cutoff` / `modified_before` | `""` | ISO dates: skip older / newer files |
| `min_size` / `max_size` | `""` | `300`, `10KB`, `1.5MB`, `2GB` |
| `guard_future` | `true` | skip files dated after the run started |
| `cloud_only` | `list` | `list` = report unread, `skip`, `read` (**downloads** online-only files) |
| `query` | `""` | boolean query; empty = every file that passes the rules |
| `terms` | `{}` | named word groups (YAML only) |
| `output` | `candidates.csv` | candidates; unreadable go to `candidates_unread.csv` |
| `score`, `hash_results`, `max_text_chars`, `log_level` | `true`, `true`, `200000`, `INFO` | reporting |

YAML is forgiving: `roots: C:\docs` (one string) is a one-item list; unquoted `cutoff: 2013-01-01` and `min_size: 5000` work.
Use single quotes for Windows paths (`'C:\docs'`) so backslashes survive.

## Query grammar

```
ext:pdf,docx          name:invoice        name:"new season"      name:/^img_\d+/
path:regatta          text:"total due"    text:/\$\d{3,}/        contractor        (bare group: name OR text)
name:@contractor      (group, file name only; never reads the file)
size>1MB   size:1KB..4KB   size<=500      modified>2013-05-01   modified:2014-01-01..2014-12-31
a AND b   a OR b   NOT a   (a OR b) c     # two atoms side by side = AND; AND binds tighter than OR
```

| Tier | Fields | Cost |
|---|---|---|
| 0 | `name path ext` | free |
| 1 | `size modified` | one `stat` |
| 2 | `text`, bare group | **reads the file** |

* Strings are case-insensitive substrings; `a,b` = a **or** b. Field names and `AND/OR/NOT` are case-insensitive; group names are not.
* A bare date means the whole day. `modified>2014-06-01` is strictly after midnight at the start of that day.
* Unreadable text is **maybe**, never **no**: that file lands in `_unread.csv`, it does not silently vanish.

## Terms

```yaml
terms:
  contractor: {weight: 5, any: [Morgan, "New Season"]}              # whole words, case-insensitive
  money:      {weight: 2, regex: '\$\d{1,3}(,\d{3})*(\.\d\d)?', capture: true}   # capture: list the matches in the CSV
```

Score per group = `weight x (min(text hits, 5) + 2 if the file name hits)`; total = sum over groups.
Letters/digits are word characters; `_`, `-`, `.` separate words (`Morgan_contract.pdf` matches `Morgan`).

## Common recipes

| Goal | Command |
|---|---|
| How big is this job? | `treequery funnel -p p.yml` |
| Only PDFs and Word, no hidden folders | `treequery run -p p.yml --only-ext pdf docx --skip-hidden` |
| Search a different drive with the same profile | `treequery run -p p.yml --roots H:\ --output h_drive.csv` |
| Skip `Downloads` subfolders but not Downloads' own files | `--shallow-dirs "^downloads$"` |
| Descend into skipped folders but ignore their own files | `--exclude-dirs Backup --no-prune` |
| Fast name-only pass | `--query "name:@contractor OR ext:pdf AND name:contract"` + `--no-score` |

## Gotchas

| Symptom | Cause / fix |
|---|---|
| `treequery: Not a directory: X` | root missing or drive unplugged; the run refuses to search less than you asked |
| A file you expected is missing | `treequery explain -p p.yml -f FILE`: shows the folder rule, file rule and each query leaf |
| Everything is in `_unread.csv` | `readers` not installed with extras (`pip install -e readers[pdf,docx,excel]`) or scanned PDFs (need OCR) |
| `.doc`/`.xls`/images never match on text | no reader; they match on **name/path** only and are listed unread when text was needed |
| Same file twice | overlapping roots (a root inside another root); list each tree once. Identical copies in different folders share a `sha256` and show `copies > 1` |
| Online-only Drive files not searched | by design (`cloud_only: list`); `read` downloads them |
| `--score` slow | scoring reads the text of every candidate; use `--no-score` for name-only passes |
| A root named like a `shallow_dirs` pattern lists only its top level | the root is tested too; rename the pattern or use `keep_dirs` |
| Windows backslashes in YAML | single-quote the path |
