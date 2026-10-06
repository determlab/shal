#!/usr/bin/env python3
"""The whole story, one script (issue #343) -- now a thin wrapper around
``shal_arena.demo`` (issue #410), which ships inside the ``shal-arena``
package itself so ``shal-arena demo`` runs the same story with no clone and
no config. This script stays runnable directly for anyone who already has
this repo checked out; see ``shal_arena.demo``'s own module docstring for
everything the story actually does.

A fresh venv needs exactly two packages, ``pyshal`` and ``shal-arena``
(neither on PyPI yet -- install both from source, see this directory's
README).
"""
from __future__ import annotations

import sys

try:
    from shal_arena.demo import main
except ImportError as e:
    fix = ("shal-arena is not on PyPI yet; install it from source -- see "
          "examples/demos/story/README.md#install")
    print(f"run_story.py: cannot import shal_arena ({e}). {fix}", file=sys.stderr)
    sys.exit(3)

if __name__ == "__main__":
    sys.exit(main())
