"""Control a real Deebot through the Ecovacs cloud.

Setup:
  1. edit deebot_real.yaml (country, robot name, deebot vs deebot-v2)
  2. store credentials once (any later terminal will find them):
         [Environment]::SetEnvironmentVariable("ECOVACS_EMAIL", "you@example.com", "User")
         [Environment]::SetEnvironmentVariable("ECOVACS_PASSWORD", "...", "User")
     If they are missing, loading the topology fails with this exact recipe.
  3. run:
         python demo_real.py                # battery + state (read-only)
         python demo_real.py --locate       # robot plays a sound — good first test
         python demo_real.py --clean
         python demo_real.py --pause | --resume | --stop
         python demo_real.py --dock

A write is NEVER silently re-fired (DESIGN V2 decision 6): if a command fails
with delivered=unknown, this script reports it and lets YOU decide to re-run.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import deebot_driver  # noqa: F401  registers ecovacs,deebot / ecovacs,deebot-v2
import ecovacs_bus    # noqa: F401  registers ecovacs,cloud

import shal


def main() -> int:
    parser = argparse.ArgumentParser(description="Drive a Deebot via SHAL")
    action = parser.add_mutually_exclusive_group()
    for name, help_ in [("clean", "start auto-clean"), ("pause", "pause cleaning"),
                        ("resume", "resume cleaning"), ("stop", "stop cleaning"),
                        ("dock", "return to charger"), ("locate", "play 'I am here'")]:
        action.add_argument(f"--{name}", action="store_true", help=help_)
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    # This is a REAL robot: every motion command asks for confirmation on the
    # terminal before it is sent (issue #14). ConsoleApprover denies automatically
    # if stdin is not a TTY, so a piped/cron run never moves the robot unattended.
    shal.set_approver(shal.ConsoleApprover())

    try:
        hal = shal.load(HERE / "deebot_real.yaml")
    except shal.LoadError as e:  # e.g. credentials missing — message carries the fix
        print(e, file=sys.stderr)
        return 2

    with hal:
        bot = hal.get_device("cleaner")
        try:
            if args.clean:
                bot.start_cleaning(); print("cleaning started")
            elif args.pause:
                bot.pause(); print("paused")
            elif args.resume:
                bot.resume(); print("resumed")
            elif args.stop:
                bot.stop_cleaning(); print("stopped")
            elif args.dock:
                bot.dock(); print("returning to dock")
            elif args.locate:
                bot.locate(); print("robot is announcing itself")
            print(f"battery : {bot.get_battery_percent()} %")
            print(f"state   : {bot.get_clean_state()}")
        except shal.ApprovalDenied as e:
            print(f"not approved — nothing was sent to the robot.\n  {e}",
                  file=sys.stderr)
            return 1
        except shal.HopError as e:
            if e.delivered == "unknown":
                print(f"delivery UNKNOWN — the command may or may not have reached "
                      f"the robot; re-run if safe.\n  {e}", file=sys.stderr)
            else:
                print(f"failed before delivery:\n  {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
