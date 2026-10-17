"""Regenerate MANIFEST.txt for the tracked files (source, tests, verify_install.py)."""
import glob
import os
from datetime import datetime, timezone

from verify_install import bundle_hash, sha16

TRACKED = sorted(glob.glob("src/treequery/*.py") + glob.glob("tests/*.py") + glob.glob("docs/*.md") + ["README.md", "CHANGELOG.md", "pyproject.toml"] + ["verify_install.py", "make_manifest.py"])

rows = {}
for path in TRACKED:
    with open(path, "rb") as fh:
        rows[path] = sha16(fh.read())
bundle = bundle_hash(rows)
tests = sum(1 for p in TRACKED if p.startswith("tests/test_") for line in open(p, encoding="utf-8")
            if line.strip().startswith("def test_"))
lines = ["# treequery file manifest", "# Generated: %s" % datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
         "# Format:  sha256_prefix<TAB>local_path",
         "# Comments (lines starting with #) and blank lines are ignored.",
         "# Update only the hash values when files change.",
         "# verify_install.py reads this file - do not change the format.",
         "# Note: hashes computed with CRLF normalisation (Windows-compatible).",
         "# Bundle: SHA-256 of sorted file hashes concatenated (hash-of-hashes).",
         "#", "bundle:\t%s" % bundle, "#", "# Source, test and utility files"]
lines += ["%s\t%s" % (sha, path) for path, sha in rows.items()]
lines += ["#", "# Tests: %d methods" % tests]
with open("MANIFEST.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")
print("Bundle hash: %s\nTests: %d\nFiles: %d" % (bundle, tests, len(rows)))
