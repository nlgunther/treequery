# treequery: test suite reference

Quick reference for adding tests. Update this file when a test file is added or a pattern changes.

## Layout

```
tests/
├── conftest.py                  # make_tree, set_mtime, names, `tree` fixture
├── test_walk.py                 # folder/file rules: the core cases
├── test_walk_edges.py           # prune x exclude matrix, depth, symlinks, permissions, vanishing files, explain_scope
├── test_query.py                # grammar and basic evaluation (also exports GROUPS, entry, CountingReader)
├── test_query_semantics.py      # Kleene truth tables, cost ordering, field table, hypothesis properties
├── test_terms.py                # term groups and scoring basics
├── test_report_and_scoring.py   # score arithmetic, CSV format (BOM, quoting), funnel text, degraded environments
├── test_settings.py             # defaults < YAML < CLI, generated flags
├── test_regressions_config.py   # every bad-input path: message, exit code 2, no traceback
├── test_plan.py                 # SearchPlan stages; estimate and dry run agree on which files are read
├── test_estimate.py             # estimator: statistics, synthetic machine, folders, CLI
├── test_search.py               # run_search with an injected reader, dry run, explain
├── test_text.py                 # text stage with the readers package
├── test_formats_real.py         # real PDF/docx/xlsx/odt/ods files end to end
├── test_cli.py                  # main() in-process
├── test_cli_process.py          # `python -m treequery` as a subprocess
├── test_parity_copyindate.py    # walk_tree selects what copyindate2code.copy_tree selects
└── test_docs_examples.py        # runs every block in docs/WORKFLOWS.md
```

**Framework:** pytest (+ hypothesis, pytest-cov). **Config:** `pyproject.toml` -> `[tool.pytest.ini_options]`
`pythonpath = ["src"]`, `testpaths = ["tests"]`. Tests that need the `readers` package (or reportlab/python-docx/openpyxl/odfpy)
call `pytest.importorskip` and skip cleanly; without them 302 tests run (6 skipped), with them 351 (1 skipped, POSIX-permissions test when run as root or on Windows).

Markers: `slow_statistics` (simulation over many seeds; ~2 s). Deselect with `-m "not slow_statistics"`.

Run: `pytest -q`, with coverage: `pytest -q --cov=treequery --cov-branch --cov-report=term-missing` (currently 99%).

## Fixture patterns

No disk needed: build `FileEntry` objects and an injected reader.

```python
from test_query import entry, CountingReader
from treequery.query import FileView, Query

reader = CountingReader("invoice total", status=TextStatus.OK)      # counts calls
view = FileView(entry("Morgan_contract.pdf", size=2048), reader)
assert Query.parse("ext:pdf AND text:invoice", GROUPS).evaluate(view) is Verdict.YES
assert reader.calls == 1                                             # cost ordering is observable
```

Real folders: `tmp_path` plus `make_tree`.

```python
from conftest import make_tree, names, set_mtime
make_tree(tmp_path, {"a/one.txt": "x", "a/skip/two.txt": "y"})
found, stats = walk(tmp_path, exclude_dirs=("skip",))               # walk() is in test_walk.py
assert names(found) == {"a/one.txt"}
```

The shared `tree` fixture (conftest) is a nine-file tree with excluded, hidden, shallow and nested folders.

Whole search without real readers: pass `reader=` to `run_search`.

```python
def fake_reader(texts):                       # see test_search.py
    reads = []
    def read(entry):
        reads.append(entry.name)
        return texts.get(entry.name, TextResult(TextStatus.OK, ""))
    read.reads = reads
    return read
```

Real files of every format: the `make_pdf/make_docx/make_xlsx/make_odt/make_ods/make_txt` helpers in `test_formats_real.py`.

Parametrised matrices (copy the shape): `test_prune_matrix` (prune x expected set), `test_field_predicates`
(query -> verdict, `None` meaning ParseError), `test_and_or_truth_tables` (all 9 combinations through real queries),
`test_cli_reports_bad_profiles_with_exit_code_2` (YAML text -> message fragment).

Properties (hypothesis): `expressions()` in `test_query_semantics.py` generates valid nested queries; use it to check any
new algebraic law (double negation, De Morgan, commutativity, cheap-never-contradicts-full).

Subprocess: `run(*args, cwd=)` in `test_cli_process.py` sets `PYTHONPATH` to `src` so no install is needed.

A synthetic machine for time estimates: `Machine` in `test_estimate.py` is a fake clock that advances by a known
`fixed + per_byte x size` (optionally with multiplicative jitter) each time its `reader` is called; `build(tmp_path)` makes
sparse files with heavy-tailed sizes. Pass both to `estimate(..., reader=machine.reader, clock=machine.clock)` and compare
with `true_seconds(files)`.

Monkeypatching the filesystem layer: patch `treequery.walk._make_entry` (see `test_cloud_only_modes`,
`test_file_vanishing_mid_walk_is_recorded`) to simulate cloud-only or vanishing files on any OS.

## Inventory

| File | Tests | What it covers |
|---|---|---|
| `test_walk.py` | 16 | core exclude/shallow/keep/depth/hidden/file-rule behaviour |
| `test_walk_edges.py` | 36 | prune matrix, depth boundaries, case rules, symlink loop, locked folder, vanishing file, every `FileVerdict`, cloud modes, invalid settings, `explain_scope` |
| `test_query.py` | 32 | tokenizer, grammar, group references, text tiering |
| `test_query_semantics.py` | 63 | truth tables, read-count table, all field forms, error messages, 6 property tests |
| `test_terms.py` | 7 | group building, word boundaries |
| `test_report_and_scoring.py` | 28 | score arithmetic and caps, malformed groups, CSV round trip, funnel text |
| `test_settings.py` | 7 | option metadata, flag generation, precedence |
| `test_regressions_config.py` | 23 | bad regex/date/size/YAML/choice -> one-line error, exit 2; YAML coercions |
| `test_plan.py` | 17 | triage never reads; `scores`; estimator counts == dry-run counts over 10 query/score combos; CLI-only settings |
| `test_estimate.py` | 64 | `fit_line`, `allocate`, `weighted_sample`, `percentile`; exact on a noiseless machine; size bias; reproducibility; unbiasedness over seeds; scope (name-only, scoring, file rules, cloud-only); folder/recursive/group depth; truncation; the census hook (default parity, custom census, notes, cloud-only); CLI |
| `test_search.py` | 9 | funnel with injected reader, dry run, unread list, explain |
| `test_text.py` | 7 | readers integration, statuses |
| `test_formats_real.py` | 25 | real files in six formats, corrupt files, unicode, full mixed-tree search, duplicates, CLI e2e |
| `test_cli.py` | 8 | `main()` in-process |
| `test_cli_process.py` | 8 | help text, usage errors, flags-only run, default profile, funnel writes nothing, explain |
| `test_parity_copyindate.py` | 1 | same selection as `copyindate2code.copy_tree` |
| `test_docs_examples.py` | 1 | executes the documentation's examples |

## Conventions

- **File names:** `test_<area>.py`; bug regressions go in `test_regressions_*.py` with the symptom in the test name.
- **Structure:** flat functions, grouped by `# ---- section ----` comments; no test classes.
- **Names:** `test_<subject>_<expected outcome>`, as a sentence a failure message can use.
- **Assertions:** plain `assert`; sets for file collections (`names(found) == {...}`), never list order unless ordering is the subject.
- **Exceptions:** `pytest.raises(Type, match=re.escape("fragment"))`; always `match` the message, since the message is the user interface.
- **No real clock or network:** pass `run_start`/`guard_future=False`, set mtimes with `set_mtime`; use `tmp_path` only.
- **Platform:** POSIX-only tests carry `skipif(sys.platform == "win32")`; the suite must also pass on Windows.
- **Parametrize** when two or more cases differ only by data; write separate tests when the assertion logic differs.
- **Every user-facing error path needs a test** that checks the message and exit code (`test_regressions_config.py`).
- **Every new setting** needs: a default test, a YAML test, a CLI-flag test, and an invalid-value test.
- **Every new query field** needs a row in the field table, the read-count table, and a `leaves()`/explain check.

## Common imports

```python
import pytest
from conftest import make_tree, names, set_mtime
from test_query import GROUPS, CountingReader, entry
from treequery.cli import main
from treequery.query import FileView, ParseError, Query, Verdict
from treequery.search import ProfileSettings, run_search, explain
from treequery.terms import build_groups, score
from treequery.text import TextResult, TextStatus
from treequery.walk import TreeRules, TreeSettings, WalkStats, walk_tree
```

*Last updated: 2026-10-15: added estimator and plan tests (suite now 351 with readers, 302 without; census hook tests; 72 at first release).*
