r"""Run with: .\.venv\Scripts\python.exe app.py (Windows)."""

import math

from flask import Flask, jsonify, render_template, request

from simulator import SCENARIOS, compare_runs, run_simulation
from ai_detector import infer_readings, model_status
from ai_stage3 import infer_stage3, stage3_status
from optimizer import compare_sequences, intervention_catalog, schedule_savings, setpoint_search


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4096


def options():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValueError("Send a JSON object with scenario and tariff.")
    scenario = payload.get("scenario", "normal")
    if not isinstance(scenario, str) or scenario not in SCENARIOS:
        raise ValueError("Choose normal, leak, unloaded, filter or worn.")
    tariff = payload.get("tariff", 7.0)
    if isinstance(tariff, bool) or not isinstance(tariff, (float, int)) or not math.isfinite(tariff) or not 0 <= tariff <= 100:
        raise ValueError("Enter an illustrative electricity price from ₹0 to ₹100 per kWh.")
    return scenario, float(tariff)


def payload():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("Send a JSON object.")
    return data


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify(status="ok", application="FactoryAir Twin",
                   stage="simulator + rules + Stage 2 anomaly detector + Stage 3 optimiser & fault classifier")


@app.get("/api/ai/status")
def ai_status():
    return jsonify(model_status())


@app.get("/api/ai/report")
def ai_report():
    status = model_status()
    if not status.get("report"):
        return jsonify(error="No matching local evaluation report. Run train_model.py, then restart app.py."), 404
    response = jsonify(status["report"])
    response.headers["Content-Disposition"] = 'attachment; filename="FactoryAirTwin_SYNTHETIC_validation.json"'
    return response


@app.get("/api/stage3/status")
def stage3_status_route():
    return jsonify(stage3_status())


@app.get("/api/stage3/report")
def stage3_report():
    status = stage3_status()
    if not status.get("report"):
        return jsonify(error="No matching Stage 3 report. Run train_stage3.py, then restart app.py."), 404
    response = jsonify(status["report"])
    response.headers["Content-Disposition"] = 'attachment; filename="FactoryAirTwin_STAGE3_SYNTHETIC_validation.json"'
    return response


@app.post("/api/simulate")
def simulate():
    try:
        scenario, tariff = options()
        run = run_simulation(scenario, tariff=tariff)
        run["ai"] = infer_readings(run["readings"])
        run["ai_stage3"] = infer_stage3(run["readings"])
        return jsonify(run)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/compare")
def compare():
    try:
        scenario, tariff = options()
        return jsonify(compare_runs(scenario, tariff))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/optimise/setpoint")
def optimise_setpoint():
    try:
        scenario, tariff = options()
        return jsonify(setpoint_search(scenario, tariff))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/optimise/sequence")
def optimise_sequence():
    try:
        data = payload()
        tariff = data.get("tariff", 7.0)
        if isinstance(tariff, bool) or not isinstance(tariff, (float, int)) or not 0 <= tariff <= 100:
            raise ValueError("Enter an illustrative electricity price from ₹0 to ₹100 per kWh.")
        return jsonify(compare_sequences(float(tariff)))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/schedule")
def schedule():
    try:
        data = payload()
        sec = data.get("sec_kwh_per_unit")
        if isinstance(sec, bool) or not isinstance(sec, (float, int)) or not math.isfinite(sec) or sec <= 0:
            raise ValueError("Load a simulation first; its specific energy feeds the scheduler.")
        kwargs = {key: data[key] for key in ("units_per_day", "shiftable_pct", "peak_rate",
                                             "off_peak_rate", "solar_rate", "solar_share_pct") if key in data}
        return jsonify(schedule_savings(float(sec), **kwargs))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/catalog")
def catalog():
    try:
        scenario, tariff = options()
        return jsonify(intervention_catalog(scenario, tariff))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.get("/api/connectivity/sample")
def connectivity_sample():
    sample = run_simulation("normal")["readings"][119]
    return jsonify({
        "description": ("Edge-first integration sketch. In a real installation an edge device would "
                        "sample these registers and publish windows locally; this sample is generated "
                        "from one simulated reading."),
        "mqtt": {
            "broker": "Local edge broker (no internet required)",
            "topics": ["factoryair/site-01/compressor-01/readings",
                       "factoryair/site-01/compressor-01/windows",
                       "factoryair/site-01/recommendations"],
            "payload": {
                "timestamp_second": sample["second"],
                "pressure_bar_g": sample["pressure_bar"],
                "power_kw": sample["power_kw"],
                "temperature_c": sample["temperature_c"],
                "state": sample["state"],
                "production_active": sample["production_active"],
            },
        },
        "modbus": {
            "note": "Illustrative register map for a generic compressor controller.",
            "registers": [
                {"address": 40001, "signal": "Header pressure", "unit": "bar(g) x100"},
                {"address": 40002, "signal": "Motor power", "unit": "kW x10"},
                {"address": 40003, "signal": "Oil temperature", "unit": "°C"},
                {"address": 40004, "signal": "Running state", "unit": "0 Off / 1 Loaded / 2 Unloaded"},
                {"address": 40005, "signal": "Production status", "unit": "0 paused / 1 active"},
            ],
        },
        "schneider_alignment": ("Electrical measurements map to a PowerTag-class meter at the compressor "
                                "panel; pressure and state come from the controller. Analytics stays on "
                                "the edge device and only recommendations require operator approval."),
    })


if __name__ == "__main__":
    print("FactoryAir Twin: open http://127.0.0.1:5001 in your browser.")
    app.run(host="127.0.0.1", port=5001, debug=False)
