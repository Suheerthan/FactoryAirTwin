"""Stage 5 field-readiness: live edge ingestion bridge.

Accepts real 1 Hz telemetry as REST batches (or MQTT when paho-mqtt is
installed), derives the compressor state from motor power alone — keeping the
sensor-light promise — and reuses the exact same rule engine and Stage-2
Isolation Forest as the simulated dashboard. The bridge only READS: no control
command is ever sent to plant equipment.
"""

import threading
from collections import deque

from ai_detector import WINDOW, infer_readings
from simulator import PLANT, detect_alerts

LIMITS = {"pressure_bar": (0.0, 16.0), "power_kw": (0.0, 500.0), "temperature_c": (-20.0, 120.0)}
MAX_BUFFER = 3600  # one hour of 1 Hz readings
UNLOADED_LOADED_MID_KW = (PLANT.unloaded_kw + PLANT.loaded_kw) / 2.0

_lock = threading.Lock()
_buffer = deque(maxlen=MAX_BUFFER)
_totals = {"accepted": 0, "rejected": 0, "reasons": {}}


def derive_state(power_kw):
    """Sensor-light state estimate from motor power only (off / unloaded / loaded)."""
    if power_kw < 0.5:
        return "Off"
    if power_kw < UNLOADED_LOADED_MID_KW:
        return "Unloaded"
    return "Loaded"


def _clean_row(raw, previous_second):
    if not isinstance(raw, dict):
        return None, "row must be an object"
    try:
        second = int(raw["t"])
        pressure = float(raw["pressure_bar"])
        power = float(raw["power_kw"])
    except (KeyError, TypeError, ValueError):
        return None, "missing/invalid t, pressure_bar or power_kw"
    for key, (low, high) in (("pressure_bar", LIMITS["pressure_bar"]), ("power_kw", LIMITS["power_kw"])):
        value = pressure if key == "pressure_bar" else power
        if not (low <= value <= high):
            return None, f"{key} outside physical range"
    if previous_second is not None and second <= previous_second:
        return None, "timestamp not increasing"
    temperature = raw.get("temperature_c")
    if temperature is not None:
        try:
            temperature = float(temperature)
            if not (LIMITS["temperature_c"][0] <= temperature <= LIMITS["temperature_c"][1]):
                return None, "temperature_c outside physical range"
        except (TypeError, ValueError):
            return None, "invalid temperature_c"
    row = {"second": second, "pressure_bar": pressure, "power_kw": power, "temperature_c": temperature}
    row["state"] = derive_state(power)
    production = raw.get("production")
    if isinstance(production, bool):
        # The edge node's optional machine-on contact: lets the twin pick
        # genuinely idle windows for the leak test.
        row["production_active"] = production
        row["production_source"] = "contact"
    else:
        # Field gateways rarely know production state; the conservative proxy is
        # "machine loaded => production demanding air". Documented in the report.
        row["production_active"] = row["state"] == "Loaded"
        row["production_source"] = "proxy"
    return row, None


def ingest(batch):
    """Validate and append a batch of raw device frames. Returns a summary."""
    if not isinstance(batch, list):
        return {"accepted": 0, "rejected": 0, "reasons": {"payload": 1}, "buffer_seconds": len(_buffer),
                "error": "payload must be a JSON array of readings"}
    accepted, reasons = 0, {}
    with _lock:
        previous = _buffer[-1]["second"] if _buffer else None
        for raw in batch:
            row, why = _clean_row(raw, previous)
            if row is None:
                reasons[why] = reasons.get(why, 0) + 1
                continue
            _buffer.append(row)
            previous = row["second"]
            accepted += 1
        _totals["accepted"] += accepted
        _totals["rejected"] += sum(reasons.values())
        for why, n in reasons.items():
            _totals["reasons"][why] = _totals["reasons"].get(why, 0) + n
    return {"accepted": accepted, "rejected": sum(reasons.values()), "reasons": reasons,
            "buffer_seconds": len(_buffer)}


def buffer_status():
    with _lock:
        rows = len(_buffer)
        span = (_buffer[-1]["second"] - _buffer[0]["second"]) if rows > 1 else 0
    return {"buffer_seconds": rows, "span_seconds": span, "max_buffer": MAX_BUFFER,
            "ready_for_scoring": rows >= WINDOW}


def snapshot():
    """Run the shared rule engine + Stage-2 model over the live buffer."""
    with _lock:
        rows = [dict(r) for r in _buffer]
    status = buffer_status()
    if not status["ready_for_scoring"]:
        return {"ready": False, "status": status, "alerts": [], "anomaly": {"enabled": False, "windows": []}}
    alerts = detect_alerts(rows)
    anomaly = infer_readings(rows)
    flagged = [w for w in anomaly.get("windows", []) if w.get("is_anomaly")]
    return {"ready": True, "status": status, "alerts": alerts,
            "anomaly": {"enabled": anomaly.get("enabled"), "threshold": anomaly.get("threshold"),
                        "windows_scored": len(anomaly.get("windows", [])), "windows_flagged": len(flagged),
                        "latest": flagged[-1] if flagged else (anomaly.get("windows") or [None])[-1]}}


def readings():
    """Copy of the live buffer (used by the self-calibrating twin)."""
    with _lock:
        return [dict(r) for r in _buffer]


def readings_since(second, limit=900):
    """Readings newer than ``second`` (for the live chart), newest ``limit`` only."""
    with _lock:
        rows = [dict(r) for r in _buffer if r["second"] > second]
    return rows[-limit:]


def totals():
    with _lock:
        return {"accepted": _totals["accepted"], "rejected": _totals["rejected"],
                "reasons": dict(_totals["reasons"])}


def clear():
    with _lock:
        _buffer.clear()
        _totals.update({"accepted": 0, "rejected": 0, "reasons": {}})


def start_mqtt_listener(broker="localhost", topic="factoryair/readings"):
    """Optional live feed. Requires `pip install paho-mqtt` on the edge host."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return {"started": False, "error": "paho-mqtt not installed; use REST /api/edge/ingest instead"}

    def on_message(_client, _userdata, message):
        import json
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        ingest(payload if isinstance(payload, list) else [payload])

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    client.connect(broker)
    client.subscribe(topic)
    client.loop_start()
    return {"started": True, "broker": broker, "topic": topic}
