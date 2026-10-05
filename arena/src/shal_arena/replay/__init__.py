"""Replay page, result card and rack page (issue #315).

``card`` builds the one-local-HTML-file result card at the end of a run,
reading only the run record and score file (never the fault before the run
has closed — that file simply does not exist yet). ``rack`` builds the
instrument-tiles page, and the one mechanism it is a thin wrapper over:
picking cases into a bench and getting back a `setup.yaml` SHAL topology
that loads.
"""
from .card import CardData, RunNotFinished, load_card_data, render_card
from .rack import Tile, build_setup_yaml, rack_tiles, render_rack_page

__all__ = [
    "CardData", "RunNotFinished", "load_card_data", "render_card",
    "Tile", "build_setup_yaml", "rack_tiles", "render_rack_page",
]
