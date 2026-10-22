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
        zone = data.get("leak_zone")
        if zone is not None:
            from leak_locator import ZONES
            if (not isinstance(zone, dict) or zone.get("zone") not in [z["id"] for z in ZONES]
                    or isinstance(zone.get("seed"), bool) or not isinstance(zone.get("seed"), int)
                    or not 1 <= zone["seed"] <= 1_000_000):
                raise ValueError("leak_zone must be {seed: 1-1000000, zone: a Leak Hunter zone id}.")
            zone = {"seed": zone["seed"], "zone": zone["zone"]}
        return jsonify(create_ticket(data.get("fault_type", "unknown"), data.get("title", "Manual ticket"),
                                     evidence=data.get("evidence", ""), predicted=data.get("predicted"),
                                     operator=operator, leak_zone=zone))
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



# ---------------- Stage 5: live edge ingestion ----------------
from edge_bridge import ingest as _edge_ingest, snapshot as _edge_snapshot, buffer_status as _edge_status
from edge_bridge import readings as _edge_readings


@app.post("/api/edge/ingest")
def edge_ingest_route():
    payload = request.get_json(silent=True)
    return jsonify(_edge_ingest(payload if payload is not None else []))


@app.get("/api/edge/status")
def edge_status_route():
    return jsonify(_edge_status())


@app.get("/api/edge/snapshot")
def edge_snapshot_route():
    return jsonify(_edge_snapshot())


# ---------------- Self-calibrating twin, Leak Hunter, advisors ----------------
from advisor import heat_recovery, opportunity_ladder, vsd_upgrade
from leak_locator import BRANCHES, decay_test, plant_layout, survey
from longrun import run_longrun, validate_identification
from twin_id import identify_stream


def _tariff(value=None):
    """Explicit tariff if given, otherwise the saved site calibration."""
    if value is None:
        value = get_config().get("tariff_inr_kwh", 7.0)
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            raise ValueError("Enter an illustrative electricity price from ₹0 to ₹100 per kWh.")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("Enter an illustrative electricity price from ₹0 to ₹100 per kWh.")
    return round(float(value), 2)


def _seed(value, default=11):
    if value is None:
        return default
    try:
        seed = int(value)
    except (TypeError, ValueError):
        raise ValueError("seed must be a whole number.")
    if not 1 <= seed <= 1_000_000:
        raise ValueError("seed must be between 1 and 1,000,000.")
    return seed


def _longrun(repair=True, tariff=None):
    return run_longrun(14, bool(repair), _tariff(tariff))


@app.get("/api/twin/longrun")
def twin_longrun():
    try:
        return jsonify(_longrun(request.args.get("repair", "1") != "0", request.args.get("tariff")))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.get("/api/twin/validation")
def twin_validation():
    return jsonify(validate_identification())


@app.post("/api/twin/identify")
def twin_identify():
    """Identify on a 30-minute scenario or on the live edge buffer."""
    try:
        data = payload()
        source = data.get("source", "scenario")
        if source == "edge":
            rows = _edge_readings()
            if len(rows) < 120:
                raise ValueError("The edge buffer needs at least 2 minutes of readings before identification.")
            # With the node's machine-on contact the buffer already says which
            # seconds were idle. Without it, the gateway schedules this call
            # inside a known idle window (night / lunch), so mark it idle.
            has_contact = any(r.get("production_source") == "contact" for r in rows)
            if not has_contact and data.get("idle_window", True):
                rows = [dict(r, production_active=False) for r in rows]
        elif source == "scenario":
            scenario = data.get("scenario", "leak")
            if scenario not in SCENARIOS:
                raise ValueError("Choose normal, leak, unloaded, filter or worn.")
            rows = run_simulation(scenario)["readings"]
        else:
            raise ValueError("source must be 'scenario' or 'edge'.")
        result = identify_stream([r["pressure_bar"] for r in rows], [r["power_kw"] for r in rows],
                                 [r.get("production_active", False) for r in rows],
                                 volume_hint_m3=get_config().get("receiver_volume_m3"))
        result["source"] = source
        return jsonify(result)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.get("/api/overview")
def overview():
    try:
        tariff = _tariff(request.args.get("tariff"))
        lr = _longrun(True, tariff)
        tickets = list_tickets()
        return jsonify({
            "data_mode": "simulation", "site": get_config().get("site_name"), "tariff_inr_kwh": tariff,
            "summary": lr["summary"], "identified": lr["identified"], "events": lr["events"],
            "daily": lr["daily"], "ladder": opportunity_ladder(lr, tariff),
            "open_tickets": sum(t["state"] not in ("resolved", "cancelled") for t in tickets),
            "resolved_tickets": sum(t["state"] == "resolved" for t in tickets),
        })
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.get("/api/leaks/layout")
def leaks_layout():
    try:
        return jsonify(plant_layout(_seed(request.args.get("seed"))))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/leaks/test")
def leaks_test():
    try:
        data = payload()
        branches = data.get("open_branches", [])
        if not isinstance(branches, list) or any(b not in BRANCHES for b in branches):
            raise ValueError(f"open_branches must list zones from: {', '.join(BRANCHES)}.")
        index = data.get("test_index", 0)
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= 50:
            raise ValueError("test_index must be a whole number from 0 to 50.")
        return jsonify(decay_test(_seed(data.get("seed")), branches, index))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/leaks/survey")
def leaks_survey():
    try:
        data = payload()
        lr = _longrun(True, data.get("tariff"))
        sp = lr["identified"].get("specific_power_kw_per_m3_min") or None
        kwargs = {"specific_power": sp} if sp else {}
        return jsonify(survey(_seed(data.get("seed")), _tariff(data.get("tariff")),
                              reveal=bool(data.get("reveal", False)), **kwargs))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/advisor/heat")
def advisor_heat():
    try:
        data = payload()
        lr = _longrun(True, data.get("tariff"))
        kwargs = {k: data[k] for k in ("fuel", "recoverable_pct", "usable_pct", "inlet_c", "outlet_c",
                                        "fuel_price", "kit_cost_inr") if k in data}
        return jsonify(heat_recovery(lr["summary"]["monthly_kwh"], **kwargs))
    except (ValueError, TypeError) as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/advisor/vsd")
def advisor_vsd():
    try:
        data = payload()
        tariff = _tariff(data.get("tariff"))
        lr = _longrun(True, tariff)
        kwargs = {k: data[k] for k in ("min_speed_pct", "drive_loss_pct", "upgrade_cost_inr") if k in data}
        for key, value in kwargs.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"{key} must be a non-negative number.")
        return jsonify(vsd_upgrade(lr["hourly"], lr["summary"]["days"], tariff, **kwargs))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


# ---------------- Virtual edge node, bring-your-own CSV, owner report ----------------
from csv_import import identify_csv, sample_csv
from edge_bridge import clear as _edge_clear, readings_since as _edge_since, totals as _edge_totals
from edge_emulator import FAULTS, EmulatorRunner
from owner_report import build_context
from stage4 import sensor_health as _sensor_health

_emulator = EmulatorRunner(_edge_ingest, speed=20)
CSV_UPLOAD_LIMIT = 8 * 1024 * 1024


@app.post("/api/edge/emulator")
def edge_emulator_control():
    """Start/stop the virtual edge node and drive the virtual plant."""
    try:
        data = payload()
        action = data.get("action")
        if action == "start":
            speed = data.get("speed", 20)
            if isinstance(speed, bool) or not isinstance(speed, int) or not 1 <= speed <= 120:
                raise ValueError("speed must be a whole number from 1 to 120.")
            _emulator.speed = speed
            _emulator.start()
        elif action == "stop":
            _emulator.stop()
        elif action == "production":
            with _emulator.lock:
                _emulator.plant.set_production(bool(data.get("on", True)))
        elif action == "leak":
            with _emulator.lock:
                _emulator.plant.set_new_leak(bool(data.get("on", True)))
        elif action == "fault":
            fault = data.get("fault")
            if fault not in FAULTS:
                raise ValueError(f"fault must be one of {', '.join(FAULTS)}.")
            with _emulator.lock:
                _emulator.plant.inject(fault)
        elif action == "reset":
            _emulator.stop()
            _edge_clear()
            _emulator.plant.__init__()
            _emulator.sent = 0
        else:
            raise ValueError("action must be start, stop, production, leak, fault or reset.")
        return jsonify(_emulator.status())
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.get("/api/edge/live")
def edge_live():
    """Everything the Live gateway screen needs in one poll."""
    try:
        since = int(request.args.get("since", 0))
    except ValueError:
        return jsonify(error="since must be a whole number."), 400
    rows = _edge_readings()
    health = None
    if len(rows) >= 60:
        health = _sensor_health(rows[-600:], get_config())
        health = {"overall": health["overall"], "issues": health["issues"][:4],
                  "sensors": {k: v["status"] for k, v in health["sensors"].items()}}
    snap = _edge_snapshot()
    return jsonify({"emulator": _emulator.status(), "status": _edge_status(), "totals": _edge_totals(),
                    "rows": [{"t": r["second"], "p": round(r["pressure_bar"], 3), "kw": round(r["power_kw"], 2),
                              "prod": r["production_active"]} for r in _edge_since(since)],
                    "health": health,
                    "alerts": [{"key": a["key"], "title": a["title"], "evidence": a["evidence"]}
                               for a in snap.get("alerts", [])],
                    "anomaly": snap.get("anomaly")})


@app.post("/api/twin/upload")
def twin_upload():
    """Identify a plant from an uploaded logger CSV (multipart field 'csv')."""
    try:
        request.max_content_length = CSV_UPLOAD_LIMIT
        upload = request.files.get("csv")
        if upload is None:
            raise ValueError("Attach the CSV as a form field named 'csv'.")
        try:
            text = upload.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("The file is not UTF-8 text. Export it from Excel as 'CSV UTF-8'.")

        def number(name, default):
            raw = request.form.get(name)
            if raw in (None, ""):
                return default
            try:
                return float(raw)
            except ValueError:
                raise ValueError(f"{name} must be a number.")

        return jsonify(identify_csv(text, fad_nominal=number("fad", 0.9), nameplate_kw=number("kw", 7.5),
                                    tariff=number("tariff", _tariff()),
                                    kw_per_amp=number("kw_per_amp", 3 ** 0.5 * 415 * 0.86 / 1000)))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.get("/api/twin/sample.csv")
def twin_sample_csv():
    response = app.response_class(sample_csv(), mimetype="text/csv")
    response.headers["Content-Disposition"] = 'attachment; filename="FactoryAirTwin_sample_logger_SIMULATED.csv"'
    return response


@app.get("/api/evidence/selfcal")
def evidence_selfcal():
    """Self-calibration on real MetroPT-3 data, if metropt_selfcal.py has been run."""
    import json as _json
    from metropt_selfcal import REPORT
    if not REPORT.exists():
        return jsonify(error="Not run yet. Download MetroPT-3 and run metropt_selfcal.py."), 404
    return jsonify(_json.loads(REPORT.read_text(encoding="utf-8")))


@app.get("/report/owner")
def report_owner():
    lang = request.args.get("lang", "en")
    tariff = _tariff()
    lr = _longrun(True, tariff)
    overview_data = {"site": get_config().get("site_name"), "summary": lr["summary"], "identified": lr["identified"],
                     "events": lr["events"], "daily": lr["daily"], "ladder": opportunity_ladder(lr, tariff)}
    return render_template("owner_report.html", **build_context(overview_data, list_tickets(), lang))


def _warm_caches():
    """Pre-compute the two-week twin so the first dashboard visit is instant."""
    try:
        _longrun(True)
        validate_identification()
    except Exception:  # noqa: BLE001 - warming is best-effort; routes compute on demand
        pass


if __name__ == "__main__":
    import os
    import threading
    threading.Thread(target=_warm_caches, daemon=True).start()
    host = os.environ.get("FACTORYAIR_HOST", "127.0.0.1")
    port = int(os.environ.get("FACTORYAIR_PORT", "5001"))
    print(f"FactoryAir Twin: open http://{host}:{port} in your browser.")
    app.run(host=host, port=port, debug=False)
