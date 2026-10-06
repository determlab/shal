"""Reference sim for the ``sht31`` ADK case (harness — not given to the
player). Answers the write-command/read-6-bytes transaction
``docs/datasheet.md`` describes, adapted from the real single-shot I2C
protocol documented in determlab/adk-lab's ``cases/sht31``.

issue relay-rail: this model has NO fault-specific knowledge of its own —
it reads whatever ``bind_card_state`` last gave it (a zero-argument
callable returning the live temperature in Celsius), the same generic hook
``runner._bind_card_state`` attaches to any sim whose instrument `probe`s a
card point (the callback mechanism the CTO ruling calls for, in place of
baking a fault's shift into this file the way the ``dmm``/``scpi-psu``
cases' static ``config:`` injection does). With nothing bound yet (the
static, unfaulted harness `check-driver` validates against) it reads a
fixed nominal value, same fallback shape issue #312's ``dmm`` case uses."""
from __future__ import annotations

from collections.abc import Callable, Sequence

from shal.buses.sim import sim_model
from shal.transport import Op, Read, Write

NOMINAL_C = 45.0
_HUMIDITY_PCT = 50.0  # not probed by this case's task; kept physically plausible


def _crc8(data: bytes) -> int:
    """Sensirion-style CRC-8: poly 0x31, init 0xFF, MSB-first, no reflect, no
    final XOR — the one this case's datasheet documents (section 5)."""
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def _encode(value: float, *, low: float, span: float) -> bytes:
    raw = round((value - low) * 65535 / span)
    raw = max(0, min(65535, raw))
    data = raw.to_bytes(2, "big")
    return data + bytes([_crc8(data)])


def _frame(celsius: float, humidity_pct: float) -> bytes:
    return _encode(celsius, low=-45.0, span=175.0) + _encode(humidity_pct, low=0.0, span=100.0)


@sim_model("arena,bench-temp1")
class BenchTemp1Model:
    def __init__(self) -> None:
        self._card_state: Callable[[], float] = lambda: NOMINAL_C

    def bind_card_state(self, fn: Callable[[], float]) -> None:
        """issue relay-rail: the arena harness's bind step (``runner.
        _bind_card_state``) calls this with a zero-arg function that
        returns this run's live reading for whatever card point this
        instrument probes — I2C's own version of ``shal,sim-scpi``'s
        ``bind_sim`` hook, which shal core does not provide for sim-i2c."""
        self._card_state = fn

    def txn(self, ops: Sequence[Op]) -> bytes:
        out = b""
        for o in ops:
            if isinstance(o, Write):
                continue  # the command byte selects the measurement mode; one mode here
            elif isinstance(o, Read):
                frame = _frame(self._card_state(), _HUMIDITY_PCT)
                out += frame[: o.n]
        return out
