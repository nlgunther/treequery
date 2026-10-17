# treequery workflows

Each block is run by `tests/test_docs_examples.py` against a small demo tree, so they stay true.

## 1. Find every document that supports a number (the Regatta basis search)

**Goal:** list contracts, invoices and payment records mentioning your contractors, across several drives, skipping junk.

```yaml
# basis.yml
roots: ['demo/DavisCA', 'demo/Drive']
exclude_dirs: [.git, node_modules, Windows]
skip_hidden: true
cutoff: 2013-01-01
output: demo/candidates.csv
query: "ext:pdf,docx,xlsx,odt,ods,txt AND (contractor OR (doctype AND money))"
terms:
  contractor: {weight: 5, any: [Raynal, "RAL Builders", Moreno]}
  doctype:    {weight: 3, any: [invoice, contract, receipt]}
  money:      {weight: 2, regex: '\$\d{1,3}(,\d{3})*(\.\d\d)?', capture: true}
```

```python
import csv
from treequery.cli import main

assert main(["funnel", "-p", "basis.yml", "--log-level", "ERROR"]) == 0     # how much work is it?
assert main(["run", "-p", "basis.yml", "--log-level", "ERROR"]) == 0        # write the CSVs
rows = list(csv.DictReader(open("demo/candidates.csv", encoding="utf-8-sig")))
print([r["name"] for r in rows])        # best score first
```

Open `candidates.csv` in Excel. `captured` shows the dollar amounts found; `copies > 1` flags identical duplicates (a local and a Drive copy).
Then work `candidates_unread.csv`: scanned PDFs and `.doc` files that could not be judged.

## 2. Narrow a search that is too big (funnel, then tighten)

**Goal:** a first run would read 40,000 files; get that down before reading any.

```python
from treequery import ProfileSettings, run_search

broad = ProfileSettings(roots=("demo/DavisCA",), query="text:invoice", exclude_dirs=())
print(run_search(broad, dry_run=True).would_read, "files would be read")

tight = ProfileSettings(roots=("demo/DavisCA",), exclude_dirs=("Photos",), only_ext=("pdf", "txt"),
                        cutoff="2013-01-01", max_size="20MB", query="ext:pdf,txt AND name:invoice OR text:invoice")
result = run_search(tight, dry_run=True)
print(result.would_read, "files would be read;", result.matched_without_reading, "match by name alone")
```

Each added rule costs nothing to apply (folder rules touch no files; file rules are one `stat`). Use `--no-prune`
(`prune=False`) when a skipped folder's own files are noise but its subfolders matter.

## 3. Find out why one file is (not) found

```python
from treequery import ProfileSettings, explain

cfg = ProfileSettings(roots=("demo/DavisCA",), query="contractor", terms={"contractor": {"any": ["Raynal"]}},
                      exclude_dirs=("Photos",))
print("\n".join(explain(cfg, "demo/DavisCA/Regatta/RAL_contract.txt")))
```

Output lines name the folder rule, the file rule, each query term with its yes/no/maybe, and the final verdict.

## 4. Reuse the folder rules in another tool (e.g. `copyindate2code.py`)

**Goal:** select files with the same exclude/shallow/keep/cutoff rules, then do something else with them.

```python
from datetime import datetime
from treequery.walk import TreeRules, TreeSettings, WalkStats, walk_tree

cfg = TreeSettings(roots=("demo/DavisCA",), exclude_dirs=("Photos",), skip_extension_pattern=r"\.eml$", cutoff="2013-01-01")
rules = TreeRules.from_settings(cfg, run_start=datetime.now())
stats = WalkStats()
chosen = [e.path for e in walk_tree(cfg.roots, rules, stats)]
print(len(chosen), "files;", stats.dirs_pruned, "folders pruned")
```

Setting names match `copyindate2code.Settings`; `tests/test_parity_copyindate.py` proves the selections agree.

## 5. Add your own search field (e.g. email `from:`)

1. Add the field to `FIELD_TIER` in `query.py` with its cost tier.
2. Add its getter in `_atom_to_node` (one line in the `getter` dict).
3. Add tests in `tests/test_query_semantics.py` (cost-ordering table and field table).

No other module changes: the parser, evaluator, `explain` output and short-circuiting already handle the new tier.

## 6. How long will this take? (and which folder is the expensive one)

**Goal:** before running, know the time to read the files the query cannot settle by name, per folder, with a range.

```python
from treequery import EstimateSettings, ProfileSettings, estimate, format_estimate

cfg = ProfileSettings(roots=("demo/DavisCA",), exclude_dirs=("Photos",), query="text:invoice")
est = estimate(cfg, EstimateSettings(sample=20, seed=1))
print("files to read:", est.total.files, "- estimated seconds:", round(est.total.seconds, 3))
print(format_estimate(est).splitlines()[0])

one = estimate(cfg, EstimateSettings(folder="demo/DavisCA/Regatta", recursive=False, seed=1))
print("Regatta's own files:", one.total.files)
```

From the command line: `treequery estimate -p basis.yml --folder D:\docs\2014 --no-recursive`. Run it on the same machine and
drive you will search (caches and disk speed are part of the answer). If one folder dominates, tighten the profile for it
(`only_ext`, `max_size`, `shallow_dirs`) and estimate again; each estimate takes seconds because only the sample is read.
