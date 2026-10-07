"""The Play UI's own reference drivers (issue #407 round 2, must-fix 2).

A person playing a task over `ui.play` has no `driver.py` of their own, so
every Play control uses one of these for its instrument's case. They are
shipped IN the package (unlike `arena/examples/*.py`, the copy-paste
templates for a player writing their own driver -- those are never
installed, so a reference driver living there is unreachable from a real
`pip install`). Each file here otherwise matches the shape its
`arena/examples/*` counterpart teaches, and registers plainly, the one way
`shal.registry.register` is meant to be called -- see
`shal_arena.loader.once_per_path` for how Play keeps one class per
compatible across repeated calls with no escape hatch needed.
"""
from __future__ import annotations
