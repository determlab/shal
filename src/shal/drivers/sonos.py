"""sonos,speaker — control a Sonos speaker from an AI agent (issue #28).

The first "wrap an existing Python library" hero driver: a **root** driver
(`kind = None`, no SHAL bus) that wraps the `soco` library. `soco` is imported
lazily and is an optional extra (`pip install pyshal[sonos]`) — needed only to
talk to a real speaker.

Sim-first like the rest of SHAL: address ``sim`` selects a built-in in-memory
model (no `soco`, no hardware), so the whole "control my Sonos" flow validates
with zero dependencies. Address otherwise is the speaker's IP/host.

Playback and volume are **benign, reversible writes** (`side_effect="write"`) —
controlling a speaker is instant, not physical actuation — so an agent drives it
without a prompt per call. Reads are free. (A cautious operator can still run a
stricter approval policy.)
"""
from __future__ import annotations

from typing import Any

from .. import registry
from ..capabilities import MediaPlayer
from ..driver import Driver, idempotent, op
from ..errors import HopError
from ..log import current_txn


class _SimSonos:
    """In-memory stand-in for `soco.SoCo`, exposing the exact subset of its API
    this driver uses — so the driver body is identical for sim and real."""

    def __init__(self) -> None:
        self.volume = 25
        self._state = "STOPPED"
        self._track = {"title": "Aja", "artist": "Steely Dan", "album": "Aja"}

    def play(self) -> None:
        self._state = "PLAYING"

    def pause(self) -> None:
        self._state = "PAUSED_PLAYBACK"

    def stop(self) -> None:
        self._state = "STOPPED"

    def next(self) -> None:
        pass

    def previous(self) -> None:
        pass

    def get_current_transport_info(self) -> dict:
        return {"current_transport_state": self._state}

    def get_current_track_info(self) -> dict:
        return dict(self._track)


@registry.register
class SonosSpeaker(Driver, MediaPlayer):
    compatible = "sonos,speaker"
    kind = None          # root driver: wraps soco directly, no SHAL bus
    llm_ready = True

    def bind(self, node) -> None:
        super().bind(node)
        self._addr = str(node.address)
        self._client: Any = None  # lazy: built on first op (sim or real)

    # -- client (lazy; sim or real soco) --------------------------------------
    def _client_obj(self) -> Any:
        if self._client is None:
            if self._addr == "sim":
                self._client = _SimSonos()
            else:  # real hardware — soco only needed here (pyshal[sonos])
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

    # -- transport controls (benign, reversible writes) -----------------------
    @op("Start or resume playback on this speaker.", side_effect="write")
    def play(self) -> None:
        self._do(lambda c: c.play())

    @op("Pause playback on this speaker.", side_effect="write")
    def pause(self) -> None:
        self._do(lambda c: c.pause())

    @op("Stop playback on this speaker.", side_effect="write")
    def stop(self) -> None:
        self._do(lambda c: c.stop())

    @op("Skip to the next track.", side_effect="write")
    def next_track(self) -> None:
        self._do(lambda c: c.next())

    @op("Go back to the previous track.", side_effect="write")
    def previous_track(self) -> None:
        self._do(lambda c: c.previous())

    @op("Set the speaker volume (0-100).", side_effect="write",
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
        return str(info.get("current_transport_state", "UNKNOWN"))

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
                "description": "Sonos speaker IP/host, or 'sim' for the built-in "
                               "simulator (no hardware, no soco needed).",
                "examples": ["192.168.1.50", "sim"],
            },
            "config_schema": {"type": "object", "properties": {},
                              "additionalProperties": False},
        }
