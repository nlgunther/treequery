# treequery

Cost-ordered search of large file trees for one narrow purpose at a time.

A search is a **funnel**; each stage is cheaper than the next and only passes survivors on:

1. **Prune folders** — `exclude_dirs`, `shallow_dirs`/`keep_dirs`, `max_depth`, `skip_hidden`. No file is touched.
2. **Filter files** by extension, name, size and date — one `stat` per file.
3. **Query** — a small boolean language (below), cheapest predicate first.
4. **Read text** only if nothing cheaper decided — via the shared `readers` package.
5. **Score and rank** by weighted term hits.

Anything that could not be judged (scanned PDF, legacy `.doc`, online-only Drive file, corrupt file) goes to an
`_unread` list with the reason. Nothing disappears silently.

## Install and run

```
pip install -e path\to\treequery
pip install -e path\to\readers[pdf,docx,excel]      # the text stage

treequery funnel -p profile.yml                       # dry run: how much does each stage narrow things?
treequery run    -p profile.yml                       # writes candidates.csv and candidates_unread.csv
treequery explain -p profile.yml -f D:\docs\a\b.pdf   # why is (or isn't) this file a candidate?
treequery estimate -p profile.yml                     # how long will the text-reading take? (times a sample)
treequery run -p profile.yml --roots D:\docs --skip-hidden --no-prune --query "ext:pdf AND invoice"
```

Settings come from built-in defaults < the YAML profile < the command line. An unknown YAML key is an error.
Every setting below is both a YAML key and a flag (`exclude_dirs` -> `--exclude-dirs`); `terms` is YAML-only.

## How long will it take?

`treequery estimate` answers before you commit to a run. It finds the files whose text a real run would read, times a
**size-weighted** sample of real reads (big files are sampled far more often, because they dominate the total), fits
`seconds = fixed cost + cost per byte` for each file type, and applies that to every file, with an 80% range:

```
treequery estimate -p profile.yml                                   # one line per top-level folder, plus a total
treequery estimate -p profile.yml --folder D:\docs\2014            # just this folder and its subfolders
treequery estimate -p profile.yml --folder D:\docs\2014 --no-recursive    # its own files only
treequery estimate -p profile.yml --sample 60 --group-depth 2       # tighter, and one level deeper
```

See [docs/DESIGN.md](docs/DESIGN.md#the-time-estimate) for the method and how it was validated.

## Settings

| Setting | Meaning |
|---|---|
| `roots` | folders to search |
| `exclude_dirs` | folder names never entered (case-insensitive) |
| `prune` | **true (default):** a skipped folder takes its whole subtree with it. **false (`--no-prune`):** skip only that folder's own files and still descend into its subfolders |
| `shallow_dirs` / `keep_dirs` | regexes: shallow folders yield only their top-level files, unless the full path matches `keep_dirs` |
| `max_depth`, `skip_hidden` | depth limit (roots are 0); skip folders starting with `.` |
| `only_ext`, `skip_extension_pattern`, `skip_name_pattern` | extension allow-list; regexes on the extension and file name |
| `cutoff`, `modified_before`, `min_size`, `max_size` | date and size window (`2013-01-01`, `10KB`, `200MB`) |
| `guard_future` | skip files dated after the run started (clock-skew guard) |
| `cloud_only` | `list` (default: report, never download), `skip`, or `read` (downloads!) |
| `query`, `terms` | the boolean query and the named word groups it uses |
| `output`, `score`, `hash_results`, `max_text_chars`, `log_level` | reporting |

`exclude_dirs`, `shallow_dirs`, `keep_dirs`, `skip_extension_pattern` and `cutoff` deliberately match the setting
names in `copyindate2code.py`, and `walk_tree` selects the same files as its `copy_tree` (see
`tests/test_parity_copyindate.py`), so that script can adopt `walk_tree` without renaming anything.

## Query language

```
query := or            or  := and (OR and)*        and := unary (AND? unary)*
unary := NOT unary | ( or ) | atom
atom  := FIELD ':' VALUE | FIELD ('>' | '<' | '>=' | '<=') VALUE | GROUP
FIELD := name | path | ext | text | size | modified
VALUE := word[,word...] | "phrase" | /regex/ | @GROUP | low..high
```

* Keywords are case-insensitive; two atoms side by side mean AND.
* Strings are case-insensitive. `name:a,b` means contains a **or** b. `ext:.PDF,docx` ignores dots and case.
* `size>1MB`, `size:1KB..4KB`, `modified>2013-05-01`, `modified:2014-01-01..2014-12-31`; a bare date means that whole day.
* A bare `GROUP` (a name from `terms:`) matches in the file name or the text. `name:@GROUP`, `path:@GROUP`,
  `text:@GROUP` match that field only — so `name:@contractor` never causes a file to be read.

**Cost tiers:** 0 = `name path ext` (free), 1 = `size modified` (a `stat`), 2 = `text` and bare groups (reads the file).
`AND`/`OR` try their cheapest children first and stop at the first decisive answer, so
`ext:txt AND text:invoice` never opens a PDF. Answers are three-valued — yes / no / **maybe** — and unreadable text
is *maybe*, never *no* (and `NOT` of *maybe* is still *maybe*), which is what feeds the `_unread` list.

## Terms

```yaml
terms:
  contractor: {weight: 5, any: [Morgan, "New Season", Loomis]}
  money:      {weight: 2, regex: '\$\s?\d{1,3}(,\d{3})*(\.\d\d)?', capture: true}   # capture: keep the amounts
```

Words match whole words case-insensitively (letters/digits are word characters; `_` and `-` are separators, so
`Morgan_contract.pdf` matches `Morgan`). Score = sum over groups of `weight x (min(text hits, 5) + 2 if the name hits)`.

## Output

`candidates.csv`: `score, path, name, ext, size, modified, text_status, hits, captured, sha256, copies`
(`copies` > 1 means identical duplicates, e.g. a local and a Drive copy). `candidates_unread.csv`:
`path, ext, size, modified, text_status, detail`. Both are UTF-8 with BOM so Excel opens them correctly.

## Extending

* **New file format:** add its extension to the dispatch in `text.py` (or to `readers.READER_REGISTRY`).
* **New field** (e.g. `from:` for email): add it to `FIELD_TIER` and a getter in `query._atom_to_node`.
* **New folder or file rule:** add a field to `TreeSettings` and a check in `TreeRules.file_verdict`; the flag,
  YAML key and `--help` text appear automatically.
* **Deferred:** a text cache keyed on (path, mtime, size) so a second search with new terms skips re-reading.

## Documentation

| | |
|---|---|
| [docs/CHEATSHEET.md](docs/CHEATSHEET.md) | settings table, grammar, recipes, gotchas |
| [docs/API.md](docs/API.md) | every public function, class and exception |
| [docs/WORKFLOWS.md](docs/WORKFLOWS.md) | worked examples (run by the test suite) |
| [docs/DESIGN.md](docs/DESIGN.md) | why it works this way; extension points; known limits |
| [docs/TEST_GUIDE.md](docs/TEST_GUIDE.md) | how to add tests |
| [docs/UV_GUIDE.md](docs/UV_GUIDE.md) | managing treequery, readers, search, grove and copy with uv |

## Tests

```
pip install -e "treequery[dev]"
pytest -q                                                  # see docs/TEST_GUIDE.md for counts
pytest -q --cov=treequery --cov-branch --cov-report=term-missing
```

Tests needing `readers` or the document libraries skip themselves when those are absent.
