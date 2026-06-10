"""Drive a (simulated) Deebot through SHAL — zero hardware, credentials, or network.

    python demo_sim.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import deebot_driver  # noqa: F401  registers ecovacs,deebot / ecovacs,deebot-v2
import sim_cloud      # noqa: F401  registers playground,sim-cloud

import shal
from deebot_driver import VacuumRobot

logging.basicConfig(level=logging.INFO)  # the APP configures logging, never the library


def main() -> None:
    with shal.load(HERE / "deebot_sim.yaml") as hal:
        bot = hal.get_device("cleaner")
        assert isinstance(bot, VacuumRobot)  # capability, not driver, is the contract

        print(f"battery : {bot.get_battery_percent()} %")
        print(f"state   : {bot.get_clean_state()}")

        bot.start_cleaning()
        print(f"start   -> {bot.get_clean_state()}")
        bot.pause()
        print(f"pause   -> {bot.get_clean_state()}")
        bot.resume()
        print(f"resume  -> {bot.get_clean_state()}")
        bot.dock()
        print(f"dock    -> {bot.get_clean_state()}")
        bot.dock()  # already docked: robot answers 30007, driver treats it as success
        print("dock again while docked: ok (code 30007 handled)")

        bot.locate()
        print("locate  : played 'I am here'")


if __name__ == "__main__":
    main()
