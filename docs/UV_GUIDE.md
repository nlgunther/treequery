# Using uv for treequery, readers, search (docsearch), grove and copy

This guide moves your five code bases from conda environments to [uv](https://docs.astral.sh/uv/).
Every command below was run against a scratch copy of your layout (four packages in one workspace,
Python 3.13, Linux). Windows-specific advice is marked **Windows** and comes from uv's documented
behaviour rather than from a run on your machine; check those lines once on desktop-p68uf9g.

## 1. The short version

- **One workspace** holds the four packages that are real projects: `readers`, `docsearch` (folder `search`), `grove`, `treequery`.
- **One lockfile** (`uv.lock`) pins every package in all four, so they are always installed at versions that work together.
- **One virtual environment** (`.venv`), created automatically. You never run `activate`; you run `uv run ...`.
- **Command-line tools** (`treequery`, `manifest`, `schd`, `docsearch`) are installed once with `uv tool install --editable`, so they work from any folder with no environment active.
- **`copy`** is a single script, so it uses **inline script metadata** (PEP 723) instead of a project.

## 2. Install uv and Python (once)

**Windows**

```powershell
winget install --id=astral-sh.uv -e        # or:  scoop install uv
uv --version
uv python install 3.13                     # uv manages its own Pythons; conda's is not used
uv tool update-shell                       # puts uv's tool folder on PATH, then reopen the terminal
```

Set these once (System > Environment Variables, or `setx`):

| Variable | Value | Why |
|---|---|---|
| `UV_LINK_MODE` | `copy` | Your code is on Google Drive / a different drive from uv's cache; hardlinks fail or warn there. |
| `UV_PROJECT_ENVIRONMENT` | e.g. `C:\venvs\nlgcode` | Keeps `.venv` out of any synced folder (Drive would try to upload thousands of files). Optional but recommended for `G:` paths. |

Your code lives under `C:\Users\nlgun\nlgcode`, which is not synced, so a plain `.venv` there is fine. Use
`UV_PROJECT_ENVIRONMENT` only for projects under `G:\My Drive`.

## 3. Take stock of what conda has now

Before removing anything, record what each environment really needs:

```powershell
conda env list
conda activate myenv
conda env export --from-history > myenv_requested.yml     # only what you asked for, not the whole closure
pip freeze > myenv_frozen.txt                             # exact pip-installed versions
```

Compare with what the packages declare. In your code the declared dependencies are small:

| Package | Declared dependencies |
|---|---|
| readers | none (extras: `pdf`, `docx`, `excel`, `full`) |
| docsearch | `tqdm`, `readers` |
| grove | `lxml`, `py7zr`, `pyyaml` (**and pandas, undeclared**, see section 8) |
| treequery | `pyyaml`; optional readers |
| copyindate2code.py | `pyyaml`, `python-dateutil`, `loguru`, `odfpy` |

Anything in the conda export beyond these is either a dev tool (pytest, a notebook) or a leftover.
Conda-only packages with no wheels (GDAL-style binary stacks, some CUDA builds) are the only real
migration risk; none of the five packages needs one.

## 4. Conda to uv: the command map

| You used to type | Now |
|---|---|
| `conda create -n x python=3.13` | `uv venv --python 3.13` (or let `uv run` do it) |
| `conda activate x` | nothing; use `uv run ...`. (If you must: `.venv\Scripts\activate`.) |
| `conda install foo` / `pip install foo` | `uv add foo` (project) or `uv pip install foo` (ad hoc) |
| `conda env export` | `uv.lock` (automatic), `uv export --no-hashes` for a requirements file |
| `conda env create -f environment.yml` | `uv sync` |
| `pip install -e .` | automatic: workspace members are installed editable by `uv sync` |
| `pipx install tool` | `uv tool install tool` |
| `python script.py` | `uv run script.py` |
| `conda list` | `uv pip list` or `uv tree` |
| `conda env remove -n x` | delete the `.venv` folder; `uv cache clean` for the cache |

## 5. The workspace layout

Your real folders are `C:\Users\nlgun\nlgcode\{grove,search,readers}`, and `C:\Users\nlgun\personal\nlgcode\{copy,treequery}`.
A uv workspace can only contain projects *inside* its root folder, so there are two ways to handle treequery living in
`personal\nlgcode`:

- **Option A (one workspace, one lockfile).** Move `treequery` beside the others
  (`C:\Users\nlgun\nlgcode\treequery`) and use the layout below. treequery has no hard-coded paths, so no code changes.
- **Option B (leave it where it is).** Keep treequery as its own project and point it at your local readers with a path
  source (section 5a). Nothing moves; treequery gets its own `uv.lock`.

Section 5a was tested with the exact folder shape you have now (suite: 351 passed). The rest of this guide assumes Option A.

```
nlgcode\
  pyproject.toml        <- workspace root (new)
  uv.lock               <- created by uv
  .python-version       <- "3.13"
  readers\              pyproject.toml  (project "readers")
  search\               pyproject.toml  (project "docsearch")
  grove\                pyproject.toml  (project "grove")
  treequery\            pyproject.toml  (project "treequery")
```

Root `pyproject.toml` (this exact file was tested):

```toml
[project]
name = "nlgcode"
version = "0.0.0"
description = "Workspace root for Ken's packages; installs nothing itself"
requires-python = ">=3.10"
dependencies = []

[tool.uv.workspace]
members = ["readers", "search", "grove", "treequery"]

[tool.uv.sources]
readers = { workspace = true }
docsearch = { workspace = true }
grove = { workspace = true }
treequery = { workspace = true }

[dependency-groups]
dev = ["pytest>=7", "pytest-cov>=4", "hypothesis>=6", "pyflakes"]

[tool.uv]
config-settings = { editable_mode = "compat" }
```

Then:

```powershell
cd C:\Users\nlgun\nlgcode
uv python pin 3.13
uv sync --all-packages        # creates .venv, resolves, writes uv.lock, installs all four editable
uv lock --check               # CI-style: fails if uv.lock is stale
```

### 5a. Option B: treequery stays in `personal\nlgcode\treequery`

Add this to treequery's `pyproject.toml` (the relative path goes from treequery's folder to readers):

```toml
[tool.uv.sources]
readers = { path = "../../../nlgcode/readers", editable = true }
```

Then, from `C:\Users\nlgun\personal\nlgcode\treequery`:

```powershell
uv sync --extra text --extra dev       # creates .venv and uv.lock here; readers is installed editable from nlgcode
uv run pytest -q                       # 351 passed
uv tool install --editable . --with-editable ..\..\..\nlgcode\readers     # the global `treequery` command
```

Trade-offs: no shared lockfile, so treequery and grove could resolve different versions of the same library (pyyaml is
the only dependency they share); `uv sync` there does not touch the other packages. If the relative path is wrong,
`uv sync` says `Distribution not found at: file:///...`; fix the number of `..`.

### Three things that will surprise you

1. **The folder `search` is the project `docsearch`.** uv addresses projects by *name*:
   `uv run --package docsearch pytest`, not `--package search`.
2. **Compat editable mode.** `readers` and `docsearch` use a flat layout (`readers\readers\`, `search\docsearch\`).
   Without `editable_mode = "compat"`, running `python` from the workspace root finds the *folder*
   `readers\` first and imports it as an empty namespace package, shadowing the real one. The `config-settings` line above fixes this. (Converting both to `src\` layout, as grove and treequery already use, also fixes it and is the better long-term move.)
3. **Pytest per package.** Run each suite from its own folder; one combined pytest session over all four fails because test files share basenames (`test_cli.py` and friends).

```powershell
cd readers;   uv run --package readers   pytest ; cd ..
cd search;    uv run --package docsearch pytest ; cd ..
cd grove;     uv run --package grove     pytest ; cd ..
cd treequery; uv run --package treequery pytest ; cd ..
```

All four suites passed this way in the scratch workspace.

## 6. Per-package pyproject notes

**treequery** (already uv-ready). Its `dev` extra works, but the modern spelling is a *dependency group* (PEP 735), which is
never published with the package and is installed by default by `uv sync`:

```toml
[dependency-groups]
dev = ["pytest", "pytest-cov", "hypothesis", "reportlab", "python-docx", "openpyxl", "odfpy"]
```

Optional readers support stays an extra: `uv sync --extra text`. In the workspace, `treequery` finds `readers` through `[tool.uv.sources]`, so you never `pip install` it by hand.

**readers.** Add nothing; consider renaming the *distribution* (not the import name) to `nlg-readers`. `readers` is a name already taken on PyPI, and the day you run `uv pip install readers` outside the workspace you would get someone else's package. `import readers` is unchanged.

**docsearch (search).** Pin the readers dependency in the workspace by leaving it unversioned (as now); `[tool.uv.sources]` redirects it to your copy. Delete the stale `search\docs\docsearch` and `search\docs\tests` copies and the `*.egg-info` folders; they confuse tools and add nothing.

**grove.** `requires-python = ">=3.8"` is untrue in practice for a workspace pinned to 3.13 and lxml/py7zr wheels; raise it to what you actually test (`>=3.10`). Declare pandas (section 8).

## 7. Daily commands

| Task | Command |
|---|---|
| Run a tool inside the project | `uv run treequery run roots.yml` (or `uv run manifest ...`) |
| Run a script with the workspace's packages | `uv run python myscript.py` |
| Run tests for one package | `uv run --package treequery pytest -q` |
| Coverage | `uv run --package treequery pytest --cov=treequery --cov-branch` |
| Add a dependency to one package | `uv add --package grove lxml` |
| Add a dev-only tool | `uv add --dev pyflakes` |
| Optional extra | `uv add --package grove --optional dataframe pandas` |
| Remove | `uv remove --package grove pandas` |
| Upgrade everything | `uv lock --upgrade && uv sync --all-packages` |
| Upgrade one package | `uv lock --upgrade-package lxml` |
| See why something is installed | `uv tree --package treequery` |
| Try something without touching the project | `uv run --with rich python -c "import rich"` |
| Build wheel + sdist | `uv build --package treequery` (output in `dist\`) |
| Export for another machine | `uv export --package treequery --no-dev --no-hashes > requirements.txt` |
| Reproduce exactly (CI, a new PC) | `uv sync --locked --all-packages` |

`uv run` re-checks the lock and environment every time and fixes them first, so you can edit a `pyproject.toml` and just run.

## 8. The pandas gap in grove

Collecting grove's tests failed with `ModuleNotFoundError: pandas`: its dataframe commands import pandas but
`pyproject.toml` does not declare it. conda hid this because pandas was installed in the environment for other reasons.
Quick workaround: `uv run --with pandas pytest`. The right fix (tested, `uv add` writes it for you):

```toml
[project.optional-dependencies]
dataframe = ["pandas>=2.3"]
```

Then `uv sync --extra dataframe`, and grove's core stays light. If it is always needed, make it a plain dependency instead.

## 9. Installing the command-line tools globally

Tools you want on `PATH` everywhere, independent of any project environment:

```powershell
cd C:\Users\nlgun\nlgcode
uv tool install --editable .\treequery --with-editable .\readers
uv tool install --editable .\grove          # manifest, mnft, scheduler, schd
uv tool install --editable .\search --with-editable .\readers   # docsearch
uv tool list --show-with --show-paths
```

- `--editable` means edits to your source take effect immediately; no reinstall.
- `--with-editable .\readers` puts your local `readers` in that tool's private environment (the tool environment does not see the workspace).
- Add extras like this: `".\treequery[text]"`.
- Update after changing dependencies: `uv tool upgrade treequery`, or reinstall with `--reinstall`.
- Remove: `uv tool uninstall treequery`.

Each tool gets its own isolated environment, which replaces the habit of `pipx` or of activating a conda environment just to run `manifest`.

## 10. The copy script (copyindate2code.py)

It is one file with four third-party imports, so a project is overkill. Give it PEP 723 inline metadata:

```powershell
uv add --script copyindate2code.py pyyaml python-dateutil loguru odfpy
uv run --script copyindate2code.py --help
```

uv writes a block at the top of the file:

```python
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "loguru",
#     "odfpy",
#     "python-dateutil",
#     "pyyaml",
# ]
# ///
```

`uv add --script` sets `requires-python` to the interpreter it used; lower it to `>=3.10` if you want. The script then runs
anywhere uv is installed, with no environment of its own to maintain. (This edits the file, so do it on your say-so; I have not touched your copy folder.)
If it later grows (modules, tests), promote it to a package in the workspace.

## 11. Migration plan, in order

1. Install uv, Python 3.13, set `UV_LINK_MODE=copy` (section 2).
2. Record the conda environments (section 3). Keep them until step 7.
3. Add the root `pyproject.toml`, move `treequery` beside the others, `uv sync --all-packages`.
4. Fix grove's pandas, readers' name (optional), then `uv lock`.
5. Run the four test suites (section 5). They should match your conda results.
6. `uv tool install --editable ...` for the CLIs (section 9). Run `treequery --help`, `manifest`, `schd` from a fresh terminal.
7. Add inline metadata to `copyindate2code.py`; run once on a throwaway folder.
8. After a week with no reach for conda: `conda env remove -n <name>` for each, then uninstall or leave conda idle. Commit `uv.lock`.

Scheduled tasks (Task Scheduler) that call `python` from a conda env must change to
`uv run --project C:\Users\nlgun\nlgcode --package grove schd ...` or to the global tool path from `uv tool dir --bin`. Test each one by hand once.

## 12. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `package 'search' not found` | Use the project name: `--package docsearch`. |
| `import readers` gives an empty module from the root folder | Namespace shadowing; add `editable_mode = "compat"` (section 5) or move to `src\` layout. |
| Warning: failed to hardlink | `UV_LINK_MODE=copy`. |
| `.venv` being synced by Drive | Put it elsewhere with `UV_PROJECT_ENVIRONMENT`. |
| `treequery` not found in a new terminal | `uv tool update-shell`, reopen the terminal. |
| Tool sees old dependencies | `uv tool install --reinstall --editable ...`. |
| `No solution found` on an old `requires-python` | Raise the floor (grove's `>=3.8`) to what you test. |
| Tests collect with "import file mismatch" | Run pytest per package, not from the workspace root. |
| A package only on conda | Check for a wheel on PyPI first (`uv pip install name`); otherwise keep that one project on conda. |
| Want conda's behaviour for a one-off | `uv run --with pkg python` makes a throwaway environment and leaves nothing behind. |
