# FactoryAir Edge Node — hardware design (Stage 2)

A clip-on, read-only box that gives FactoryAir Twin its two signals. Not built
yet (no prototype budget); the software emulator `edge_emulator.py` sends the
identical frames today so the whole pipeline can be demonstrated.

## Signals and wiring

| Signal | Sensor | Into | Notes |
|---|---|---|---|
| Header pressure | 4–20 mA transmitter, 0–16 bar(g), G¼, on the existing gauge port via a ball valve | 165 Ω 0.1 % shunt → ADS1115 A0 | 4 mA = 0.66 V, 20 mA = 3.30 V. < 3.6 mA = open loop → frame skipped |
| Motor current | Split-core CT SCT-013-030 (30 A : 1 V, built-in burden) on one phase | ADS1115 A1–A3 differential | Clip-on: no cable is cut. kW = √3 · V · I · pf (pf measured once on site) |
| Production on/off | Dry contact from a machine-on relay (optional) | ESP32 GPIO 7, INPUT_PULLUP | Lets the twin pick idle windows for the leak test |
| Clock | NTP (IST) | — | Frames without valid time are dropped, not guessed |
| Buffer | microSD (SPI, CS = GPIO 10) | — | 72 h at 1 Hz when Wi-Fi is down, replayed oldest-first |

ESP32-S3 I²C: SDA = GPIO 8, SCL = GPIO 9. Supply: 24 V DIN-rail PSU → 5 V buck → board.

## Frame format (shared with the emulator and the API)

```json
[{"t": 1729580400, "pressure_bar": 6.842, "power_kw": 7.41, "production": true}, ...]
```

POST batches of 10 to `/api/edge/ingest`. The bridge rejects out-of-range
values, non-increasing timestamps and malformed frames, and reports why.

## Indicative bill of materials (prices to confirm with vendors)

| Part | Qty | ≈ ₹ |
|---|---|---|
| ESP32-S3 DevKitC | 1 | 800 |
| ADS1115 16-bit ADC module | 1 | 350 |
| 4–20 mA pressure transmitter 0–16 bar | 1 | 1,800 |
| SCT-013-030 split-core CT | 1 | 450 |
| 165 Ω 0.1 % shunt, TVS, fuses | 1 set | 150 |
| 24 V DIN PSU + 5 V buck | 1 | 1,100 |
| microSD module + 16 GB card | 1 | 400 |
| IP65 ABS enclosure, glands, DIN rail | 1 | 700 |
| G¼ tee + ball valve | 1 | 350 |
| **Total** | | **≈ 6,100** |

## Safety by design

* No outputs: the node cannot command the compressor.
* Clip-on CT: no mains conductor is opened; built-in burden avoids open-secondary voltage.
* Pressure tap behind an isolation ball valve; transmitter rated above relief-valve pressure.
* Relevant standards: IEC 61010-1 / 61010-2-032 (measurement equipment, current clamps), IEC 60529 (IP65), ISO 11011 (compressed-air energy assessment).
