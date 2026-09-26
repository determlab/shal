# The loop over units is this sample's own Python, not SHAL's: no `shal` verb runs
# a sequence. So each record says runner="script" — not pytest, not Bricks.
"""Jig: a jig that logs results — several units, one record each, read back and counted.

A simulated sensor measures each unit against a limit. Each unit's result is one
record, in the shape `shal.record` ships, written to `jig-records/` in the folder
you run from. A SQLite node then reads the records back and counts them.
"""
from datetime import datetime, timezone
from pathlib import Path

import shal
from shal import record
from shal.adk.reference.sqlite.driver import SqliteDatabase  # noqa: F401  registers sqlite,database

TOPOLOGY = Path(__file__).resolve().parent / "topology.yaml"
STORE = "jig-records"                   # the topology's `db` node reads records.db here
UNITS = ["U001", "U002", "U003", "U004", "U005"]
LOW, HIGH = 15.0, 35.0                  # the limit each unit is measured against


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


with shal.load(str(TOPOLOGY)) as hal:
    sensor = hal.get_device("sensor")
    for unit in UNITS:
        started, celsius = now(), sensor.read_celsius()
        ok = LOW <= celsius <= HIGH
        step = record.Step(name="temperature", verdict="pass" if ok else "fail", measurements=[
            record.Measurement(name="temperature", value=celsius, unit="C",
                               limits=record.Limits(min=LOW, max=HIGH), passed=ok)])
        # a fixed id per unit: run it again and the same records are rewritten
        record.write(record.Record(
            record=f"jig-{unit}", unit=unit, station="jig", sequence="jig",
            sequence_version="1", setup="topology.yaml", setup_version="1",
            runner="script", started=started, ended=now(), steps=[step]), STORE)
        print(f"{unit}: {celsius:.2f} C  {'PASS' if ok else 'FAIL'}")

    rows = hal.get_device("db").query("records", limit=1000)
    print(f"records in {STORE}/records.db: {len(rows)}")
