"""Drivers from different worlds. Each translates capability calls into the
payload its parent bus understands, via self.io()."""
import struct
from core import Driver, driver


@driver("ti,tmp102")
class Tmp102(Driver):
    def read(self):
        raw = self.io(("RD", 0x00))
        val = ((raw[0] << 8) | raw[1]) >> 4
        return round(val * 0.0625, 4)


@driver("bosch,bmi160")
class Bmi160(Driver):
    def read_accel(self):
        raw = self.io(("RD", 0x12))
        return struct.unpack("<hhh", raw)


@driver("ublox,neo6m")
class NeoGps(Driver):
    def position(self):
        line = self.io(("READLINE",))
        f = line.split(",")
        return {"lat": f[3] + f[4], "lon": f[5] + f[6]}


@driver("intel,e1000")
class E1000(Driver):
    def mac(self):
        lo = self.io(("RD32", 0x5400))
        hi = self.io(("RD32", 0x5404))
        b = lo.to_bytes(4, "little") + (hi & 0xFFFF).to_bytes(2, "little")
        return ":".join(f"{x:02x}" for x in b)


@driver("generic,usb-thermo")
class UsbThermo(Driver):
    def read(self):
        return self.io(("CTRL", 0x01))[0]


@driver("acme,modbus-relay")
class ModbusRelay(Driver):
    def set(self, on):
        self.io(("WRITE_COIL", 0x0000, on))

    def get(self):
        return bool(self.io(("READ_COIL", 0x0000)))


@driver("vesc,motor")
class Vesc(Driver):
    def set_rpm(self, rpm):
        return self.io(("TX", 0x0501, rpm.to_bytes(4, "big")))   # no reply

    def get_rpm(self):
        return int.from_bytes(self.io(("POLL", 0x0901)), "big")


@driver("polar,hr")
class PolarHr(Driver):
    def heart_rate(self):
        return self.io(("READ_CHAR", "2A37"))[1]


@driver("robi,vacuum")
class Vacuum(Driver):
    def battery(self):
        return self.io(("GET", "/api/status"))["battery"]

    def start(self):
        self.io(("POST", "/api/clean", {}))
        return self.io(("GET", "/api/status"))["status"]


@driver("acme,tcp-sensor")
class TcpSensor(Driver):
    def read(self):
        return int(self.io(b"READ").strip())


@driver("acme,mqtt-temp")
class MqttTemp(Driver):
    def read(self):
        # pub/sub has no synchronous reply; this only returns a retained snapshot
        return self.io(("SUBSCRIBE", "home/temp"))

    def subscribe(self, callback):
        # proper async: every broker push delivered to callback
        return self.node.subscribe(callback)


@driver("shal,log-stream")
class LogStream(Driver):
    # remote log follower: async over ssh via a held `tail -f`, NO agent
    def follow(self, callback):
        return self.node.subscribe(callback)
