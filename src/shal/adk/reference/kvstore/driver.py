"""python,dbm — wrap an existing Python library as a root driver: the short recipe.

The library is the stdlib `dbm.dumb` (a key-value store in files), so this runs
anywhere with nothing to install. Put your vendor SDK or client library where
`dbm.dumb` is and the shape stays the same (AGENT_GUIDE.md, "Wrap a library"):

- `kind = None` makes this a root driver. The loader checks for a parent bus
  only when a driver declares a `kind`, so this node needs no bus above it.
- No constructor arguments. The loader builds a device driver as `cls()` (a bus
  is built as `cls(node)`); the node arrives later, in `bind()`, which only
  reads the address. No I/O there.
- Connect lazily. The library is imported and opened on the first op and kept
  on `self`, so `shal tools` lists the ops without touching the store.
- Register it. Here, `@registry.register` (run it with `--drivers driver.py`);
  in a published package, a `shal.drivers` entry point.

Address `sim` is the twin: the same `dbm.dumb`, in a throwaway temp folder. Any
other address is the store's path (dbm.dumb adds `.dat` / `.dir` / `.bak`).
`put` and `delete` are `write`s: each returns what the other needs to undo it.
"""
from __future__ import annotations

import shutil
import tempfile
import threading
import weakref
from pathlib import Path

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.errors import Error, HopError


class NoSuchKey(Error, LookupError):
    """The store answered, and the key is not there: the device said no (a
    `shal.Error`, audited), not a failed hop (a `HopError`)."""


def _drop_twin(db, folder: str) -> None:
    db.close()  # first: dbm.dumb writes its index on close
    shutil.rmtree(folder, ignore_errors=True)


@registry.register
class DbmStore(Driver):
    compatible = "python,dbm"
    kind = None          # root driver: wraps dbm.dumb directly, no SHAL bus
    llm_ready = True

    def bind(self, node) -> None:  # the loader made DbmStore(), then calls bind(node)
        super().bind(node)
        self._path = str(node.address)
        self._db = None                 # lazy: opened on the first op, never here
        self._lock = threading.Lock()   # dbm.dumb is not thread-safe: one call at a time

    # -- the library (lazy) -----------------------------------------------------
    def _client(self):
        if self._db is None:
            import dbm.dumb  # import + open here, on the first op
            if self._path != "sim":
                self._db = dbm.dumb.open(self._path, "c")
            else:  # the twin: the real library in a throwaway folder, gone with us
                folder = tempfile.mkdtemp(prefix="shal-dbm-")
                self._db = dbm.dumb.open(str(Path(folder) / "store"), "c")
                weakref.finalize(self, _drop_twin, self._db, folder)
        return self._db

    def _do(self, fn, write: bool = False):
        """Run one library call; a library failure is a HopError (D12, D18)."""
        with self._lock:
            try:
                db = self._client()
                result = fn(db)
                if write:
                    db.sync()  # dbm.dumb keeps part of its index in memory until sync
                return result
            except OSError as e:  # dbm.dumb.error is an OSError
                raise HopError(f"dbm {self._path}: {e}", path=self.node.path,
                               hop="dbm", delivered="unknown") from e

    # -- none: reads, live or raise ---------------------------------------------
    @idempotent
    @op("Read the value stored under `key` now. Raises if there is no such key.",
        side_effect="none")
    def get(self, key: str) -> str:
        def read(db):
            if key.encode() not in db:
                raise NoSuchKey(f"no key {key!r}")
            return db[key.encode()].decode()
        return self._do(read)

    @idempotent
    @op("List every key in the store now.", side_effect="none")
    def list_keys(self) -> list:
        return sorted(k.decode() for k in self._do(lambda db: list(db.keys())))

    # -- write: each one undone by the other --------------------------------------
    @op("Store `value` under `key`; return the value it replaced, or null if the "
        "key is new. Undo: put the old value back, or delete the key if it was null.",
        side_effect="write")
    def put(self, key: str, value: str) -> str | None:
        def write(db):
            old = db.get(key.encode())
            db[key.encode()] = value.encode()
            return None if old is None else old.decode()
        return self._do(write, write=True)

    @op("Delete `key` and return its value. Undo: put that value back.",
        side_effect="write")
    def delete(self, key: str) -> str:
        def remove(db):
            if key.encode() not in db:
                raise NoSuchKey(f"no key {key!r}")
            old = db[key.encode()].decode()
            del db[key.encode()]
            return old
        return self._do(remove, write=True)

    @classmethod
    def authoring_meta(cls) -> dict:
        return {
            "address_schema": {"type": "string", "minLength": 1,
                               "description": "the store's path (dbm.dumb adds its "
                                              "own suffixes), or 'sim' for the twin",
                               "examples": ["sim", "data/store"]},
            "config_schema": {"type": "object", "properties": {},
                              "additionalProperties": False},
        }
