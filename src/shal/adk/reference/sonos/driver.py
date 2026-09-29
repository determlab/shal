"""sonos,speaker — a root driver that wraps an existing library (soco).

The guide's headline pattern: no SHAL bus (`kind = None`), the driver calls the
`soco` library directly and connects lazily, on the first op. Address ``sim``
selects the twin in ``sim.py`` (no `soco`, no speaker); any other address is the
speaker's IP/host and needs ``pip install soco``.

Playback and volume change the physical world (sound in a room), so they are
actuators (`side_effect="actuator"`) and gated like every other actuator: a
call stops for approval. Reads are live or raise.
"""
from __future__ import annotations

from typing import Any

from shal import registry
from shal.capabilities import MediaPlayer
from shal.driver import Driver, idempotent, op
from shal.errors import HopError
from shal.log import current_txn


@registry.register
class SonosSpeaker(Driver, MediaPlayer):
    compatible = "sonos,speaker"
    kind = None          # root driver: wraps soco directly, no SHAL bus
    llm_ready = True

    def bind(self, node) -> None:
        super().bind(node)
        self._addr = str(node.address)
        self._client: Any = None  # lazy: built on the first op, never in bind()

    # -- client (lazy; the sim twin or real soco) -----------------------------
    def _client_obj(self) -> Any:
        if self._client is None:
            if self._addr == "sim":
                try:
                    from .sim import SimSoCo  # the installed reference (a package)
                except ImportError:
                    from sim import SimSoCo  # your copy: sim.py beside driver.py
                self._client = SimSoCo()
            else:  # a real speaker: soco is only needed here (`pip install soco`)
                import soco  # noqa: PLC0415  (lazy by design)
                self._client = soco.SoCo(self._addr)
        return self._client

    def _do(self, fn):
        """Run one client call, mapping network / soco errors to HopError so the
        agent surface reports a clean, honest failure (delivery unknown)."""
        try:
            return fn(self._client_obj())
        except OSError as e:
            raise self._hop(e) from e
        except Exception as e:  # soco.exceptions.* — mapped without importing soco
            if type(e).__module__.split(".")[0] == "soco":
                raise self._hop(e) from e
            raise

    def _hop(self, e: Exception) -> HopError:
        return HopError(f"sonos {self._addr}: {e}", path=self.node.path,
                        hop="sonos", txn=current_txn.get(), delivered="unknown")

    # -- transport controls (actuators: gated) --------------------------------
    @op("Start or resume playback on this speaker.", side_effect="actuator")
    def play(self) -> None:
        self._do(lambda c: c.play())

    @op("Pause playback on this speaker.", side_effect="actuator")
    def pause(self) -> None:
        self._do(lambda c: c.pause())

    @op("Stop playback on this speaker.", side_effect="actuator")
    def stop(self) -> None:
        self._do(lambda c: c.stop())

    @op("Skip to the next track.", side_effect="actuator")
    def next_track(self) -> None:
        self._do(lambda c: c.next())

    @op("Go back to the previous track.", side_effect="actuator")
    def previous_track(self) -> None:
        self._do(lambda c: c.previous())

    @op("Set the speaker volume (0-100).", side_effect="actuator",
        params={"level": {"type": "integer", "minimum": 0, "maximum": 100}})
    def set_volume(self, level: int) -> None:
        self._do(lambda c: setattr(c, "volume", int(level)))

    # -- reads (free) ---------------------------------------------------------
    @idempotent
    @op("Read the current volume (0-100).", side_effect="none")
    def get_volume(self) -> int:
        return int(self._do(lambda c: c.volume))

    @idempotent
    @op("Read the playback state (e.g. PLAYING / PAUSED_PLAYBACK / STOPPED).",
        side_effect="none")
    def get_state(self) -> str:
        info = self._do(lambda c: c.get_current_transport_info())
        state = info.get("current_transport_state")
        if not state:  # no live answer: raise, never a made-up default
            raise self._hop(RuntimeError("no transport state in the reply"))
        return str(state)

    @idempotent
    @op("Read what's playing now (title, artist, album).", side_effect="none")
    def now_playing(self) -> dict:
        t = self._do(lambda c: c.get_current_track_info())
        return {"title": t.get("title", ""), "artist": t.get("artist", ""),
                "album": t.get("album", "")}

    @classmethod
    def authoring_meta(cls) -> dict:  # shal.catalog() detail (issue #1)
        return {
            "address_schema": {
                "type": "string",
                "description": "Sonos speaker IP/host, or 'sim' for the twin in "
                               "sim.py (no hardware, no soco needed).",
                "examples": ["192.168.1.50", "sim"],
            },
            "config_schema": {"type": "object", "properties": {},
                              "additionalProperties": False},
        }
