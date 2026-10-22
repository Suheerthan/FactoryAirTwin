"""Virtual FactoryAir Edge Node: the software twin of the clip-on box.

Generates exactly the frames the ESP32 firmware (firmware/factoryair_edge)
will send - {"t", "pressure_bar", "power_kw", "production"} at 1 Hz - from a
virtual compressor that uses the same receiver physics as the rest of the
twin. Frames go through the real ingestion path:

* in-app:  the dashboard's Live gateway screen starts a background thread
           that calls edge_bridge.ingest() - the same validation as HTTP
* CLI:     python edge_emulator.py --speed 20 --url http://127.0.0.1:5001
           POSTs batches to /api/edge/ingest over HTTP (standard library only)

Operators can switch the virtual plant between a production shift and an
idle period, open a new leak, and inject the sensor faults the gateway must
survive: a Wi-Fi dropout, a stuck transmitter, an impossible spike and a
clock that jumps backwards. Nothing here touches real equipment.
"""

import argparse
import json
import threading
import time
from math import sin
from random import Random
from urllib import request as urlrequest

from simulator import PLANT

FAULTS = ("dropout", "stuck", "spike", "clock")
BASE_LEAK = 0.06
NEW_LEAK = 0.13


class VirtualCompressor:
    """1 Hz virtual plant with operator controls and fault injection."""

    def __init__(self, seed=5, start_epoch=None, receiver_m3=0.62):
        self.rng = Random(seed)
        self.t = int(start_epoch if start_epoch is not None else time.time())
        self.k = PLANT.reference_bar / receiver_m3 / 60.0
        self.p, self.state, self.idle = 7.0, 0, 0
        self.production = True
        self.extra_leak = 0.0
        self.pending = {}           # fault -> frames remaining
        self.stuck_value = None
        self.second = 0

    # ---------------------------------------------------------------- controls
    def set_production(self, on):
        self.production = bool(on)

    def set_new_leak(self, on):
        self.extra_leak = NEW_LEAK if on else 0.0

    def inject(self, fault):
        if fault not in FAULTS:
            raise ValueError(f"fault must be one of {', '.join(FAULTS)}.")
        self.pending[fault] = {"dropout": 30, "stuck": 120, "spike": 1, "clock": 1}[fault]

    # ---------------------------------------------------------------- physics
    def _step(self):
        self.second += 1
        demand = 0.0
        if self.production:
            demand = 0.45 * (1 + 0.10 * sin(self.second / 37.0)) if self.second % 300 < 210 else 0.05
        if self.p <= PLANT.cut_in_bar:
            self.state, self.idle = 1, 0
        elif self.state == 1 and self.p >= PLANT.cut_out_bar:
            self.state, self.idle = 2, 0
        if self.state == 2:
            if self.idle >= 20:
                self.state = 0
            else:
                self.idle += 1
        if self.state == 1:
            inlet = PLANT.rated_flow_m3_min * 0.97 * (1 - 0.02 * (self.p - 7))
            kw = PLANT.loaded_kw * 1.02 * (1 + 0.035 * (self.p - 7))
        elif self.state == 2:
            inlet, kw = 0.0, PLANT.unloaded_kw * 1.02
        else:
            inlet, kw = 0.0, 0.0
        leak = (BASE_LEAK + self.extra_leak) * self.p / 7.0
        self.p += self.k * (inlet - demand - leak)
        return (self.p + self.rng.gauss(0, 0.004), kw + self.rng.gauss(0, 0.025) if kw else 0.0)

    def frames(self, n):
        """Advance n seconds and return the frames a real node would send."""
        out = []
        for _ in range(n):
            self.t += 1
            pressure, power = self._step()
            if self.pending.get("dropout"):
                self.pending["dropout"] -= 1
                continue                                  # Wi-Fi gap: frame never arrives
            if self.pending.get("stuck"):
                self.stuck_value = self.stuck_value if self.stuck_value is not None else round(pressure, 3)
                pressure = self.stuck_value
                self.pending["stuck"] -= 1
                if not self.pending["stuck"]:
                    self.stuck_value = None
            frame = {"t": self.t, "pressure_bar": round(pressure, 3), "power_kw": round(power, 2),
                     "production": self.production}
            if self.pending.get("spike"):
                frame["pressure_bar"] = 25.0                # impossible: above the 16 bar span
                self.pending["spike"] -= 1
            if self.pending.get("clock"):
                frame["t"] = self.t - 5                     # clock jumped backwards
                self.pending["clock"] -= 1
            out.append(frame)
        return out


class EmulatorRunner:
    """Background thread feeding a sink (edge_bridge.ingest or HTTP)."""

    def __init__(self, sink, speed=20, seed=5):
        self.sink = sink
        self.speed = speed
        self.plant = VirtualCompressor(seed=seed)
        self._stop = threading.Event()
        self._thread = None
        self.sent = 0
        self.started_at = None
        self.lock = threading.Lock()

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self):
        while not self._stop.is_set():
            with self.lock:
                batch = self.plant.frames(max(1, int(self.speed / 2)))
            if batch:
                self.sink(batch)
                self.sent += len(batch)
            self._stop.wait(0.5)

    def status(self):
        p = self.plant
        return {"running": self.running, "speed_x": self.speed, "frames_generated": self.sent,
                "production": p.production, "new_leak_open": p.extra_leak > 0,
                "active_faults": {k: v for k, v in p.pending.items() if v},
                "virtual_clock": p.t}


def http_sink(base_url):
    def send(batch):
        body = json.dumps(batch).encode("utf-8")
        req = urlrequest.Request(f"{base_url.rstrip('/')}/api/edge/ingest", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
        with urlrequest.urlopen(req, timeout=10) as resp:
            print(json.loads(resp.read().decode("utf-8")))
    return send


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stream virtual edge-node frames to FactoryAir Twin.")
    parser.add_argument("--url", default="http://127.0.0.1:5001")
    parser.add_argument("--speed", type=int, default=20, help="simulated seconds per real second")
    parser.add_argument("--seconds", type=int, default=600, help="virtual seconds to send")
    parser.add_argument("--idle", action="store_true", help="plant idle (no production) - for the leak test")
    parser.add_argument("--leak", action="store_true", help="open the extra leak")
    args = parser.parse_args()
    plant = VirtualCompressor()
    plant.set_production(not args.idle)
    plant.set_new_leak(args.leak)
    send = http_sink(args.url)
    for _ in range(0, args.seconds, 10):
        send(plant.frames(10))
        time.sleep(10 / max(1, args.speed))
