"""shal,ssh-host — CommandTransport over the system ssh client.

Contract (DESIGN V2 'Remote'): argv vector + stdin -> (stdout, stderr, exit),
executed WITHOUT a shell: `ssh host -- prog arg...`. Connection reuse via
ControlMaster, so the per-call cost is the exec, not the handshake.
ssh's own exit code 255 is indistinguishable from a remote command exiting
255 — treated as a transport failure with delivered=unknown (never silently
re-fired; the retry policy is the safety net).
"""
from __future__ import annotations

import subprocess
import time
from collections.abc import Sequence

from .. import registry
from ..driver import Driver
from ..errors import HopError, HopTimeout
from ..log import bus_logger, current_txn
from ..node import Node
from ..transport import CommandTransport, Completed, Transport


def ssh_argv(target: str, argv: Sequence[str]) -> list[str]:
    """Pure renderer — unit-testable. No shell string ever appears."""
    return [
        "ssh",
        "-o", "BatchMode=yes",            # never hang on a password prompt
        "-o", "ControlMaster=auto",       # persistent session per connection
        "-o", "ControlPath=~/.ssh/shal-%C",
        "-o", "ControlPersist=60",
        target,
        "--",                             # end of ssh options, start of argv
        *argv,
    ]


@registry.register
class SshBus(Driver, Transport, CommandTransport):
    compatible = "shal,ssh-host"
    kind = None  # root, or nested behind another CommandTransport

    DEFAULT_TIMEOUT_S = 30.0

    def __init__(self, node: Node) -> None:
        Transport.__init__(self, node)
        self.log = bus_logger("ssh", node.path)

    def run(self, argv: Sequence[str], stdin: bytes = b"") -> Completed:
        with self.lock:
            self.ensure_ready()
            full = ssh_argv(str(self.host.address), argv)
            t0 = time.perf_counter()
            try:
                p = subprocess.run(full, input=stdin, capture_output=True,
                                   timeout=self.DEFAULT_TIMEOUT_S)
            except FileNotFoundError as e:
                raise HopError("ssh client not installed", path=self.host.path,
                               hop="ssh", txn=current_txn.get(), delivered="no") from e
            except subprocess.TimeoutExpired as e:
                self._active = False
                raise HopTimeout(f"ssh exceeded {self.DEFAULT_TIMEOUT_S}s",
                                 path=self.host.path, hop="ssh",
                                 txn=current_txn.get(), delivered="unknown") from e
            if p.returncode == 255:  # ssh transport failure (see module docstring)
                self._active = False
                raise HopError(
                    f"ssh transport failure: {p.stderr.decode(errors='replace').strip()[:200]}",
                    path=self.host.path, hop="ssh",
                    txn=current_txn.get(), delivered="unknown")
            self.log.debug("run %s (%d args) exit %d", argv[0], len(argv) - 1,
                           p.returncode, event="run", op=str(argv[0]),
                           duration_ms=round((time.perf_counter() - t0) * 1000, 1))
            return Completed(stdout=p.stdout, stderr=p.stderr, exit=p.returncode)
