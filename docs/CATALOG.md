# SHAL Bus & Driver Catalog

A roadmap of the buses and device/service drivers SHAL aims to support, grouped
into **domain libraries** and prioritized by real-world adoption. Use it to pick
what to build next, to claim a driver before writing one, or to see where a
device you need would live.

> **This is a planning document, not shipped API.** Only items marked ✅ exist
> today. Everything else is a proposed `compatible` id + target library.

**Counts:** 74 buses · 271 drivers. (Targets: ≥50 buses, ≥200 drivers.)

---

## How to read this

**Priority — by SHAL's beachhead, not raw popularity.** Ranked by fit to the
target user (AI-agent builders + validation/test engineers) and to SHAL's
differentiator (one model for hardware *and* software, agent-native), then by
leverage. A part can be hugely popular elsewhere and still be P2 here if it
doesn't serve that wedge. Build order is sequenced in the next section.

| | Meaning |
|---|---|
| **P0** | Lands the beachhead — serves the target user, showcases the HW+SW+agent wedge, or unlocks many devices / the first demo. Build first. |
| **P1** | Broadens reach once the beachhead is won. |
| **P2** | Long tail — niche, vendor-specific, or already well-served elsewhere. |

**Status** — ✅ shipped · 🟡 next up · ⬜ backlog.

**`compatible`** — the YAML `driver:` id, `vendor,part` (hardware) or
`shal,<name>` / `vendor,<service>` (software). Names are proposals; the registry
is the source of truth once built.

**Capability** — the Protocol a driver implements. Only `TemperatureSensor`
exists today; the rest are proposed and will be ratified as capabilities land.

---

## Build order (beachhead first)

The 80/20 to land the target user, in waves: **beachhead → differentiation →
flywheel**. Build Wave 1 before scattering across the long tail.

**Wave 1 — make "hand your bench to an agent" real** (the wedge):
- **Buses:** `scpi-raw`, `visa`, `modbus-tcp` (on top of shipped `ssh` / `tcp` /
  `http` / `local` / `i2c-cli`).
- **Instruments — one per class:** PSU (`keysight,e36312a` or `rigol,dp832`),
  DMM (`keysight,34461a`), scope (`rigol,ds1000z`), SMU (`keithley,2400`),
  load (`siglent,sdl1020`).
- **Software (prove HW + SW in one graph):** `postgres,db`, `redis,db`,
  `mqtt,broker`.
- **Onboarding sensors (2-minute quick win):** `ti,tmp102` ✅, `bosch,bme280`,
  `ti,ina219`.

**Wave 2 — broaden the lab:** more instrument models per class, `gpib` /
`usbtmc`, `opcua`, server management (`dmtf,redfish`), CI/observability
(`github,api`, `prometheus,tsdb`), and leverage parts (`nxp,pca9548` ✅,
`microchip,mcp23017`, `ti,ads1115`).

**Wave 3 — long tail:** hobby sensors, wireless, niche fieldbus, and
vendor-specific instruments — mostly community-contributed via `shal-contrib-*`.

---

## Proposed library layout

Domain sub-packages, not a flat dir. Drivers are still discovered by entry point
(`shal.drivers`) regardless of folder — the layout is for humans. Large or
third-party families ship as separate `shal-contrib-*` distributions.

```text
src/shal/
├─ buses/
│  ├─ embedded/      # i2c, spi, uart, can, i3c, 1-wire, smbus, pmbus, lin, jtag, swd …
│  ├─ host/          # usb, pcie, gpio, hid, ftdi-mpsse, sdio, mdio …
│  ├─ net/           # tcp, udp, ssh, telnet, http, websocket, grpc, mqtt, coap, snmp …
│  ├─ instruments/   # visa, gpib, vxi-11, usbtmc, scpi-raw, lxi, hislip …
│  ├─ fieldbus/      # modbus, ethercat, profinet, ethernet-ip, canopen, opc-ua, bacnet …
│  ├─ wireless/      # ble-gatt, zigbee, z-wave, lorawan, thread, matter, nfc …
│  ├─ devops/        # local, docker-exec, k8s-api, winrm, redfish, ipmi, kafka, amqp …
│  └─ cloud/         # aws-iot, azure-iot, gcp-iot …
└─ drivers/
   ├─ sensors/       # temperature, pressure, gas, imu, light, current, adc …
   ├─ actuators/     # motor drivers, servos, relays, valves
   ├─ power/         # pmic, fuel gauge, charger, regulators
   ├─ io/            # expanders, muxes, gpio, digipots
   ├─ memory/        # eeprom, fram, flash, rtc
   ├─ display/       # oled, tft, char-lcd, led-matrix, addressable led
   ├─ instruments/   # scope, dmm, psu, load, awg, smu, sa, vna, daq
   ├─ programmers/   # jlink, stlink, openocd, esptool, dfu-util
   ├─ robotics/      # lidar, gps, imu-modules, servo-bus, mav, depth-cam
   ├─ industrial/    # plc, vfd, io-link, server-mgmt
   ├─ data/          # databases, brokers, object stores
   ├─ services/      # rest apis, ci/cd, observability, infra
   └─ cloud/         # aws, azure, gcp services
```

---

# Buses (74)

## Embedded / chip-level (22)

| Bus | `compatible` | Kind | Pri | Status | Notes |
|---|---|---|---|---|---|
| I²C | `shal,i2c-cli` | Byte | P0 | ✅ | argv over a CommandTransport; native variant planned |
| SPI | `shal,spi-cli` | Byte | P0 | ✅ | as above |
| UART / serial | `shal,serial` | Byte | P0 | 🟡 | pyserial; the most common bringup link |
| CAN / CAN-FD | `shal,can` | Message | P0 | 🟡 | python-can backends (socketcan, PCAN, Vector) |
| I3C | `shal,i3c` | Byte | P1 | ⬜ | I²C successor, in-band IRQ |
| 1-Wire | `shal,onewire` | Byte | P1 | ⬜ | DS18B20 et al; w1 kernel or DS2482 |
| SMBus | `shal,smbus` | Byte | P1 | ⬜ | I²C profile; battery/PMIC |
| PMBus | `shal,pmbus` | Byte | P1 | ⬜ | power-management over SMBus |
| LIN | `shal,lin` | Message | P1 | ⬜ | automotive low-speed |
| JTAG | `shal,jtag` | Command | P1 | ⬜ | boundary scan / debug |
| SWD | `shal,swd` | Command | P1 | ⬜ | ARM 2-wire debug |
| I²S | `shal,i2s` | Byte | P2 | ⬜ | digital audio |
| QSPI / OctoSPI | `shal,qspi` | Byte | P2 | ⬜ | external flash/PSRAM |
| eSPI | `shal,espi` | Byte | P2 | ⬜ | LPC successor |
| SENT | `shal,sent` | Message | P2 | ⬜ | automotive sensor |
| SDIO | `shal,sdio` | Byte | P2 | ⬜ | SD/eMMC/SDIO peripherals |
| MDIO | `shal,mdio` | Byte | P2 | ⬜ | Ethernet PHY mgmt |
| SWIM | `shal,swim` | Command | P2 | ⬜ | STM8 debug |
| UPDI | `shal,updi` | Command | P2 | ⬜ | new AVR programming |
| BDM | `shal,bdm` | Command | P2 | ⬜ | Freescale background debug |
| Parallel / FMC | `shal,parallel` | Byte | P2 | ⬜ | memory-mapped peripherals |
| PSI5 | `shal,psi5` | Message | P2 | ⬜ | automotive sensor interface |

## Host / board interconnect (7)

| Bus | `compatible` | Kind | Pri | Status | Notes |
|---|---|---|---|---|---|
| USB (libusb) | `shal,usb` | Message | P0 | ⬜ | control/bulk transfers |
| GPIO | `shal,gpio` | Byte | P0 | ⬜ | libgpiod / sysfs lines |
| HID | `shal,hid` | Message | P1 | ⬜ | hidapi devices |
| FTDI MPSSE | `shal,ftdi-mpsse` | Byte | P1 | ⬜ | USB→I²C/SPI/JTAG bridge |
| PCIe | `shal,pcie` | Byte | P1 | ⬜ | BAR / config-space access |
| SD/MMC | `shal,mmc` | Byte | P2 | ⬜ | block access |
| GPIB-USB | `shal,gpib-usb` | Message | P1 | ⬜ | see instruments too |

## Network / remote (14)

| Bus | `compatible` | Kind | Pri | Status | Notes |
|---|---|---|---|---|---|
| Local / subprocess | `shal,local` | Command | P0 | ✅ | run on this machine |
| SSH | `shal,ssh-host` | Command | P0 | ✅ | ControlMaster reuse; argv only |
| TCP socket | `shal,tcp` | Message | P0 | ✅ | TLS by default |
| HTTP / HTTPS | `shal,http` | Message | P0 | ✅ | REST services |
| MQTT | `shal,mqtt` | Stream | P0 | 🟡 | pub/sub; IoT default |
| UDP | `shal,udp` | Message | P1 | ⬜ | datagram devices |
| WebSocket | `shal,websocket` | Stream | P1 | ⬜ | bidirectional |
| gRPC | `shal,grpc` | Message | P1 | ⬜ | typed RPC services |
| SNMP | `shal,snmp` | Message | P1 | ⬜ | network gear / PDUs |
| Telnet | `shal,telnet` | Command | P2 | ⬜ | legacy instruments/switches |
| CoAP | `shal,coap` | Message | P2 | ⬜ | constrained IoT |
| AMQP | `shal,amqp` | Stream | P1 | ⬜ | RabbitMQ etc. |
| Redis RESP | `shal,resp` | Message | P1 | ⬜ | cache / pub-sub |
| WinRM | `shal,winrm` | Command | P1 | ⬜ | remote Windows |

## Instruments / lab (T&M) (7)

| Bus | `compatible` | Kind | Pri | Status | Notes |
|---|---|---|---|---|---|
| VISA (PyVISA) | `shal,visa` | Message | P0 | ⬜ | universal instrument backend |
| SCPI raw socket | `shal,scpi-raw` | Message | P0 | ⬜ | TCP :5025; no VISA needed |
| GPIB / IEEE-488 | `shal,gpib` | Message | P0 | ⬜ | classic bench bus |
| USBTMC | `shal,usbtmc` | Message | P1 | ⬜ | USB test-and-measurement |
| VXI-11 | `shal,vxi11` | Message | P1 | ⬜ | LAN instruments (legacy) |
| LXI | `shal,lxi` | Message | P1 | ⬜ | modern LAN instruments |
| HiSLIP | `shal,hislip` | Message | P2 | ⬜ | high-speed LAN protocol |

## Industrial / fieldbus (10)

| Bus | `compatible` | Kind | Pri | Status | Notes |
|---|---|---|---|---|---|
| Modbus TCP | `shal,modbus-tcp` | Message | P0 | 🟡 | pymodbus; most common fieldbus |
| Modbus RTU | `shal,modbus-rtu` | Message | P0 | 🟡 | serial variant |
| OPC-UA | `shal,opcua` | Message | P0 | ⬜ | factory data backbone |
| EtherCAT | `shal,ethercat` | Message | P1 | ⬜ | high-speed motion |
| EtherNet/IP | `shal,ethernet-ip` | Message | P1 | ⬜ | Rockwell ecosystem |
| PROFINET | `shal,profinet` | Message | P1 | ⬜ | Siemens ecosystem |
| CANopen | `shal,canopen` | Message | P1 | ⬜ | motion/drives over CAN |
| BACnet | `shal,bacnet` | Message | P1 | ⬜ | building automation |
| IO-Link | `shal,io-link` | Message | P1 | ⬜ | smart-sensor point-to-point |
| DNP3 | `shal,dnp3` | Message | P2 | ⬜ | utilities/SCADA |

## Wireless (6)

| Bus | `compatible` | Kind | Pri | Status | Notes |
|---|---|---|---|---|---|
| BLE GATT | `shal,ble` | Stream | P0 | ⬜ | bleak; sensors & wearables |
| Zigbee | `shal,zigbee` | Stream | P1 | ⬜ | mesh home/industrial |
| LoRaWAN | `shal,lorawan` | Message | P1 | ⬜ | long-range IoT |
| Z-Wave | `shal,zwave` | Stream | P1 | ⬜ | home automation |
| Thread | `shal,thread` | Message | P2 | ⬜ | low-power mesh |
| Matter | `shal,matter` | Message | P1 | ⬜ | cross-vendor smart home |

## DevOps / infra (8)

| Bus | `compatible` | Kind | Pri | Status | Notes |
|---|---|---|---|---|---|
| Docker exec | `shal,docker` | Command | P1 | ⬜ | run inside containers |
| Kubernetes API | `shal,k8s` | Message | P1 | ⬜ | exec/port-forward to pods |
| Redfish | `shal,redfish` | Message | P1 | ⬜ | modern server BMC |
| IPMI | `shal,ipmi` | Message | P1 | ⬜ | legacy server BMC |
| Kafka | `shal,kafka` | Stream | P1 | ⬜ | event streaming |
| NATS | `shal,nats` | Stream | P2 | ⬜ | lightweight messaging |
| NETCONF | `shal,netconf` | Message | P2 | ⬜ | network device config |
| Serial console server | `shal,console-server` | Command | P2 | ⬜ | port-per-device terminal |

---

# Drivers (271)

## Sensors — temperature & humidity (24)

> Most sensors are **P1/P2 for SHAL** regardless of popularity — the hobby-sensor
> space is well-served by CircuitPython/kernel and isn't SHAL's differentiator. A
> marquee few (`ti,tmp102`, `bosch,bme280`, `ti,ina219`) stay **P0** as the
> 2-minute onboarding / demo path.

| Device | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| TI TMP102 | `ti,tmp102` | TemperatureSensor | P0 | ✅ shipped; the canonical first driver |
| TI TMP117 | `ti,tmp117` | TemperatureSensor | P1 | high-accuracy |
| Maxim DS18B20 | `maxim,ds18b20` | TemperatureSensor | P1 | 1-Wire; hobby staple (popular, not beachhead) |
| NXP/TI LM75 | `nxp,lm75` | TemperatureSensor | P1 | ubiquitous clone target |
| Microchip MCP9808 | `microchip,mcp9808` | TemperatureSensor | P1 | ±0.25 °C |
| Maxim MAX31855 | `maxim,max31855` | TemperatureSensor | P1 | thermocouple amp (SPI) |
| Maxim MAX6675 | `maxim,max6675` | TemperatureSensor | P1 | K-type thermocouple |
| Sensirion SHT31 | `sensirion,sht31` | HumiditySensor | P1 | temp+RH reference part |
| Sensirion SHT40 | `sensirion,sht40` | HumiditySensor | P1 | newer gen |
| Silabs Si7021 | `silabs,si7021` | HumiditySensor | P1 | common temp+RH |
| Bosch BME280 | `bosch,bme280` | EnvironmentSensor | P0 | temp/RH/pressure |
| Bosch BME680 | `bosch,bme680` | EnvironmentSensor | P1 | adds gas/IAQ |
| Bosch BMP280 | `bosch,bmp280` | PressureSensor | P0 | temp+pressure |
| Melexis MLX90614 | `melexis,mlx90614` | TemperatureSensor | P1 | IR non-contact |
| Melexis MLX90640 | `melexis,mlx90640` | ThermalCamera | P2 | 32×24 thermal array |
| ADI ADT7410 | `adi,adt7410` | TemperatureSensor | P2 | 16-bit |
| ST STTS751 | `st,stts751` | TemperatureSensor | P2 | compact |
| TI TMP36 | `ti,tmp36` | TemperatureSensor | P2 | analog (needs ADC) |
| Aosong DHT22 | `aosong,dht22` | HumiditySensor | P1 | cheap temp+RH |
| Aosong DHT11 | `aosong,dht11` | HumiditySensor | P2 | entry-level |
| Sensirion SCD30 | `sensirion,scd30` | CO2Sensor | P1 | NDIR CO₂ |
| Sensirion SCD41 | `sensirion,scd41` | CO2Sensor | P1 | photoacoustic CO₂ |
| TE MS5611 | `te,ms5611` | PressureSensor | P2 | altimeter-grade |
| Infineon DPS310 | `infineon,dps310` | PressureSensor | P2 | barometric |

## Sensors — motion / IMU (14)

| Device | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| InvenSense MPU6050 | `invensense,mpu6050` | IMU | P1 | 6-axis; hobby staple |
| InvenSense MPU9250 | `invensense,mpu9250` | IMU | P1 | 9-axis |
| InvenSense ICM-20948 | `invensense,icm20948` | IMU | P1 | 9-axis, low power |
| InvenSense ICM-42688 | `invensense,icm42688` | IMU | P1 | high-perf 6-axis |
| Bosch BNO055 | `bosch,bno055` | OrientationSensor | P0 | sensor-fusion output |
| Bosch BMI160 | `bosch,bmi160` | IMU | P1 | low-power 6-axis |
| Bosch BMI270 | `bosch,bmi270` | IMU | P1 | wearables |
| ST LSM6DSOX | `st,lsm6dsox` | IMU | P1 | 6-axis + ML core |
| ST LSM9DS1 | `st,lsm9ds1` | IMU | P1 | 9-axis |
| ST LIS3DH | `st,lis3dh` | Accelerometer | P1 | 3-axis accel |
| ADI ADXL345 | `adi,adxl345` | Accelerometer | P1 | classic 3-axis |
| ADI ADXL355 | `adi,adxl355` | Accelerometer | P2 | low-noise |
| NXP FXOS8700 | `nxp,fxos8700` | IMU | P2 | accel+mag |
| AMS AS5600 | `ams,as5600` | AngleSensor | P1 | magnetic rotary encoder |

## Sensors — light / proximity / distance (8)

| Device | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| AMS TSL2561 | `ams,tsl2561` | LightSensor | P1 | lux |
| AMS TSL2591 | `ams,tsl2591` | LightSensor | P1 | high dynamic range |
| Rohm BH1750 | `rohm,bh1750` | LightSensor | P1 | cheap lux |
| Vishay VEML7700 | `vishay,veml7700` | LightSensor | P2 | ambient light |
| ST VL53L0X | `st,vl53l0x` | DistanceSensor | P1 | ToF, popular |
| ST VL53L1X | `st,vl53l1x` | DistanceSensor | P1 | longer range ToF |
| AMS APDS9960 | `ams,apds9960` | GestureSensor | P2 | gesture/color/proximity |
| Sharp GP2Y0A | `sharp,gp2y0a` | DistanceSensor | P2 | analog IR |

## Sensors — gas / air quality (6)

| Device | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| Sensirion SGP30 | `sensirion,sgp30` | GasSensor | P1 | VOC/eCO₂ |
| Sensirion SGP40 | `sensirion,sgp40` | GasSensor | P1 | VOC index |
| AMS CCS811 | `ams,ccs811` | GasSensor | P1 | eCO₂/TVOC |
| ScioSense ENS160 | `sciosense,ens160` | GasSensor | P2 | air quality |
| Plantower PMS5003 | `plantower,pms5003` | ParticulateSensor | P1 | PM2.5 |
| Figaro TGS | `figaro,tgs` | GasSensor | P2 | MOX gas |

## Sensors — current / power / ADC / DAC (18)

| Device | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| TI INA219 | `ti,ina219` | PowerMonitor | P0 | ✅ shipped; I²C current/power; very common |
| TI INA226 | `ti,ina226` | PowerMonitor | P1 | higher precision |
| TI INA260 | `ti,ina260` | PowerMonitor | P1 | integrated shunt |
| TI INA3221 | `ti,ina3221` | PowerMonitor | P2 | 3-channel |
| Allegro ACS712 | `allegro,acs712` | CurrentSensor | P1 | hall current (analog) |
| TI ADS1115 | `ti,ads1115` | ADC | P0 | 16-bit 4-ch; ubiquitous |
| TI ADS1015 | `ti,ads1015` | ADC | P1 | 12-bit |
| Microchip MCP3008 | `microchip,mcp3008` | ADC | P0 | 8-ch SPI; Pi staple |
| Microchip MCP3208 | `microchip,mcp3208` | ADC | P1 | 12-bit |
| TI ADS131M | `ti,ads131m` | ADC | P2 | precision delta-sigma |
| TI ADS8688 | `ti,ads8688` | ADC | P2 | 8-ch 16-bit |
| Microchip MCP4725 | `microchip,mcp4725` | DAC | P1 | 12-bit I²C DAC |
| ADI AD5693 | `adi,ad5693` | DAC | P2 | 16-bit |
| TI DAC8568 | `ti,dac8568` | DAC | P2 | 8-ch |
| TI ADS1256 | `ti,ads1256` | ADC | P2 | 24-bit |
| Microchip PAC1934 | `microchip,pac1934` | PowerMonitor | P2 | 4-ch energy |
| Avia HX711 | `avia,hx711` | LoadCell | P1 | load-cell ADC; scales |
| Nau7802 | `nuvoton,nau7802` | LoadCell | P2 | 24-bit bridge |

## I/O expanders, muxes, digipots (8)

| Device | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| NXP PCA9548 | `nxp,pca9548` | I2CMux | P0 | ✅ shipped mux (8-ch) |
| Microchip MCP23017 | `microchip,mcp23017` | GPIOExpander | P0 | 16-bit I²C; very common |
| Microchip MCP23008 | `microchip,mcp23008` | GPIOExpander | P1 | 8-bit |
| NXP PCF8574 | `nxp,pcf8574` | GPIOExpander | P1 | 8-bit; LCD backpacks |
| NXP PCA9555 | `nxp,pca9555` | GPIOExpander | P2 | 16-bit |
| TI TCA6416 | `ti,tca6416` | GPIOExpander | P2 | 16-bit |
| TI TCA9548A | `ti,tca9548a` | I2CMux | P1 | PCA9548 equivalent |
| Microchip MCP4131 | `microchip,mcp4131` | DigitalPot | P2 | SPI digipot |

## Actuators — motor drivers & servos (16)

| Device | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| NXP PCA9685 | `nxp,pca9685` | PWMController | P0 | 16-ch servo/LED PWM |
| Allegro A4988 | `allegro,a4988` | StepperDriver | P0 | classic step/dir |
| TI DRV8825 | `ti,drv8825` | StepperDriver | P1 | higher current |
| Trinamic TMC2209 | `trinamic,tmc2209` | StepperDriver | P0 | silent; 3D-printer default |
| Trinamic TMC5160 | `trinamic,tmc5160` | StepperDriver | P1 | high power, motion ctrl |
| ST L298N | `st,l298n` | MotorController | P1 | dual H-bridge |
| ST L6470 | `st,l6470` | StepperDriver | P2 | SPI dSPIN |
| TI DRV8833 | `ti,drv8833` | MotorController | P1 | dual H-bridge, low V |
| Infineon BTS7960 | `infineon,bts7960` | MotorController | P2 | 43 A H-bridge |
| VESC | `vesc,motor` | MotorController | P1 | open BLDC controller |
| DSHOT ESC | `generic,dshot-esc` | ESC | P1 | drone motor protocol |
| Robotis Dynamixel | `robotis,dynamixel` | ServoBus | P1 | smart serial servos |
| Adafruit Motor Shield | `adafruit,motorshield` | MotorController | P2 | PCA9685-based |
| Pololu Tic | `pololu,tic` | StepperDriver | P2 | USB/serial stepper |
| Generic relay | `generic,relay` | Relay | P0 | GPIO/expander coil |
| SainSmart 8-relay | `sainsmart,relay8` | Relay | P2 | module board |

## Memory & RTC (10)

| Device | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| Maxim DS3231 | `maxim,ds3231` | RTC | P1 | TCXO RTC; very common |
| Maxim DS1307 | `maxim,ds1307` | RTC | P1 | basic RTC |
| NXP PCF8523 | `nxp,pcf8523` | RTC | P2 | low power |
| NXP PCF8563 | `nxp,pcf8563` | RTC | P2 | common clone |
| Microchip 24LC256 | `microchip,24lc256` | EEPROM | P1 | I²C EEPROM |
| Microchip AT24C32 | `microchip,at24c32` | EEPROM | P1 | pairs with DS3231 |
| Cypress FM24 | `cypress,fm24` | FRAM | P2 | nonvolatile RAM |
| Winbond W25Q | `winbond,w25q` | SPIFlash | P1 | NOR flash; ESP/RP2040 |
| Micron MT25Q | `micron,mt25q` | SPIFlash | P2 | high density |
| SD card (SPI) | `generic,sdcard-spi` | BlockStorage | P2 | FAT logging |

## Displays & LEDs (12)

| Device | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| Solomon SSD1306 | `solomon,ssd1306` | Display | P1 | 128×64 OLED; everywhere |
| Solomon SSD1331 | `solomon,ssd1331` | Display | P2 | color OLED |
| Sitronix ST7789 | `sitronix,st7789` | Display | P1 | IPS TFT |
| Sitronix ST7735 | `sitronix,st7735` | Display | P1 | small TFT |
| Ilitek ILI9341 | `ilitek,ili9341` | Display | P1 | 320×240 TFT |
| Hitachi HD44780 | `hitachi,hd44780` | CharDisplay | P0 | 16×2 char LCD |
| Maxim MAX7219 | `maxim,max7219` | LEDMatrix | P1 | 7-seg / 8×8 matrix |
| WS2812 / NeoPixel | `worldsemi,ws2812` | AddressableLED | P1 | addressable RGB |
| SK6812 | `opsco,sk6812` | AddressableLED | P1 | RGBW variant |
| APA102 / DotStar | `apa,apa102` | AddressableLED | P1 | SPI addressable |
| TM1637 | `titan,tm1637` | LEDDisplay | P2 | 4-digit 7-seg |
| Nokia 5110 (PCD8544) | `philips,pcd8544` | Display | P2 | retro LCD |

## Power ICs — PMIC / fuel gauge / charger (8)

| Device | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| Maxim MAX17048 | `maxim,max17048` | FuelGauge | P1 | LiPo SoC gauge |
| TI BQ27441 | `ti,bq27441` | FuelGauge | P1 | impedance-track gauge |
| TI BQ24295 | `ti,bq24295` | BatteryCharger | P1 | I²C charger |
| TI TPS65217 | `ti,tps65217` | PMIC | P2 | SBC PMIC (BeagleBone) |
| Maxim MAX77650 | `maxim,max77650` | PMIC | P2 | wearable PMIC |
| ADI LTC2941 | `adi,ltc2941` | CoulombCounter | P2 | battery gauge |
| TI INA228 | `ti,ina228` | PowerMonitor | P2 | 20-bit energy |
| Maxim DS2438 | `maxim,ds2438` | BatteryMonitor | P2 | 1-Wire |

## Programmers & debug tools (9)

| Device | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| SEGGER J-Link | `segger,jlink` | DebugProbe | P0 | flash/debug via JLinkExe |
| ST-Link | `st,stlink` | DebugProbe | P0 | STM32 flash/debug |
| OpenOCD target | `openocd,target` | DebugProbe | P1 | generic JTAG/SWD |
| Espressif esptool | `espressif,esptool` | Flasher | P0 | ESP32/ESP8266 flashing |
| avrdude | `atmel,avrdude` | Flasher | P1 | AVR programming |
| dfu-util | `generic,dfu-util` | Flasher | P1 | USB DFU |
| Nordic nrfjprog | `nordic,nrfjprog` | Flasher | P1 | nRF5x |
| Bus Pirate | `dangerous,buspirate` | BusBridge | P2 | multi-protocol probe |
| Black Magic Probe | `blacksphere,bmp` | DebugProbe | P2 | GDB-native |

## Test & measurement instruments (56)

> SCPI-class; reached over `visa`, `scpi-raw`, `gpib`, or `usbtmc`.

### Oscilloscopes (12)

| Device | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| Keysight DSOX1000 | `keysight,dsox1000` | Oscilloscope | P1 | entry InfiniiVision |
| Keysight DSOX3000 | `keysight,dsox3000` | Oscilloscope | P1 | mid-range |
| Keysight Infiniium | `keysight,infiniium` | Oscilloscope | P2 | high-end |
| Tektronix MSO5 | `tektronix,mso5` | Oscilloscope | P1 | mixed-signal |
| Tektronix DPO4000 | `tektronix,dpo4000` | Oscilloscope | P1 | popular bench |
| Tektronix TBS1000 | `tektronix,tbs1000` | Oscilloscope | P2 | entry |
| Rigol DS1000Z | `rigol,ds1000z` | Oscilloscope | P0 | hobby/lab favorite |
| Rigol MSO5000 | `rigol,mso5000` | Oscilloscope | P1 | value MSO |
| Siglent SDS1000X-E | `siglent,sds1000xe` | Oscilloscope | P0 | high value |
| Siglent SDS2000X | `siglent,sds2000x` | Oscilloscope | P1 | mid-range |
| R&S RTB2000 | `rohde-schwarz,rtb2000` | Oscilloscope | P2 | 10-bit |
| LeCroy WaveSurfer | `lecroy,wavesurfer` | Oscilloscope | P2 | — |

### Digital multimeters (7)

| Device | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| Keysight 34461A | `keysight,34461a` | DigitalMultimeter | P0 | 6½-digit bench standard |
| Keysight 34470A | `keysight,34470a` | DigitalMultimeter | P1 | 7½-digit |
| Keithley DMM6500 | `keithley,dmm6500` | DigitalMultimeter | P1 | touchscreen 6½ |
| Keithley 2000 | `keithley,2000` | DigitalMultimeter | P1 | long-running classic |
| Fluke 8845A | `fluke,8845a` | DigitalMultimeter | P1 | precision bench |
| Rigol DM3068 | `rigol,dm3068` | DigitalMultimeter | P1 | 6½-digit value |
| Siglent SDM3045X | `siglent,sdm3045x` | DigitalMultimeter | P1 | 4½-digit |

### Power supplies (9)

| Device | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| Keysight E36312A | `keysight,e36312a` | PowerSupply | P0 | triple-output bench |
| Keysight E3631A | `keysight,e3631a` | PowerSupply | P1 | classic triple |
| Rigol DP832 | `rigol,dp832` | PowerSupply | P0 | popular triple |
| Rigol DP712 | `rigol,dp712` | PowerSupply | P1 | single high-power |
| Keithley 2230 | `keithley,2230` | PowerSupply | P1 | triple |
| Siglent SPD3303 | `siglent,spd3303` | PowerSupply | P1 | value triple |
| R&S NGP800 | `rohde-schwarz,ngp800` | PowerSupply | P2 | multi-channel |
| TDK-Lambda Genesys | `tdk-lambda,genesys` | PowerSupply | P2 | high-power rack |
| BK Precision 9100 | `bk-precision,9100` | PowerSupply | P2 | — |

### Electronic loads (4)

| Device | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| Keysight EL34243A | `keysight,el34243a` | ElectronicLoad | P1 | dual input |
| Rigol DL3021 | `rigol,dl3021` | ElectronicLoad | P1 | value DC load |
| Siglent SDL1020 | `siglent,sdl1020` | ElectronicLoad | P0 | programmable; build-first DC load |
| BK Precision 8600 | `bk-precision,8600` | ElectronicLoad | P2 | — |

### Function / arbitrary generators (6)

| Device | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| Keysight 33500B | `keysight,33500b` | FunctionGenerator | P0 | TrueForm AWG |
| Keysight 33220A | `keysight,33220a` | FunctionGenerator | P1 | classic 20 MHz |
| Rigol DG1000Z | `rigol,dg1000z` | FunctionGenerator | P0 | value AWG |
| Rigol DG4000 | `rigol,dg4000` | FunctionGenerator | P1 | 4-ch |
| Siglent SDG2000X | `siglent,sdg2000x` | FunctionGenerator | P1 | — |
| Tektronix AFG31000 | `tektronix,afg31000` | FunctionGenerator | P2 | touchscreen |

### Source measure units (5)

| Device | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| Keithley 2400 | `keithley,2400` | SourceMeter | P0 | the SMU standard |
| Keithley 2450 | `keithley,2450` | SourceMeter | P1 | touchscreen successor |
| Keithley 2600B | `keithley,2600b` | SourceMeter | P1 | dual-channel TSP |
| Keysight B2902B | `keysight,b2902b` | SourceMeter | P1 | precision SMU |
| Keysight B2961A | `keysight,b2961a` | SourceMeter | P2 | low-noise |

### Spectrum / network / other (13)

| Device | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| Keysight N9000 CXA | `keysight,n9000` | SpectrumAnalyzer | P1 | — |
| Rigol DSA800 | `rigol,dsa800` | SpectrumAnalyzer | P0 | value SA; build-first |
| Siglent SSA3000X | `siglent,ssa3000x` | SpectrumAnalyzer | P1 | — |
| R&S FSV | `rohde-schwarz,fsv` | SpectrumAnalyzer | P2 | high-end |
| Keysight E5071C | `keysight,e5071c` | NetworkAnalyzer | P2 | VNA |
| NanoVNA | `nanovna,nanovna` | NetworkAnalyzer | P1 | low-cost VNA |
| Keysight E4980A | `keysight,e4980a` | LCRMeter | P2 | precision LCR |
| Hioki IM3536 | `hioki,im3536` | LCRMeter | P2 | — |
| Keysight 53220A | `keysight,53220a` | FrequencyCounter | P2 | universal counter |
| Keysight DAQ970A | `keysight,daq970a` | DataAcquisition | P1 | switch/measure |
| Keysight 34970A | `keysight,34970a` | DataAcquisition | P1 | legacy DAQ |
| NI DAQmx | `ni,daqmx` | DataAcquisition | P0 | LabVIEW ecosystem |
| Yokogawa WT300 | `yokogawa,wt300` | PowerAnalyzer | P2 | power measurement |

## Robotics (12)

| Device | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| Slamtec RPLIDAR | `slamtec,rplidar` | Lidar | P1 | hobby/SLAM lidar |
| Velodyne VLP-16 | `velodyne,vlp16` | Lidar | P2 | 3D lidar |
| Ouster OS1 | `ouster,os1` | Lidar | P2 | digital lidar |
| u-blox NEO-M8 | `ublox,neo-m8` | GNSS | P0 | popular GPS |
| u-blox ZED-F9P | `ublox,zed-f9p` | GNSS | P1 | RTK cm-accuracy |
| Intel RealSense | `intel,realsense` | DepthCamera | P1 | RGB-D |
| PX4 / MAVLink | `px4,mavlink` | FlightController | P1 | drones |
| ArduPilot | `ardupilot,mavlink` | FlightController | P1 | drones/rovers |
| ROS 2 node | `ros,node` | ROSBridge | P1 | topic/service bridge |
| Robotis OpenCR | `robotis,opencr` | RobotController | P2 | TurtleBot |
| Adafruit Crickit | `adafruit,crickit` | RobotController | P2 | maker robotics |
| YDLIDAR | `ydlidar,x4` | Lidar | P2 | low-cost lidar |

## Industrial devices (14)

| Device | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| Siemens S7-1200 | `siemens,s7-1200` | PLC | P0 | dominant PLC |
| Siemens S7-1500 | `siemens,s7-1500` | PLC | P1 | high-end |
| Allen-Bradley CompactLogix | `allen-bradley,compactlogix` | PLC | P0 | Rockwell ecosystem |
| Schneider Modicon | `schneider,modicon` | PLC | P1 | Modbus-native |
| Beckhoff EtherCAT terminals | `beckhoff,el-terminals` | IOModule | P1 | EtherCAT I/O |
| WAGO 750 | `wago,750` | IOModule | P1 | fieldbus I/O |
| ABB ACS880 | `abb,acs880` | VFD | P1 | motor drive |
| Danfoss VLT | `danfoss,vlt` | VFD | P2 | motor drive |
| IFM IO-Link master | `ifm,io-link-master` | IOLinkMaster | P1 | smart sensor hub |
| Balluff IO-Link | `balluff,io-link` | IOLinkMaster | P2 | — |
| SICK laser scanner | `sick,laser-scanner` | SafetyScanner | P2 | safety/measurement |
| Festo CPX | `festo,cpx` | ValveTerminal | P2 | pneumatics |
| Generic Modbus relay | `generic,modbus-relay` | Relay | P1 | common industrial relay |
| Generic Modbus meter | `generic,modbus-meter` | EnergyMeter | P1 | power metering |

## Server / infra management (10)

| Device | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| Redfish BMC | `dmtf,redfish` | ServerManagement | P0 | modern standard |
| IPMI BMC | `ipmi,bmc` | ServerManagement | P1 | legacy standard |
| Dell iDRAC | `dell,idrac` | ServerManagement | P1 | Redfish + extras |
| HPE iLO | `hpe,ilo` | ServerManagement | P1 | — |
| Lenovo XCC | `lenovo,xcc` | ServerManagement | P2 | — |
| APC PDU | `apc,pdu` | PowerDistribution | P1 | switched PDU (SNMP) |
| Raritan PDU | `raritan,pdu` | PowerDistribution | P2 | — |
| ServerTech PDU | `servertech,pdu` | PowerDistribution | P2 | — |
| Cisco IOS switch | `cisco,ios` | NetworkSwitch | P1 | SSH/NETCONF |
| Arista EOS switch | `arista,eos` | NetworkSwitch | P2 | API-first |

## Data — databases (12)

| Service | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| PostgreSQL | `postgres,db` | RelationalDB | P0 | most-loved RDBMS |
| MySQL | `mysql,db` | RelationalDB | P0 | ubiquitous |
| MariaDB | `mariadb,db` | RelationalDB | P1 | MySQL fork |
| SQLite | `sqlite,db` | RelationalDB | P1 | embedded |
| Redis | `redis,db` | KeyValueStore | P0 | cache/store |
| MongoDB | `mongodb,db` | DocumentStore | P0 | document DB |
| InfluxDB | `influxdb,db` | TimeSeriesDB | P0 | metrics/IoT time series |
| TimescaleDB | `timescale,db` | TimeSeriesDB | P1 | PG time-series |
| ClickHouse | `clickhouse,db` | AnalyticsDB | P1 | OLAP |
| Elasticsearch | `elastic,search` | SearchStore | P1 | logs/search |
| Cassandra | `cassandra,db` | WideColumnStore | P2 | distributed |
| CockroachDB | `cockroach,db` | RelationalDB | P2 | distributed SQL |

## Data — brokers & object storage (11)

| Service | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| Apache Kafka | `kafka,broker` | MessageBroker | P0 | event streaming |
| RabbitMQ | `rabbitmq,broker` | MessageBroker | P0 | AMQP queues |
| NATS | `nats,broker` | MessageBroker | P1 | lightweight |
| MQTT broker | `mqtt,broker` | MessageBroker | P0 | IoT pub/sub |
| Apache Pulsar | `pulsar,broker` | MessageBroker | P2 | streaming |
| ZeroMQ | `zeromq,socket` | MessageBroker | P2 | brokerless |
| AWS S3 | `aws,s3` | ObjectStore | P0 | cloud object store |
| MinIO | `minio,s3` | ObjectStore | P1 | self-hosted S3 |
| Google Cloud Storage | `gcp,gcs` | ObjectStore | P1 | — |
| Azure Blob | `azure,blob` | ObjectStore | P1 | — |
| SFTP server | `generic,sftp` | FileStore | P1 | file transfer |

## Services — observability & CI/CD & infra (16)

| Service | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| Prometheus | `prometheus,tsdb` | MetricsStore | P0 | metrics standard |
| Grafana | `grafana,api` | Dashboards | P1 | dashboards/alerts |
| Loki | `loki,logs` | LogStore | P1 | log aggregation |
| Jaeger | `jaeger,tracing` | TraceStore | P2 | distributed tracing |
| OpenTelemetry Collector | `otel,collector` | Telemetry | P1 | pipeline |
| Datadog | `datadog,api` | Observability | P1 | SaaS APM |
| GitHub | `github,api` | SCM | P0 | repos/PRs/actions |
| GitLab | `gitlab,api` | SCM | P1 | repos/CI |
| Jenkins | `jenkins,api` | CIServer | P1 | build server |
| Argo CD | `argocd,api` | GitOps | P2 | k8s delivery |
| HashiCorp Vault | `hashicorp,vault` | SecretStore | P1 | secrets |
| HashiCorp Consul | `hashicorp,consul` | ServiceDiscovery | P2 | discovery/KV |
| etcd | `etcd,kv` | KeyValueStore | P2 | k8s backing store |
| Docker Engine | `docker,engine` | ContainerRuntime | P1 | containers |
| Kubernetes | `k8s,cluster` | Orchestrator | P0 | container orchestration |
| Ansible host | `ansible,host` | ConfigManagement | P2 | provisioning target |

## Cloud services (7)

| Service | `compatible` | Capability | Pri | Notes |
|---|---|---|---|---|
| AWS IoT Core | `aws,iot-core` | IoTPlatform | P1 | device cloud (MQTT) |
| AWS Lambda | `aws,lambda` | FunctionInvoke | P1 | serverless |
| AWS DynamoDB | `aws,dynamodb` | KeyValueStore | P1 | managed NoSQL |
| Azure IoT Hub | `azure,iot-hub` | IoTPlatform | P1 | device cloud |
| GCP Pub/Sub | `gcp,pubsub` | MessageBroker | P1 | managed messaging |
| GCP IoT (legacy) | `gcp,iot` | IoTPlatform | P2 | — |
| Twilio | `twilio,api` | Notification | P2 | SMS/voice alerts |

---

## Prioritization methodology

Priorities follow a **marketing lens, not a popularity contest**:

- **Beachhead first (Theory of Constraints).** Win one user — AI-agent builders +
  validation/test engineers — before broadening. Their stack (instruments, power,
  DUTs, software services) ranks above the general long tail.
- **Differentiation over coverage.** SHAL wins on the HW+SW, agent-native wedge, so
  instruments and software services that *showcase* it outrank parts already
  well-served elsewhere (the hobby-sensor space is owned by CircuitPython/kernel).
- **Leverage / flywheel.** Parts that unlock many devices (muxes, I/O expanders,
  ADCs) or power the first demo rank up — each landed user becomes a case study and
  a future driver contributor.
- **Pareto (80/20).** The Build order near the top is the vital 20% that covers most
  beachhead use cases; everything else sequences after it.
- **Quick win / activation.** A few cheap, sim-able sensors stay P0 purely as the
  2-minute onboarding path.

Raw adoption data (CircuitPython / PyVISA / pymodbus stars, bench & PLC market
share) is an *input*, not the ranking — it orders items within a tier. Re-rank with
better data via a PR against this file.
