"""Sim twin of a Sonos speaker: an in-memory stand-in for ``soco.SoCo``.

A root driver has no SHAL bus, so its twin is not a bus model. It is a fake of
the library the driver wraps, exposing the exact subset of the ``soco`` API the
driver calls, so the driver body is the same for sim and real. ``driver.py``
builds it when the node's address is ``sim``. It needs no ``soco`` and no speaker.
"""


class SimSoCo:
    """The ``soco.SoCo`` calls ``driver.py`` makes, answered from state."""

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
