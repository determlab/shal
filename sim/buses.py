"""Simulated buses from many different worlds.

Each bus implements the single contract method via `perform` (local IO) and,
where it makes sense, `render`/`parse` (so it can be tunneled over a remote bus
with no agent). The point is to stress the recursive exchange model, not to be
electrically accurate.
"""
import struct
from core import Bus, bus, Subscription

SELECTS = {"count": 0}   # global counter to prove mux is_active caching


def _to_int(a):
    if isinstance(a, int):
        return a
    return int(a, 16)


# ---- shared I2C simulation (single source for local AND ssh-tunneled paths) ----
def sim_i2c(addr, payload, channel):
    op = payload[0]
    if op == "MUXSEL":
        return b""
    if op == "RD" and addr == 0x48:            # ti,tmp102
        temp = {1: 30.0}.get(channel, 23.5)    # channel 1 reads warmer
        raw = int(round(temp / 0.0625)) << 4
        return bytes([(raw >> 8) & 0xFF, raw & 0xFF])
    return b"\x00"


@bus("i2c")
class I2cBus(Bus):
    tunnelable = True   # can be expressed as a CLI command (i2ctransfer-style)

    def _busno(self):
        digits = "".join(ch for ch in str(self.host.addr) if ch.isdigit())
        return digits or "0"

    def perform(self, addr, payload):
        return sim_i2c(_to_int(addr), payload, self.current_channel)

    def render(self, addr, payload):
        op, reg = payload[0], payload[1]
        return f"I2CRD bus={self._busno()} dev={addr} reg={reg}"

    def parse(self, raw):
        return bytes.fromhex(raw)


@bus("i2c-mux")
class MuxBus(Bus):
    """A node that is also a bus. Selects a channel on the upstream bus, then
    forwards the device op. Channel selection is cached (is_active)."""

    def is_channel_active(self, ch):
        return self.upbus.current_channel == ch

    def exchange(self, addr, payload):
        self.ensure_ready()
        chan_str, dev = str(addr).split(":")
        ch = int(chan_str.replace("ch", ""))
        if not self.is_channel_active(ch):                 # only select if needed
            self.upbus.exchange(self.host.addr, ("MUXSEL", ch))
            self.upbus.current_channel = ch
            SELECTS["count"] += 1
        return self.upbus.exchange(dev, payload)


@bus("spi")
class SpiBus(Bus):
    def perform(self, addr, payload):
        # bosch,bmi160 accel read: 6 bytes little-endian int16 x,y,z
        if payload[0] == "RD":
            return struct.pack("<hhh", 120, -240, 16384)
        return b"\x00"


@bus("uart")
class UartBus(Bus):
    # STRAIN: no addressing, data is a continuous stream. We model a pull read of
    # "the latest line", but real UART pushes unsolicited data with no addr.
    def perform(self, addr, payload):
        if payload[0] == "READLINE":
            return "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,,*6A"
        return ""


@bus("pcie")
class PcieBus(Bus):
    REGS = {0x5400: 0x12345678, 0x5404: 0x00009ABC}   # e1000 RAL/RAH

    def perform(self, addr, payload):
        if payload[0] == "RD32":
            return self.REGS.get(payload[1], 0)
        return 0


@bus("usb")
class UsbBus(Bus):
    def perform(self, addr, payload):
        if payload[0] == "CTRL":          # generic,usb-thermo
            return bytes([22])
        return b"\x00"


@bus("modbus")
class ModbusBus(Bus):
    # stateful local bus: coils per unit id
    def __init__(self, host, cfg):
        super().__init__(host, cfg)
        self.coils = {}

    def perform(self, addr, payload):
        op = payload[0]
        if op == "WRITE_COIL":
            self.coils[(addr, payload[1])] = bool(payload[2])
            return True
        if op == "READ_COIL":
            return self.coils.get((addr, payload[1]), False)
        return None


@bus("can")
class CanBus(Bus):
    # STRAIN: broadcast, message-id addressed, no guaranteed reply.
    RPM = {"val": 3000}

    def perform(self, addr, payload):
        op = payload[0]
        if op == "TX":                    # fire-and-forget frame, NO reply
            return None
        if op == "POLL":                  # request a value (bent into req/resp)
            return self.RPM["val"].to_bytes(4, "big")
        return None


@bus("ble")
class BleBus(Bus):
    # GATT read holds; notifications (push) are NOT representable here.
    def perform(self, addr, payload):
        if payload[0] == "READ_CHAR" and payload[1] == "2A37":
            return bytes([0x00, 72])      # flags, bpm
        if payload[0] == "NOTIFY":
            raise RuntimeError("BLE notify is async push; no synchronous result")
        return b"\x00"


@bus("ssh")
class SshBus(Bus):
    remote = True            # children live on the far side
    supports_stream = True   # can HOLD a long-running command (e.g. `tail -f`) open
    # ssh is the terminal that *runs* a carried command; it does not tunnel further.

    def __init__(self, host, cfg):
        super().__init__(host, cfg)
        self._streams = []

    def activate(self):
        # demonstrates connection caching: only "connects" once
        SELECTS.setdefault("ssh_connects", 0)
        SELECTS["ssh_connects"] += 1
        self._active = True

    def _open_stream(self, addr, callback):
        # simulates holding `ssh user@host tail -f <addr>` open; no agent, just a
        # long-running shell command whose stdout lines we forward up.
        entry = (addr, callback)
        self._streams.append(entry)
        return Subscription(lambda: self._streams.remove(entry))

    def emit(self, addr, line):
        for a, cb in list(self._streams):
            if a == addr:
                cb(line)

    def perform(self, addr, payload):
        # payload is a rendered shell command string. Simulate a remote shell.
        cmd = payload
        if isinstance(cmd, str) and cmd.startswith("I2CRD"):
            kv = dict(tok.split("=") for tok in cmd.split()[1:])
            dev = _to_int(kv["dev"])
            reg = int(kv["reg"])
            return sim_i2c(dev, ("RD", reg), None).hex()   # stdout = hex
        raise RuntimeError(f"remote shell can't run: {cmd!r}")


@bus("tcp")
class TcpBus(Bus):
    remote = True
    def perform(self, addr, payload):
        if payload.strip() == b"READ":
            return b"42\n"
        return b"\n"


@bus("http")
class HttpBus(Bus):
    remote = True
    STATE = {"battery": 87, "status": "idle"}

    def perform(self, addr, payload):
        method, path = payload[0], payload[1]
        if method == "GET" and path == "/api/status":
            return dict(self.STATE)
        if method == "POST" and path == "/api/clean":
            self.STATE["status"] = "cleaning"
            return {"ok": True}
        return {}


@bus("mqtt")
class MqttBus(Bus):
    remote = True
    supports_stream = True   # native pub/sub: the proper home for async
    RETAINED = {"home/temp": 21.0}

    def __init__(self, host, cfg):
        super().__init__(host, cfg)
        self._streams = []

    def perform(self, addr, payload):
        op = payload[0]
        if op == "PUBLISH":
            self.RETAINED[payload[1]] = payload[2]
            return {"ack": True}
        if op == "SUBSCRIBE":
            # synchronous read = retained snapshot only (the old hack)
            return self.RETAINED.get(payload[1])
        return None

    def _open_stream(self, addr, callback):
        # proper async: hold the subscription open, deliver every push via callback
        entry = (addr, callback)
        self._streams.append(entry)
        return Subscription(lambda: self._streams.remove(entry))

    def emit(self, topic, value):
        self.RETAINED[topic] = value
        for a, cb in list(self._streams):
            if a == topic:
                cb(value)
