---
type: reference
owner: repo-agent
scope: repo/shal
reviewed: 2026-09-27
---

# SHAL Bus & Driver Catalog

Where a driver for your device belongs, and what to name it: the `compatible` id
and the domain library folder for each bus and device, so two authors never pick
the same name. Look your device up, claim its name, then build it from the SDK
(`shal docs`).

> **A row is a reserved name, not shipped API.** SHAL core ships the framework
> and its `shal,*` buses; device drivers are written by their users and the
> community. Only items marked ✅ exist today.
>
> **No vendor device driver ships registered** (D1, re-affirmed #110; #149). The
> only registered devices are the simulated `shal,sim-sensor`, `shal,sim-psu` and
> `shal,sim-dmm`.
> A ✅ vendor device driver is either an **ADK reference** — shipped inside the Authoring Kit as guide
> material, printed by `shal docs --example <name>` — or a **repo example** under
> `examples/drivers/`. Either runs by naming its files with `--drivers` (D5).

> **Decision (#102, CMO ruling 2026-09-26):** this page is a name registry. Its
> priorities, build waves and counts were removed; the `shal-build-driver` and
> `shal-build-bus` skills send authors here to pick and claim a name. **#57** (a
> governed `shal.standards` capability registry) was closed on 2026-09-26
> without being built, so this file stays the place names are claimed, and
> capability names below are proposals until a Protocol for them exists in
> `shal.capabilities`.

---

## How to read this

**Status** — ✅ shipped · ○ name reserved, nobody has built it. Bus tables carry
the mark in the Status column; in driver tables a ✅ opens the Notes, and a row
without one is ○.

**`compatible`** — the YAML `driver:` id, `vendor,part` (hardware) or
`shal,<name>` / `vendor,<service>` (software).

**Capability** — the Protocol a driver implements. `TemperatureSensor`,
`PowerMonitor`, `PowerSupply`, `DigitalMultimeter`, `ADC`, `GPIOExpander` and
`MediaPlayer` exist in `shal.capabilities`; the rest are proposed names.

## Claim a name

1. Find your bus or device in the tables below. If its row is ○, the name is
   free: use that `compatible` and put the code in that section's domain folder
   (see the layout below).
2. If it is not listed, pick a `compatible` in the same form (`vendor,part`,
   lowercase, the part number without package suffixes) and the closest domain
   folder.
3. Open a PR against this file that adds the row, or writes "claimed — #<PR or
   issue>" in the Notes of an existing ○ row. Once it merges, the name is yours.

---

## Proposed library layout

Domain sub-packages, not a flat dir. Drivers are discovered by entry point
(`shal.drivers`) or `--drivers` regardless of folder — the layout is for humans.
Use it inside your own package, or a separate `shal-contrib-*` distribution for a
large or third-party family.

```text
your_package/
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

# Buses

## Embedded / chip-level (22)

| Bus | `compatible` | Kind | Status | Notes |
|---|---|---|---|---|
| I²C | `shal,i2c-cli` | Byte | ✅ | argv over a CommandTransport |
| SPI | `shal,spi-cli` | Byte | ✅ | as above |
| Simulated I²C | `shal,sim-i2c` | Byte | ✅ | simulated bus; no hardware |
| UART / serial | `shal,serial` | Byte | ○ | pyserial; the most common bringup link |
| CAN / CAN-FD | `shal,can` | Message | ○ | python-can backends (socketcan, PCAN, Vector) |
| I3C | `shal,i3c` | Byte | ○ | I²C successor, in-band IRQ |
| 1-Wire | `shal,onewire` | Byte | ○ | DS18B20 et al; w1 kernel or DS2482 |
| SMBus | `shal,smbus` | Byte | ○ | I²C profile; battery/PMIC |
| PMBus | `shal,pmbus` | Byte | ○ | power-management over SMBus |
| LIN | `shal,lin` | Message | ○ | automotive low-speed |
| JTAG | `shal,jtag` | Command | ○ | boundary scan / debug |
| SWD | `shal,swd` | Command | ○ | ARM 2-wire debug |
| I²S | `shal,i2s` | Byte | ○ | digital audio |
| QSPI / OctoSPI | `shal,qspi` | Byte | ○ | external flash/PSRAM |
| eSPI | `shal,espi` | Byte | ○ | LPC successor |
| SENT | `shal,sent` | Message | ○ | automotive sensor |
| SDIO | `shal,sdio` | Byte | ○ | SD/eMMC/SDIO peripherals |
| MDIO | `shal,mdio` | Byte | ○ | Ethernet PHY mgmt |
| SWIM | `shal,swim` | Command | ○ | STM8 debug |
| UPDI | `shal,updi` | Command | ○ | new AVR programming |
| BDM | `shal,bdm` | Command | ○ | Freescale background debug |
| Parallel / FMC | `shal,parallel` | Byte | ○ | memory-mapped peripherals |
| PSI5 | `shal,psi5` | Message | ○ | automotive sensor interface |

## Host / board interconnect (7)

| Bus | `compatible` | Kind | Status | Notes |
|---|---|---|---|---|
| USB (libusb) | `shal,usb` | Message | ○ | control/bulk transfers |
| GPIO | `shal,gpio` | Byte | ○ | libgpiod / sysfs lines |
| HID | `shal,hid` | Message | ○ | hidapi devices |
| FTDI MPSSE | `shal,ftdi-mpsse` | Byte | ○ | USB→I²C/SPI/JTAG bridge |
| PCIe | `shal,pcie` | Byte | ○ | BAR / config-space access |
| SD/MMC | `shal,mmc` | Byte | ○ | block access |
| GPIB-USB | `shal,gpib-usb` | Message | ○ | see instruments too |

## Network / remote (14)

| Bus | `compatible` | Kind | Status | Notes |
|---|---|---|---|---|
| Local / subprocess | `shal,local` | Command | ✅ | run on this machine |
| SSH | `shal,ssh-host` | Command | ✅ | ControlMaster reuse; argv only |
| TCP socket | `shal,tcp` | Message | ✅ | TLS by default |
| Simulated message bus | `shal,sim-msg` | Message | ✅ | simulated; no network |
| HTTP / HTTPS | `shal,http` | Message | ✅ | REST services — plain JSON POST, or the request envelope (`GET`, query, path; credentials in bus `config.headers`) |
| MQTT | `shal,mqtt` | Stream | ○ | pub/sub; IoT default |
| UDP | `shal,udp` | Message | ○ | datagram devices |
| WebSocket | `shal,websocket` | Stream | ○ | bidirectional |
| gRPC | `shal,grpc` | Message | ○ | typed RPC services |
| SNMP | `shal,snmp` | Message | ○ | network gear / PDUs |
| Telnet | `shal,telnet` | Command | ○ | legacy instruments/switches |
| CoAP | `shal,coap` | Message | ○ | constrained IoT |
| AMQP | `shal,amqp` | Stream | ○ | RabbitMQ etc. |
| Redis RESP | `shal,resp` | Message | ○ | cache / pub-sub |
| WinRM | `shal,winrm` | Command | ○ | remote Windows |

## Instruments / lab (T&M) (7)

| Bus | `compatible` | Kind | Status | Notes |
|---|---|---|---|---|
| VISA (PyVISA) | `shal,visa` | Message | ○ | universal instrument backend |
| SCPI raw socket | `shal,scpi-raw` | Message | ✅ | TCP :5025; no VISA needed |
| Simulated SCPI | `shal,sim-scpi` | Message | ✅ | simulated instrument bus; no hardware |
| GPIB / IEEE-488 | `shal,gpib` | Message | ○ | classic bench bus |
| USBTMC | `shal,usbtmc` | Message | ○ | USB test-and-measurement |
| VXI-11 | `shal,vxi11` | Message | ○ | LAN instruments (legacy) |
| LXI | `shal,lxi` | Message | ○ | modern LAN instruments |
| HiSLIP | `shal,hislip` | Message | ○ | high-speed LAN protocol |

## Industrial / fieldbus (10)

| Bus | `compatible` | Kind | Status | Notes |
|---|---|---|---|---|
| Modbus TCP | `shal,modbus-tcp` | Message | ○ | pymodbus; most common fieldbus |
| Modbus RTU | `shal,modbus-rtu` | Message | ○ | serial variant |
| OPC-UA | `shal,opcua` | Message | ○ | factory data backbone |
| EtherCAT | `shal,ethercat` | Message | ○ | high-speed motion |
| EtherNet/IP | `shal,ethernet-ip` | Message | ○ | Rockwell ecosystem |
| PROFINET | `shal,profinet` | Message | ○ | Siemens ecosystem |
| CANopen | `shal,canopen` | Message | ○ | motion/drives over CAN |
| BACnet | `shal,bacnet` | Message | ○ | building automation |
| IO-Link | `shal,io-link` | Message | ○ | smart-sensor point-to-point |
| DNP3 | `shal,dnp3` | Message | ○ | utilities/SCADA |

## Wireless (6)

| Bus | `compatible` | Kind | Status | Notes |
|---|---|---|---|---|
| BLE GATT | `shal,ble` | Stream | ○ | bleak; sensors & wearables |
| Zigbee | `shal,zigbee` | Stream | ○ | mesh home/industrial |
| LoRaWAN | `shal,lorawan` | Message | ○ | long-range IoT |
| Z-Wave | `shal,zwave` | Stream | ○ | home automation |
| Thread | `shal,thread` | Message | ○ | low-power mesh |
| Matter | `shal,matter` | Message | ○ | cross-vendor smart home |

## DevOps / infra (8)

| Bus | `compatible` | Kind | Status | Notes |
|---|---|---|---|---|
| Docker exec | `shal,docker` | Command | ○ | run inside containers |
| Kubernetes API | `shal,k8s` | Message | ○ | exec/port-forward to pods |
| Redfish | `shal,redfish` | Message | ○ | modern server BMC |
| IPMI | `shal,ipmi` | Message | ○ | legacy server BMC |
| Kafka | `shal,kafka` | Stream | ○ | event streaming |
| NATS | `shal,nats` | Stream | ○ | lightweight messaging |
| NETCONF | `shal,netconf` | Message | ○ | network device config |
| Serial console server | `shal,console-server` | Command | ○ | port-per-device terminal |

---

# Drivers

## Sensors — temperature & humidity (24)

| Device | `compatible` | Capability | Notes |
|---|---|---|---|
| Simulated sensor | `shal,sim-sensor` | TemperatureSensor | ✅ registered; the simulated device the Quick Start reads |
| TI TMP102 | `ti,tmp102` | TemperatureSensor | ✅ ADK reference (`shal docs --example tmp102`); the canonical first driver |
| TI TMP117 | `ti,tmp117` | TemperatureSensor | high-accuracy |
| Maxim DS18B20 | `maxim,ds18b20` | TemperatureSensor | 1-Wire; hobby staple |
| NXP/TI LM75 | `nxp,lm75` | TemperatureSensor | ubiquitous clone target |
| Microchip MCP9808 | `microchip,mcp9808` | TemperatureSensor | ✅ repo example (`examples/drivers/mcp9808`); ±0.25 °C |
| Maxim MAX31855 | `maxim,max31855` | TemperatureSensor | thermocouple amp (SPI) |
| Maxim MAX6675 | `maxim,max6675` | TemperatureSensor | K-type thermocouple |
| Sensirion SHT31 | `sensirion,sht31` | HumiditySensor | temp+RH reference part |
| Sensirion SHT40 | `sensirion,sht40` | HumiditySensor | newer gen |
| Silabs Si7021 | `silabs,si7021` | HumiditySensor | common temp+RH |
| Bosch BME280 | `bosch,bme280` | EnvironmentSensor | temp/RH/pressure |
| Bosch BME680 | `bosch,bme680` | EnvironmentSensor | adds gas/IAQ |
| Bosch BMP280 | `bosch,bmp280` | PressureSensor | temp+pressure |
| Melexis MLX90614 | `melexis,mlx90614` | TemperatureSensor | IR non-contact |
| Melexis MLX90640 | `melexis,mlx90640` | ThermalCamera | 32×24 thermal array |
| ADI ADT7410 | `adi,adt7410` | TemperatureSensor | 16-bit |
| ST STTS751 | `st,stts751` | TemperatureSensor | compact |
| TI TMP36 | `ti,tmp36` | TemperatureSensor | analog (needs ADC) |
| Aosong DHT22 | `aosong,dht22` | HumiditySensor | cheap temp+RH |
| Aosong DHT11 | `aosong,dht11` | HumiditySensor | entry-level |
| Sensirion SCD30 | `sensirion,scd30` | CO2Sensor | NDIR CO₂ |
| Sensirion SCD41 | `sensirion,scd41` | CO2Sensor | photoacoustic CO₂ |
| TE MS5611 | `te,ms5611` | PressureSensor | altimeter-grade |
| Infineon DPS310 | `infineon,dps310` | PressureSensor | barometric |

## Sensors — motion / IMU (14)

| Device | `compatible` | Capability | Notes |
|---|---|---|---|
| InvenSense MPU6050 | `invensense,mpu6050` | IMU | 6-axis; hobby staple |
| InvenSense MPU9250 | `invensense,mpu9250` | IMU | 9-axis |
| InvenSense ICM-20948 | `invensense,icm20948` | IMU | 9-axis, low power |
| InvenSense ICM-42688 | `invensense,icm42688` | IMU | high-perf 6-axis |
| Bosch BNO055 | `bosch,bno055` | OrientationSensor | sensor-fusion output |
| Bosch BMI160 | `bosch,bmi160` | IMU | low-power 6-axis |
| Bosch BMI270 | `bosch,bmi270` | IMU | wearables |
| ST LSM6DSOX | `st,lsm6dsox` | IMU | 6-axis + ML core |
| ST LSM9DS1 | `st,lsm9ds1` | IMU | 9-axis |
| ST LIS3DH | `st,lis3dh` | Accelerometer | 3-axis accel |
| ADI ADXL345 | `adi,adxl345` | Accelerometer | classic 3-axis |
| ADI ADXL355 | `adi,adxl355` | Accelerometer | low-noise |
| NXP FXOS8700 | `nxp,fxos8700` | IMU | accel+mag |
| AMS AS5600 | `ams,as5600` | AngleSensor | magnetic rotary encoder |

## Sensors — light / proximity / distance (8)

| Device | `compatible` | Capability | Notes |
|---|---|---|---|
| AMS TSL2561 | `ams,tsl2561` | LightSensor | lux |
| AMS TSL2591 | `ams,tsl2591` | LightSensor | high dynamic range |
| Rohm BH1750 | `rohm,bh1750` | LightSensor | cheap lux |
| Vishay VEML7700 | `vishay,veml7700` | LightSensor | ambient light |
| ST VL53L0X | `st,vl53l0x` | DistanceSensor | ToF, popular |
| ST VL53L1X | `st,vl53l1x` | DistanceSensor | longer range ToF |
| AMS APDS9960 | `ams,apds9960` | GestureSensor | gesture/color/proximity |
| Sharp GP2Y0A | `sharp,gp2y0a` | DistanceSensor | analog IR |

## Sensors — gas / air quality (6)

| Device | `compatible` | Capability | Notes |
|---|---|---|---|
| Sensirion SGP30 | `sensirion,sgp30` | GasSensor | VOC/eCO₂ |
| Sensirion SGP40 | `sensirion,sgp40` | GasSensor | VOC index |
| AMS CCS811 | `ams,ccs811` | GasSensor | eCO₂/TVOC |
| ScioSense ENS160 | `sciosense,ens160` | GasSensor | air quality |
| Plantower PMS5003 | `plantower,pms5003` | ParticulateSensor | PM2.5 |
| Figaro TGS | `figaro,tgs` | GasSensor | MOX gas |

## Sensors — current / power / ADC / DAC (18)

| Device | `compatible` | Capability | Notes |
|---|---|---|---|
| TI INA219 | `ti,ina219` | PowerMonitor | ✅ repo example (`examples/drivers/ina219`); I²C current/power; very common |
| TI INA226 | `ti,ina226` | PowerMonitor | higher precision |
| TI INA260 | `ti,ina260` | PowerMonitor | integrated shunt |
| TI INA3221 | `ti,ina3221` | PowerMonitor | 3-channel |
| Allegro ACS712 | `allegro,acs712` | CurrentSensor | hall current (analog) |
| TI ADS1115 | `ti,ads1115` | ADC | ✅ repo example (`examples/drivers/ads1115`); 16-bit 4-ch; ubiquitous |
| TI ADS1015 | `ti,ads1015` | ADC | 12-bit |
| Microchip MCP3008 | `microchip,mcp3008` | ADC | 8-ch SPI; Pi staple |
| Microchip MCP3208 | `microchip,mcp3208` | ADC | 12-bit |
| TI ADS131M | `ti,ads131m` | ADC | precision delta-sigma |
| TI ADS8688 | `ti,ads8688` | ADC | 8-ch 16-bit |
| Microchip MCP4725 | `microchip,mcp4725` | DAC | 12-bit I²C DAC |
| ADI AD5693 | `adi,ad5693` | DAC | 16-bit |
| TI DAC8568 | `ti,dac8568` | DAC | 8-ch |
| TI ADS1256 | `ti,ads1256` | ADC | 24-bit |
| Microchip PAC1934 | `microchip,pac1934` | PowerMonitor | 4-ch energy |
| Avia HX711 | `avia,hx711` | LoadCell | load-cell ADC; scales |
| Nau7802 | `nuvoton,nau7802` | LoadCell | 24-bit bridge |

## I/O expanders, muxes, digipots (8)

| Device | `compatible` | Capability | Notes |
|---|---|---|---|
| NXP PCA9548 | `nxp,pca9548` | I2CMux | ✅ repo example (`examples/drivers/pca9548`), 8-ch; the mux mechanism (`MuxState`/`MuxChannel`) ships in core |
| Microchip MCP23017 | `microchip,mcp23017` | GPIOExpander | ✅ ADK reference (`shal docs --example mcp23017`); 16-bit I²C; very common |
| Microchip MCP23008 | `microchip,mcp23008` | GPIOExpander | 8-bit |
| NXP PCF8574 | `nxp,pcf8574` | GPIOExpander | 8-bit; LCD backpacks |
| NXP PCA9555 | `nxp,pca9555` | GPIOExpander | 16-bit |
| TI TCA6416 | `ti,tca6416` | GPIOExpander | 16-bit |
| TI TCA9548A | `ti,tca9548a` | I2CMux | PCA9548 equivalent |
| Microchip MCP4131 | `microchip,mcp4131` | DigitalPot | SPI digipot |

## Actuators — motor drivers & servos (16)

| Device | `compatible` | Capability | Notes |
|---|---|---|---|
| NXP PCA9685 | `nxp,pca9685` | PWMController | 16-ch servo/LED PWM |
| Allegro A4988 | `allegro,a4988` | StepperDriver | classic step/dir |
| TI DRV8825 | `ti,drv8825` | StepperDriver | higher current |
| Trinamic TMC2209 | `trinamic,tmc2209` | StepperDriver | silent; 3D-printer default |
| Trinamic TMC5160 | `trinamic,tmc5160` | StepperDriver | high power, motion ctrl |
| ST L298N | `st,l298n` | MotorController | dual H-bridge |
| ST L6470 | `st,l6470` | StepperDriver | SPI dSPIN |
| TI DRV8833 | `ti,drv8833` | MotorController | dual H-bridge, low V |
| Infineon BTS7960 | `infineon,bts7960` | MotorController | 43 A H-bridge |
| VESC | `vesc,motor` | MotorController | open BLDC controller |
| DSHOT ESC | `generic,dshot-esc` | ESC | drone motor protocol |
| Robotis Dynamixel | `robotis,dynamixel` | ServoBus | smart serial servos |
| Adafruit Motor Shield | `adafruit,motorshield` | MotorController | PCA9685-based |
| Pololu Tic | `pololu,tic` | StepperDriver | USB/serial stepper |
| Generic relay | `generic,relay` | Relay | GPIO/expander coil |
| SainSmart 8-relay | `sainsmart,relay8` | Relay | module board |

## Memory & RTC (10)

| Device | `compatible` | Capability | Notes |
|---|---|---|---|
| Maxim DS3231 | `maxim,ds3231` | RTC | TCXO RTC; very common |
| Maxim DS1307 | `maxim,ds1307` | RTC | basic RTC |
| NXP PCF8523 | `nxp,pcf8523` | RTC | low power |
| NXP PCF8563 | `nxp,pcf8563` | RTC | common clone |
| Microchip 24LC256 | `microchip,24lc256` | EEPROM | I²C EEPROM |
| Microchip AT24C32 | `microchip,at24c32` | EEPROM | pairs with DS3231 |
| Cypress FM24 | `cypress,fm24` | FRAM | nonvolatile RAM |
| Winbond W25Q | `winbond,w25q` | SPIFlash | NOR flash; ESP/RP2040 |
| Micron MT25Q | `micron,mt25q` | SPIFlash | high density |
| SD card (SPI) | `generic,sdcard-spi` | BlockStorage | FAT logging |

## Displays & LEDs (12)

| Device | `compatible` | Capability | Notes |
|---|---|---|---|
| Solomon SSD1306 | `solomon,ssd1306` | Display | 128×64 OLED; everywhere |
| Solomon SSD1331 | `solomon,ssd1331` | Display | color OLED |
| Sitronix ST7789 | `sitronix,st7789` | Display | IPS TFT |
| Sitronix ST7735 | `sitronix,st7735` | Display | small TFT |
| Ilitek ILI9341 | `ilitek,ili9341` | Display | 320×240 TFT |
| Hitachi HD44780 | `hitachi,hd44780` | CharDisplay | 16×2 char LCD |
| Maxim MAX7219 | `maxim,max7219` | LEDMatrix | 7-seg / 8×8 matrix |
| WS2812 / NeoPixel | `worldsemi,ws2812` | AddressableLED | addressable RGB |
| SK6812 | `opsco,sk6812` | AddressableLED | RGBW variant |
| APA102 / DotStar | `apa,apa102` | AddressableLED | SPI addressable |
| TM1637 | `titan,tm1637` | LEDDisplay | 4-digit 7-seg |
| Nokia 5110 (PCD8544) | `philips,pcd8544` | Display | retro LCD |

## Power ICs — PMIC / fuel gauge / charger (8)

| Device | `compatible` | Capability | Notes |
|---|---|---|---|
| Maxim MAX17048 | `maxim,max17048` | FuelGauge | LiPo SoC gauge |
| TI BQ27441 | `ti,bq27441` | FuelGauge | impedance-track gauge |
| TI BQ24295 | `ti,bq24295` | BatteryCharger | I²C charger |
| TI TPS65217 | `ti,tps65217` | PMIC | SBC PMIC (BeagleBone) |
| Maxim MAX77650 | `maxim,max77650` | PMIC | wearable PMIC |
| ADI LTC2941 | `adi,ltc2941` | CoulombCounter | battery gauge |
| TI INA228 | `ti,ina228` | PowerMonitor | 20-bit energy |
| Maxim DS2438 | `maxim,ds2438` | BatteryMonitor | 1-Wire |

## Programmers & debug tools (9)

| Device | `compatible` | Capability | Notes |
|---|---|---|---|
| SEGGER J-Link | `segger,jlink` | DebugProbe | flash/debug via JLinkExe |
| ST-Link | `st,stlink` | DebugProbe | STM32 flash/debug |
| OpenOCD target | `openocd,target` | DebugProbe | generic JTAG/SWD |
| Espressif esptool | `espressif,esptool` | Flasher | ESP32/ESP8266 flashing |
| avrdude | `atmel,avrdude` | Flasher | AVR programming |
| dfu-util | `generic,dfu-util` | Flasher | USB DFU |
| Nordic nrfjprog | `nordic,nrfjprog` | Flasher | nRF5x |
| Bus Pirate | `dangerous,buspirate` | BusBridge | multi-protocol probe |
| Black Magic Probe | `blacksphere,bmp` | DebugProbe | GDB-native |

## Test & measurement instruments (56)

> SCPI-class; reached over `visa`, `scpi-raw`, `gpib`, or `usbtmc`.

### Oscilloscopes (12)

| Device | `compatible` | Capability | Notes |
|---|---|---|---|
| Keysight DSOX1000 | `keysight,dsox1000` | Oscilloscope | entry InfiniiVision |
| Keysight DSOX3000 | `keysight,dsox3000` | Oscilloscope | mid-range |
| Keysight Infiniium | `keysight,infiniium` | Oscilloscope | high-end |
| Tektronix MSO5 | `tektronix,mso5` | Oscilloscope | mixed-signal |
| Tektronix DPO4000 | `tektronix,dpo4000` | Oscilloscope | popular bench |
| Tektronix TBS1000 | `tektronix,tbs1000` | Oscilloscope | entry |
| Rigol DS1000Z | `rigol,ds1000z` | Oscilloscope | hobby/lab favorite |
| Rigol MSO5000 | `rigol,mso5000` | Oscilloscope | value MSO |
| Siglent SDS1000X-E | `siglent,sds1000xe` | Oscilloscope | high value |
| Siglent SDS2000X | `siglent,sds2000x` | Oscilloscope | mid-range |
| R&S RTB2000 | `rohde-schwarz,rtb2000` | Oscilloscope | 10-bit |
| LeCroy WaveSurfer | `lecroy,wavesurfer` | Oscilloscope | — |

### Digital multimeters (7)

| Device | `compatible` | Capability | Notes |
|---|---|---|---|
| Simulated DMM | `shal,sim-dmm` | DigitalMultimeter | ✅ registered; simulated DMM on `shal,sim-scpi`, reading a `shal,sim-psu` output (`config.probe`) |
| Keysight 34461A | `keysight,34461a` | DigitalMultimeter | ✅ repo example (`examples/drivers/keysight_34461a`, no sim twin); 6½-digit bench standard |
| Keysight 34470A | `keysight,34470a` | DigitalMultimeter | 7½-digit |
| Keithley DMM6500 | `keithley,dmm6500` | DigitalMultimeter | touchscreen 6½ |
| Keithley 2000 | `keithley,2000` | DigitalMultimeter | long-running classic |
| Fluke 8845A | `fluke,8845a` | DigitalMultimeter | precision bench |
| Rigol DM3068 | `rigol,dm3068` | DigitalMultimeter | 6½-digit value |
| Siglent SDM3045X | `siglent,sdm3045x` | DigitalMultimeter | 4½-digit |

### Power supplies (9)

| Device | `compatible` | Capability | Notes |
|---|---|---|---|
| Simulated PSU | `shal,sim-psu` | PowerSupply | ✅ registered; simulated supply on `shal,sim-scpi` |
| Keysight E36312A | `keysight,e36312a` | PowerSupply | triple-output bench |
| Keysight E3631A | `keysight,e3631a` | PowerSupply | classic triple |
| Rigol DP832 | `rigol,dp832` | PowerSupply | ✅ ADK reference (`shal docs --example rigol_dp832`); popular triple |
| Rigol DP712 | `rigol,dp712` | PowerSupply | single high-power |
| Keithley 2230 | `keithley,2230` | PowerSupply | triple |
| Siglent SPD3303 | `siglent,spd3303` | PowerSupply | value triple |
| R&S NGP800 | `rohde-schwarz,ngp800` | PowerSupply | multi-channel |
| TDK-Lambda Genesys | `tdk-lambda,genesys` | PowerSupply | high-power rack |
| BK Precision 9100 | `bk-precision,9100` | PowerSupply | — |

### Electronic loads (4)

| Device | `compatible` | Capability | Notes |
|---|---|---|---|
| Keysight EL34243A | `keysight,el34243a` | ElectronicLoad | dual input |
| Rigol DL3021 | `rigol,dl3021` | ElectronicLoad | value DC load |
| Siglent SDL1020 | `siglent,sdl1020` | ElectronicLoad | programmable DC load |
| BK Precision 8600 | `bk-precision,8600` | ElectronicLoad | — |

### Function / arbitrary generators (6)

| Device | `compatible` | Capability | Notes |
|---|---|---|---|
| Keysight 33500B | `keysight,33500b` | FunctionGenerator | TrueForm AWG |
| Keysight 33220A | `keysight,33220a` | FunctionGenerator | classic 20 MHz |
| Rigol DG1000Z | `rigol,dg1000z` | FunctionGenerator | value AWG |
| Rigol DG4000 | `rigol,dg4000` | FunctionGenerator | 4-ch |
| Siglent SDG2000X | `siglent,sdg2000x` | FunctionGenerator | — |
| Tektronix AFG31000 | `tektronix,afg31000` | FunctionGenerator | touchscreen |

### Source measure units (5)

| Device | `compatible` | Capability | Notes |
|---|---|---|---|
| Keithley 2400 | `keithley,2400` | SourceMeter | the SMU standard |
| Keithley 2450 | `keithley,2450` | SourceMeter | touchscreen successor |
| Keithley 2600B | `keithley,2600b` | SourceMeter | dual-channel TSP |
| Keysight B2902B | `keysight,b2902b` | SourceMeter | precision SMU |
| Keysight B2961A | `keysight,b2961a` | SourceMeter | low-noise |

### Spectrum / network / other (13)

| Device | `compatible` | Capability | Notes |
|---|---|---|---|
| Keysight N9000 CXA | `keysight,n9000` | SpectrumAnalyzer | — |
| Rigol DSA800 | `rigol,dsa800` | SpectrumAnalyzer | value SA |
| Siglent SSA3000X | `siglent,ssa3000x` | SpectrumAnalyzer | — |
| R&S FSV | `rohde-schwarz,fsv` | SpectrumAnalyzer | high-end |
| Keysight E5071C | `keysight,e5071c` | NetworkAnalyzer | VNA |
| NanoVNA | `nanovna,nanovna` | NetworkAnalyzer | low-cost VNA |
| Keysight E4980A | `keysight,e4980a` | LCRMeter | precision LCR |
| Hioki IM3536 | `hioki,im3536` | LCRMeter | — |
| Keysight 53220A | `keysight,53220a` | FrequencyCounter | universal counter |
| Keysight DAQ970A | `keysight,daq970a` | DataAcquisition | switch/measure |
| Keysight 34970A | `keysight,34970a` | DataAcquisition | legacy DAQ |
| NI DAQmx | `ni,daqmx` | DataAcquisition | LabVIEW ecosystem |
| Yokogawa WT300 | `yokogawa,wt300` | PowerAnalyzer | power measurement |

## Robotics (12)

| Device | `compatible` | Capability | Notes |
|---|---|---|---|
| Slamtec RPLIDAR | `slamtec,rplidar` | Lidar | hobby/SLAM lidar |
| Velodyne VLP-16 | `velodyne,vlp16` | Lidar | 3D lidar |
| Ouster OS1 | `ouster,os1` | Lidar | digital lidar |
| u-blox NEO-M8 | `ublox,neo-m8` | GNSS | popular GPS |
| u-blox ZED-F9P | `ublox,zed-f9p` | GNSS | RTK cm-accuracy |
| Intel RealSense | `intel,realsense` | DepthCamera | RGB-D |
| PX4 / MAVLink | `px4,mavlink` | FlightController | drones |
| ArduPilot | `ardupilot,mavlink` | FlightController | drones/rovers |
| ROS 2 node | `ros,node` | ROSBridge | topic/service bridge |
| Robotis OpenCR | `robotis,opencr` | RobotController | TurtleBot |
| Adafruit Crickit | `adafruit,crickit` | RobotController | maker robotics |
| YDLIDAR | `ydlidar,x4` | Lidar | low-cost lidar |

## Industrial devices (14)

| Device | `compatible` | Capability | Notes |
|---|---|---|---|
| Siemens S7-1200 | `siemens,s7-1200` | PLC | dominant PLC |
| Siemens S7-1500 | `siemens,s7-1500` | PLC | high-end |
| Allen-Bradley CompactLogix | `allen-bradley,compactlogix` | PLC | Rockwell ecosystem |
| Schneider Modicon | `schneider,modicon` | PLC | Modbus-native |
| Beckhoff EtherCAT terminals | `beckhoff,el-terminals` | IOModule | EtherCAT I/O |
| WAGO 750 | `wago,750` | IOModule | fieldbus I/O |
| ABB ACS880 | `abb,acs880` | VFD | motor drive |
| Danfoss VLT | `danfoss,vlt` | VFD | motor drive |
| IFM IO-Link master | `ifm,io-link-master` | IOLinkMaster | smart sensor hub |
| Balluff IO-Link | `balluff,io-link` | IOLinkMaster | — |
| SICK laser scanner | `sick,laser-scanner` | SafetyScanner | safety/measurement |
| Festo CPX | `festo,cpx` | ValveTerminal | pneumatics |
| Generic Modbus relay | `generic,modbus-relay` | Relay | common industrial relay |
| Generic Modbus meter | `generic,modbus-meter` | EnergyMeter | power metering |

## Server / infra management (10)

| Device | `compatible` | Capability | Notes |
|---|---|---|---|
| Redfish BMC | `dmtf,redfish` | ServerManagement | modern standard |
| IPMI BMC | `ipmi,bmc` | ServerManagement | legacy standard |
| Dell iDRAC | `dell,idrac` | ServerManagement | Redfish + extras |
| HPE iLO | `hpe,ilo` | ServerManagement | — |
| Lenovo XCC | `lenovo,xcc` | ServerManagement | — |
| APC PDU | `apc,pdu` | PowerDistribution | switched PDU (SNMP) |
| Raritan PDU | `raritan,pdu` | PowerDistribution | — |
| ServerTech PDU | `servertech,pdu` | PowerDistribution | — |
| Cisco IOS switch | `cisco,ios` | NetworkSwitch | SSH/NETCONF |
| Arista EOS switch | `arista,eos` | NetworkSwitch | API-first |

## Data — databases (13)

| Service | `compatible` | Capability | Notes |
|---|---|---|---|
| PostgreSQL | `postgres,db` | RelationalDB | most-loved RDBMS |
| MySQL | `mysql,db` | RelationalDB | ubiquitous |
| MariaDB | `mariadb,db` | RelationalDB | MySQL fork |
| SQLite | `sqlite,database` | — | ✅ ADK reference (`shal docs --example sqlite`); root driver over stdlib `sqlite3`, all four side-effect labels |
| Redis | `redis,db` | KeyValueStore | cache/store |
| Python dbm | `python,dbm` | KeyValueStore | ✅ ADK reference (`shal docs --example kvstore`); the short wrap-a-library recipe — a root driver over stdlib `dbm.dumb` |
| MongoDB | `mongodb,db` | DocumentStore | document DB |
| InfluxDB | `influxdb,db` | TimeSeriesDB | metrics/IoT time series |
| TimescaleDB | `timescale,db` | TimeSeriesDB | PG time-series |
| ClickHouse | `clickhouse,db` | AnalyticsDB | OLAP |
| Elasticsearch | `elastic,search` | SearchStore | logs/search |
| Cassandra | `cassandra,db` | WideColumnStore | distributed |
| CockroachDB | `cockroach,db` | RelationalDB | distributed SQL |

## Data — brokers & object storage (11)

| Service | `compatible` | Capability | Notes |
|---|---|---|---|
| Apache Kafka | `kafka,broker` | MessageBroker | event streaming |
| RabbitMQ | `rabbitmq,broker` | MessageBroker | AMQP queues |
| NATS | `nats,broker` | MessageBroker | lightweight |
| MQTT broker | `mqtt,broker` | MessageBroker | IoT pub/sub |
| Apache Pulsar | `pulsar,broker` | MessageBroker | streaming |
| ZeroMQ | `zeromq,socket` | MessageBroker | brokerless |
| AWS S3 | `aws,s3` | ObjectStore | cloud object store |
| MinIO | `minio,s3` | ObjectStore | self-hosted S3 |
| Google Cloud Storage | `gcp,gcs` | ObjectStore | — |
| Azure Blob | `azure,blob` | ObjectStore | — |
| SFTP server | `generic,sftp` | FileStore | file transfer |

## Services — observability & CI/CD & infra (16)

| Service | `compatible` | Capability | Notes |
|---|---|---|---|
| Prometheus | `prometheus,tsdb` | MetricsStore | metrics standard |
| Grafana | `grafana,api` | Dashboards | dashboards/alerts |
| Loki | `loki,logs` | LogStore | log aggregation |
| Jaeger | `jaeger,tracing` | TraceStore | distributed tracing |
| OpenTelemetry Collector | `otel,collector` | Telemetry | pipeline |
| Datadog | `datadog,api` | Observability | SaaS APM |
| GitHub | `github,api` | SCM | repos/PRs/actions |
| GitLab | `gitlab,api` | SCM | repos/CI |
| Jenkins | `jenkins,api` | CIServer | build server |
| Argo CD | `argocd,api` | GitOps | k8s delivery |
| HashiCorp Vault | `hashicorp,vault` | SecretStore | secrets |
| HashiCorp Consul | `hashicorp,consul` | ServiceDiscovery | discovery/KV |
| etcd | `etcd,kv` | KeyValueStore | k8s backing store |
| Docker Engine | `docker,engine` | ContainerRuntime | containers |
| Kubernetes | `k8s,cluster` | Orchestrator | container orchestration |
| Ansible host | `ansible,host` | ConfigManagement | provisioning target |

## Cloud services (7)

| Service | `compatible` | Capability | Notes |
|---|---|---|---|
| AWS IoT Core | `aws,iot-core` | IoTPlatform | device cloud (MQTT) |
| AWS Lambda | `aws,lambda` | FunctionInvoke | serverless |
| AWS DynamoDB | `aws,dynamodb` | KeyValueStore | managed NoSQL |
| Azure IoT Hub | `azure,iot-hub` | IoTPlatform | device cloud |
| GCP Pub/Sub | `gcp,pubsub` | MessageBroker | managed messaging |
| GCP IoT (legacy) | `gcp,iot` | IoTPlatform | — |
| Twilio | `twilio,api` | Notification | SMS/voice alerts |
