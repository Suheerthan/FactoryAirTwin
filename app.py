r"""Run with: .\.venv\Scripts\python.exe app.py (Windows)."""

import io
import math

from flask import Flask, jsonify, render_template, request

from flask import send_file

from simulator import SCENARIOS, compare_runs, run_simulation
from ai_detector import infer_readings, model_status
from ai_stage3 import infer_stage3, stage3_status
from optimizer import compare_sequences, intervention_catalog, schedule_savings, setpoint_search
from stage4 import (ACTIONS, ACTION_PERMISSIONS, ROLE_LABELS, ROLE_PERMISSIONS, STRESS_PROFILES,
                    build_backup, build_pdf_report, combined_savings, corrupt_readings,
                    create_ticket, diagnose_scenario, get_config, get_ticket, list_tickets,
                    model_history, require_role, restore_backup, run_stress, sensor_health,
                    ticket_action, update_config, verify_ticket)


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # small JSON-state uploads (backup restore) only


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
                   stage="simulator + rules + Stage 2 anomaly detector + Stage 3 optimiser & fault classifier"
                         " + Stage 4 production-readiness layer")


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


# ================================================================ STAGE 4
def gate(permission):
    """Resolve the caller's role (JSON body or ?role=) and enforce permission.

    The default role is 'manager' so the prototype keeps working if a client
    sends no role at all; the dashboard always sends one explicitly."""
    data = request.get_json(silent=True) or {}
    role = request.args.get("role") or (data.get("role") if isinstance(data, dict) else None) or "manager"
    label = require_role(role, permission)
    return role, label, data


@app.get("/api/stage4/status")
def stage4_status():
    tickets = list_tickets()
    return jsonify(stage="Stage 4 production readiness + 4.5 polish", data_mode="simulation",
                   config=get_config(), stress_profiles=list(STRESS_PROFILES),
                   workflow_actions=list(ACTIONS),
                   roles={name: sorted(perms) for name, perms in ROLE_PERMISSIONS.items()},
                   role_labels=ROLE_LABELS,
                   tickets={"total": len(tickets),
                            "by_state": {s: sum(t["state"] == s for t in tickets)
                                         for s in ("open", "acknowledged", "in_repair", "verifying",
                                                   "resolved", "cancelled")}})


@app.get("/api/stage4/config")
def stage4_config_get():
    return jsonify(get_config())


@app.post("/api/stage4/config")
def stage4_config_update():
    try:
        role, label, data = gate("config")
        changes = {k: v for k, v in data.items() if k not in ("role", "operator")}
        config, error = update_config(changes)
        if error:
            return jsonify(error=error), 400
        return jsonify(config)
    except ValueError as exc:
        return jsonify(error=str(exc)), 403


@app.post("/api/stage4/sensorhealth")
def stage4_sensorhealth():
    try:
        role, label, data = gate("sensorhealth")
        scenario = data.get("scenario", "normal")
        if scenario not in SCENARIOS:
            raise ValueError("Choose normal, leak, unloaded, filter or worn.")
        corruptions = data.get("corruptions", [])
        run = run_simulation(scenario)
        readings = corrupt_readings(run["readings"], corruptions) if corruptions else run["readings"]
        report = sensor_health(readings, get_config())
        report["scenario"] = scenario
        report["corruptions_applied"] = list(corruptions) if corruptions else []
        return jsonify(report)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/stage4/diagnose")
def stage4_diagnose():
    try:
        role, label, data = gate("diagnose")
        scenario = data.get("scenario", "leak")
        if scenario not in SCENARIOS:
            raise ValueError("Choose normal, leak, unloaded, filter or worn.")
        state, run = diagnose_scenario(scenario)
        return jsonify({"diagnosis": state, "energy_kwh_30min": run["summary"]["energy_kwh"],
                        "alerts": [{"key": a["key"], "title": a["title"], "second": a["second"]}
                                   for a in run["alerts"]]})
    except ValueError as exc:
        return jsonify(error=str(exc)), 403


@app.post("/api/stage4/ticket/from-diagnosis")
def stage4_ticket_from_diagnosis():
    try:
        role, label, data = gate("create_ticket")
        scenario = data.get("scenario", "leak")
        if scenario not in SCENARIOS or scenario == "normal":
            raise ValueError("Run the diagnosis on a fault scenario first.")
        state, run = diagnose_scenario(scenario)
        fault = state["faults"][0] if state["faults"] else "unknown"
        leak_alert = next((a for a in run["alerts"] if a["key"] == "leak"), None)
        predicted = leak_alert.get("impact", {}) if leak_alert else {}
        predicted_payload = ({"inr_per_month": predicted.get("inr_per_month"),
                              "kwh_per_day": predicted.get("kwh_per_day")} if predicted else {})
        operator = f"{data.get('operator', 'operator')} ({label})"
        ticket = create_ticket(fault, f"{scenario.title()} fault - {state['state']}",
                               evidence=state["explanation"], predicted=predicted_payload,
                               operator=operator)
        return jsonify(ticket)
    except ValueError as exc:
        return jsonify(error=str(exc)), 403


@app.get("/api/maintenance/tickets")
def maintenance_list():
    return jsonify(list_tickets())


@app.post("/api/maintenance/tickets")
def maintenance_create():
    try:
        role, label, data = gate("create_ticket")
        operator = f"{data.get('operator', 'operator')} ({label})"
        return jsonify(create_ticket(data.get("fault_type", "unknown"), data.get("title", "Manual ticket"),
                                     evidence=data.get("evidence", ""), predicted=data.get("predicted"),
                                     operator=operator))
    except ValueError as exc:
        return jsonify(error=str(exc)), 403


@app.post("/api/maintenance/tickets/<ticket_id>/action")
def maintenance_action(ticket_id):
    try:
        role, label, data = gate("view")
        action = data.get("action", "")
        if action not in ACTIONS:
            raise ValueError(f"Unknown action. Use one of: {', '.join(ACTIONS)}.")
        require_role(role, ACTION_PERMISSIONS[action])
        operator = f"{data.get('operator', 'operator')} ({label})"
        return jsonify(ticket_action(ticket_id, action, operator, data.get("note", "")))
    except ValueError as exc:
        return jsonify(error=str(exc)), 403


@app.post("/api/maintenance/tickets/<ticket_id>/verify")
def maintenance_verify(ticket_id):
    try:
        role, label, data = gate("verify")
        return jsonify(verify_ticket(ticket_id, f"{data.get('operator', 'operator')} ({label})"))
    except ValueError as exc:
        return jsonify(error=str(exc)), 403


@app.post("/api/optimise/combined")
def optimise_combined():
    try:
        role, label, data = gate("combined")
        scenario = data.get("scenario", "leak")
        actions = data.get("actions", ["repair_leak", "setpoint"])
        if not isinstance(actions, list) or not actions:
            raise ValueError("Pick at least one action: repair_leak, setpoint, sequencing, scheduling.")
        return jsonify(combined_savings(scenario, actions))
    except ValueError as exc:
        return jsonify(error=str(exc)), 403


@app.post("/api/stage4/stress")
def stage4_stress():
    try:
        role, label, data = gate("stress")
        return jsonify(run_stress(data.get("tests"), data.get("seed", 4400)))
    except ValueError as exc:
        return jsonify(error=str(exc)), 403


@app.get("/api/report/pdf")
def report_pdf():
    try:
        gate("report")
    except ValueError as exc:
        return jsonify(error=str(exc)), 403
    path = build_pdf_report()
    return send_file(path, as_attachment=True, download_name="FactoryAirTwin_SIMULATED_report.pdf",
                     mimetype="application/pdf")


@app.get("/api/stage4/backup")
def stage4_backup():
    gate("view")
    blob = build_backup()
    return send_file(io.BytesIO(blob), as_attachment=True,
                     download_name="FactoryAirTwin_backup.zip", mimetype="application/zip")


@app.post("/api/stage4/restore")
def stage4_restore():
    try:
        role, label, _data = gate("restore")
        upload = request.files.get("backup")
        if upload is None:
            raise ValueError("Attach the backup ZIP as a form field named 'backup'.")
        restored = restore_backup(upload.read())
        return jsonify(restored=restored, by=f"{_data.get('operator', 'manager')} ({label})")
    except ValueError as exc:
        return jsonify(error=str(exc)), 403


@app.get("/api/stage4/modelhistory")
def stage4_modelhistory():
    return jsonify(model_history())


if __name__ == "__main__":
    print("FactoryAir Twin: open http://127.0.0.1:5001 in your browser.")
    app.run(host="127.0.0.1", port=5001, debug=False)
