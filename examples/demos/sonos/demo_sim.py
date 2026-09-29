"""Drive a (simulated) Sonos speaker through SHAL — zero hardware or dependencies.

    python demo_sim.py

For a real speaker: `pip install soco`, edit `sonos.yaml` with its IP, and load
that file instead. Playback/volume are actuators (gated); only the read
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
    # playback/volume ops are actuators (gated); in a pure simulation there is
    # nothing to protect, so auto-approve. A real speaker needs a real approver.
    shal.set_approver(shal.AutoApprove())
    with shal.load(HERE / "sonos_sim.yaml") as hal:
        spk = hal.get_device("sonos")
        assert isinstance(spk, MediaPlayer)  # the capability is the contract, not the driver

        print(f"state      : {spk.get_state()}")
        print(f"volume     : {spk.get_volume()}")
        print(f"now playing: {spk.now_playing()}")

        # actuators: each one passes the approval gate (auto-approved above)
        spk.play()
        print(f"play   -> {spk.get_state()}")
        spk.pause()
        print(f"pause  -> {spk.get_state()}")
        spk.set_volume(40)
        print(f"volume -> {spk.get_volume()}")


if __name__ == "__main__":
    main()
