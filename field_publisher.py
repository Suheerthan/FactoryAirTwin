"""Demo field gateway: replays a simulated device stream into the edge bridge.

Stands in for a real MQTT/Modbus gateway during pilots. Run app.py first,
then:  python field_publisher.py leak
"""
import sys
import time

import requests

from simulator import run_simulation

BASE = "http://127.0.0.1:5001"  # app.py default port
scenario = sys.argv[1] if len(sys.argv) > 1 else "leak"
run = run_simulation(scenario)

batch = []
for row in run["readings"]:
    batch.append({"t": row["second"], "pressure_bar": round(row["pressure_bar"], 4),
                  "power_kw": round(row["power_kw"], 3),
                  "temperature_c": round(row["temperature_c"], 2)})
    if len(batch) == 60:
        print(requests.post(f"{BASE}/api/edge/ingest", json=batch, timeout=10).json())
        batch = []
        time.sleep(0.2)  # 60x real time for the demo
if batch:
    print(requests.post(f"{BASE}/api/edge/ingest", json=batch, timeout=10).json())
print(requests.get(f"{BASE}/api/edge/snapshot", timeout=10).json()["anomaly"])
