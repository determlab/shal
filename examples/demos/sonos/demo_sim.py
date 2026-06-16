"""Drive a (simulated) Sonos speaker through SHAL — zero hardware or dependencies.

    python demo_sim.py

For a real speaker: `pip install soco`, edit `sonos.yaml` with its IP, and load
that file instead. Playback/volume are benign writes (not gated); only the read
ops are exercised first so you can see state before anything changes.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import sonos_driver  # noqa: F401  registers sonos,speaker

import shal
from shal.capabilities import MediaPlayer


def main() -> None:
    with shal.load(HERE / "sonos_sim.yaml") as hal:
        spk = hal.get_device("sonos")
        assert isinstance(spk, MediaPlayer)  # the capability is the contract, not the driver

        print(f"state      : {spk.get_state()}")
        print(f"volume     : {spk.get_volume()}")
        print(f"now playing: {spk.now_playing()}")

        # benign, reversible writes — instant, so SHAL runs them without a gate prompt
        spk.play()
        print(f"play   -> {spk.get_state()}")
        spk.pause()
        print(f"pause  -> {spk.get_state()}")
        spk.set_volume(40)
        print(f"volume -> {spk.get_volume()}")


if __name__ == "__main__":
    main()
