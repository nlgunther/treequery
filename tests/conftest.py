import os
from datetime import datetime

import pytest


def make_tree(root, spec: dict) -> None:
    """spec maps relative paths ('a/b.txt') to file text; creates folders as needed."""
    for rel, content in spec.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)


def set_mtime(path, when: datetime) -> None:
    stamp = when.timestamp()
    os.utime(path, (stamp, stamp))


@pytest.fixture
def tree(tmp_path):
    """A small tree exercising excludes, nesting, hidden and shallow folders."""
    make_tree(tmp_path, {
        "top.txt": "top",
        "a/one.txt": "one",
        "a/skipme/inside.txt": "inside",
        "a/skipme/deeper/deep.txt": "deep",
        "a/keep/two.pdf": "pdf-ish",
        ".hidden/h.txt": "h",
        "Downloads/d1.txt": "d1",
        "Downloads/sub/d2.txt": "d2",
        "b/mail.eml": "eml",
    })
    return tmp_path


def names(entries) -> set:
    return {os.path.relpath(e.path, e.top).replace(os.sep, "/") for e in entries}
