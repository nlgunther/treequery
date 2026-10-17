# treequery

Find the few files you care about in a very large folder tree, without reading all of them.

treequery searches a directory tree (several drives, years of documents, a Drive sync folder) for files that match a
query such as "PDFs or Word files from 2013 onward that mention one of these contractors", and ranks the hits. Reading
the text of a file is by far the slowest step, so treequery does it **last**, and only for files that cheaper checks
could not already settle.

## How it works

A search is a funnel. Each stage is cheaper than the next and passes only its survivors on:

1. **Prune folders**: `exclude_dirs`, `shallow_dirs`/`keep_dirs`, `max_depth`, `skip_hidden`. No file is touched.
2. **Filter files** by extension, name, size and date: one `stat` call per file.
3. **Evaluate the query**: a small boolean language, cheapest predicate first, stopping as soon as the answer is decided.
4. **Read text** only if nothing cheaper decided (PDF, Word, Excel, OpenDocument, plain text).
5. **Score and rank** by weighted term hits.

Nothing disappears silently. A file that could not be judged (scanned PDF, legacy `.doc`, online-only Drive file,
corrupt file) is written to an `_unread` list with the reason, so you decide whether to OCR, convert or download it.

## Install

Requires Python 3.10+.

```
pip install -e path\to\treequery
pip install -e "path\to\readers[pdf,docx,excel]"     # optional: needed only for the text-reading stage
```

`readers` is a separate shared file-reading package and is not bundled. Without it, the folder, file-rule and
name/path/size/date stages all work; anything that needs a file's text is listed as unread.

## Quick start

1. Write a profile, `basis.yml`:

   ```yaml
   roots: ['D:\docs', 'H:\Drive']              # single-quote Windows paths
   exclude_dirs: [.git, node_modules, Windows]
   skip_hidden: true
   cutoff: 2013-01-01                          # ignore files older than this
   output: candidates.csv
   query: "ext:pdf,docx,xlsx,txt AND (contractor OR (doctype AND money))"
   terms:
     contractor: {weight: 5, any: [Morgan, "New Season"]}
     doctype:    {weight: 3, any: [invoice, contract, receipt]}
     money:      {weight: 2, regex: '\$\d{1,3}(,\d{3})*(\.\d\d)?', capture: true}
   ```

2. Check the size of the job before reading anything:

   ```
   treequery funnel -p basis.yml       # how many files survive each stage? reads no file text
   treequery estimate -p basis.yml     # how long will the text-reading take? (times a small sample)
   ```

3. Run it:

   ```
   treequery run -p basis.yml          # writes candidates.csv and candidates_unread.csv
   ```

4. If a file you expected is missing, ask why:

   ```
   treequery explain -p basis.yml -f D:\docs\a\b.pdf
   ```

`python -m treequery ...` works without installing the console script.

## Commands

| Command | What it does |
|---|---|
| `run` | Search and write `candidates.csv` and `candidates_unread.csv` |
| `funnel` | Dry run: show how much each stage narrows the search |
| `estimate` | Time a size-weighted sample of real reads; estimate the reading time per folder, with a range |
| `explain -f FILE` | Show the folder rule, file rule and each query term's yes/no/maybe for one file |

Settings come from built-in defaults, then the YAML profile, then the command line. Every setting is both a YAML key
and a flag (`exclude_dirs` becomes `--exclude-dirs`); `terms` is YAML-only. Unknown YAML keys are errors. With no `-p`,
`./treequery.yml` is used if present. Exit code 0 is success, 2 is a bad profile, bad query, missing root or usage error.

## Writing queries

```
ext:pdf,docx          name:invoice          name:"new season"       path:regatta
text:"total due"      text:/\$\d{3,}/       contractor              name:@contractor
size>1MB              size:1KB..4KB         modified>2013-05-01     modified:2014-01-01..2014-12-31
a AND b     a OR b     NOT a     (a OR b) c        # two atoms side by side mean AND
```

| Cost tier | Fields | Cost |
|---|---|---|
| 0 | `name`, `path`, `ext` | free |
| 1 | `size`, `modified` | one `stat` |
| 2 | `text`, bare term groups | reads the file |

* Matching is case-insensitive; `a,b` means a **or** b. A bare date means that whole day.
* A bare group name (from `terms:`) matches in the file name **or** the text; `name:@group` matches the name only, so it never causes a read.
* Answers are three-valued: yes, no or **maybe**. Unreadable text is *maybe*, never *no*, which is what feeds the unread list.
* `ext:txt AND text:invoice` never opens a PDF.

## Settings at a glance

| Setting | Meaning |
|---|---|
| `roots` | folders to search |
| `exclude_dirs` | folder names never entered |
| `prune` | `true` (default): a skipped folder takes its subtree with it; `false`: skip only its own files and still descend |
| `shallow_dirs` / `keep_dirs` | regexes: matching folders yield only their top-level files, unless the full path matches `keep_dirs` |
| `max_depth`, `skip_hidden` | depth limit (roots are 0); skip folders starting with `.` |
| `only_ext`, `skip_extension_pattern`, `skip_name_pattern` | extension allow-list; regexes on extension and name |
| `cutoff`, `modified_before`, `min_size`, `max_size` | date and size window (`2013-01-01`, `10KB`, `200MB`) |
| `cloud_only` | `list` (default: report, never download), `skip`, or `read` (**downloads** online-only files) |
| `query`, `terms` | the boolean query and its named word groups |
| `output`, `score`, `hash_results`, `max_text_chars`, `log_level` | reporting |

The full table with defaults is in [docs/CHEATSHEET.md](docs/CHEATSHEET.md).

## Output

`candidates.csv`: `score, path, name, ext, size, modified, text_status, hits, captured, sha256, copies`, best score first.
`captured` holds the matches of any term with `capture: true` (for example dollar amounts), and `copies > 1` flags
identical duplicates, such as a local file and its Drive copy.

`candidates_unread.csv`: `path, ext, size, modified, text_status, detail`. Work through this list after the main one.

Both files are UTF-8 with a BOM, so Excel opens them correctly.

## Scoring

Words match whole words, case-insensitively. Letters and digits are word characters; `_`, `-` and `.` separate words, so
`Morgan_contract.pdf` matches `Morgan`. A group's score is `weight x (min(text hits, 5) + 2 if the file name hits)`, and a
file's score is the sum over groups. Use `--no-score` for fast name-only passes, since scoring reads every candidate.

## Using it from Python

```python
from treequery import ProfileSettings, run_search

result = run_search(ProfileSettings(roots=("D:/docs",), query="ext:pdf AND invoice",
                                    terms={"invoice": {"any": ["invoice"]}}))
```

The walker can also be used alone to select files with the same folder and file rules (for example from the
`copyindate` scripts, whose setting names and selection it matches): see `walk_tree` and
[docs/WORKFLOWS.md](docs/WORKFLOWS.md#4-reuse-the-folder-rules-in-another-tool-eg-copyindate2codepy).

## Known limits

* Overlapping roots list a file twice. Name each tree once.
* `.doc`, `.xls`, `.rtf` and images have no reader; they can match on name/path only and are listed unread when text is needed.
* Scanned PDFs need OCR first.
* There is no text cache yet, so a second search with new terms re-reads the files.
* Dates are naive local times, as the filesystem reports them.
* `estimate` assumes one file is read at a time; run it on the same machine and drive you will search.

## Project layout

```
src/treequery/
  cli.py       command line: run | funnel | explain | estimate
  settings.py  one declaration per setting gives the YAML key, the flag and the help text
  walk.py      stages 1-2: folder pruning and file rules
  query.py     stage 3: query parser and three-valued, cost-ordered evaluator
  plan.py      SearchPlan: the single decision point for scope and "must this be read?"
  text.py      stage 4: text reading via `readers`, with explicit failure statuses
  terms.py     term groups and scoring
  search.py    run_search, explain, result types
  estimate.py  sampled time estimate
  report.py    funnel summary and CSV output
tests/         pytest suite
docs/          CHEATSHEET, API, WORKFLOWS, DESIGN, TEST_GUIDE, UV_GUIDE
```

## Development

```
pip install -e ".[dev]"
pytest -q
pytest -q --cov=treequery --cov-branch --cov-report=term-missing
```

Tests that need `readers` or the document libraries skip themselves when those are absent. To add a file format, a
query field or a folder/file rule, see "Extension points" in [docs/DESIGN.md](docs/DESIGN.md).

## Documentation

| | |
|---|---|
| [docs/CHEATSHEET.md](docs/CHEATSHEET.md) | settings table, grammar, recipes, gotchas |
| [docs/WORKFLOWS.md](docs/WORKFLOWS.md) | worked examples (run by the test suite) |
| [docs/API.md](docs/API.md) | every public function, class and exception |
| [docs/DESIGN.md](docs/DESIGN.md) | why it works this way; extension points; known limits |
| [docs/TEST_GUIDE.md](docs/TEST_GUIDE.md) | how to add tests |
| [docs/UV_GUIDE.md](docs/UV_GUIDE.md) | managing treequery and its sibling packages with uv |
| [CHANGELOG.md](CHANGELOG.md) | release history (current version 0.2.1) |
