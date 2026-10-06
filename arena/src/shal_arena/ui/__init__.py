"""shal-arena ui (issue #406): WATCH a run live in a local page, with replay
export. `data.py` is the one source of truth both the server and the
exporter read from -- never two copies that could drift or disagree about
what counts as "before the run ends" (DoD: no fault shown before then)."""
