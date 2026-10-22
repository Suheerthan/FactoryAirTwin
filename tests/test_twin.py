"""Self-calibrating twin, two-week run, Leak Hunter and advisors."""

import pytest

from advisor import heat_recovery, opportunity_ladder, pressure_opportunity, vsd_upgrade
from app import app
from leak_locator import BRANCHES, decay_test, hidden_leaks, plant_layout, survey
from longrun import SiteTruth, run_longrun, simulate_idle_window, validate_identification
from twin_id import classify_power, idle_leak_test, loaded_power_curve


@pytest.fixture(scope="module")
def longrun():
    return run_longrun()


@pytest.fixture()
def client():
    return app.test_client()


# ---------------------------------------------------------------- twin_id
def test_idle_test_recovers_hidden_leak_and_volume():
    truth = SiteTruth(receiver_m3=0.9, base_leak_m3_min=0.18, fad_scale=1.0, power_scale=1.0)
    pressure, power = simulate_idle_window(truth, seed=3)
    result = idle_leak_test(pressure, power)
    assert result["ok"] and result["method"].startswith("load/unload")
    true_leak = truth.base_leak_m3_min * result["mean_pressure_bar"] / 7.0
    assert abs(result["leak_m3_min"] - true_leak) / true_leak < 0.03
    assert abs(result["effective_volume_m3"] - 0.9) / 0.9 < 0.05
    assert result["estimator_disagreement_pct"] < 10  # time- and slope-ratio agree


def test_idle_test_falls_back_honestly_without_cycles():
    truth = SiteTruth(receiver_m3=1.5, base_leak_m3_min=0.005)
    pressure, power = simulate_idle_window(truth, seconds=600, seed=4)
    result = idle_leak_test(pressure, power, volume_hint_m3=1.5)
    assert result["ok"] and result["method"].startswith("passive decay")
    assert result["volume_source"].startswith("assumed")
    assert result["confidence"] == "low"


def test_idle_test_rejects_short_or_mismatched_input():
    assert idle_leak_test([7.0] * 10, [0.0] * 10)["ok"] is False
    assert idle_leak_test([7.0] * 200, [0.0] * 199)["ok"] is False


def test_power_classification_and_curve():
    assert classify_power([0.0, 2.1, 7.5]) == [0, 0, 1]
    pressure = [6.5 + 0.001 * i for i in range(400)]
    power = [7.5 * (1 + 0.035 * (p - 7)) * 1.08 for p in pressure]
    curve = loaded_power_curve(pressure, power)
    assert curve["verdict"] == "deterioration suspected"
    assert curve["power_ratio_vs_nameplate"] == pytest.approx(1.08, abs=0.01)


def test_validation_on_unseen_plants():
    v = validate_identification(plants=12, seed=99)
    assert v["identified"] == 12
    assert v["leak_mean_abs_error_pct"] < 6
    assert v["volume_mean_abs_error_pct"] < 8
    assert all("true_leak_m3_min" in r for r in v["rows"])


# ---------------------------------------------------------------- longrun
def test_longrun_detects_new_leak_and_verifies_repair(longrun):
    kinds = [(e["kind"], e["night"]) for e in longrun["events"]]
    assert ("new_leak", 3) in kinds  # coupling fails Thursday afternoon -> seen that night
    assert ("leak_reduced", 10) in kinds  # repaired the following Thursday
    assert len(longrun["events"]) == 2  # no duplicate or spurious flags


def test_longrun_identifies_pipework_volume_not_nameplate(longrun):
    ident = longrun["identified"]
    assert ident["nameplate_receiver_m3"] == 0.5
    assert abs(ident["effective_volume_m3"] - SiteTruth().receiver_m3) / SiteTruth().receiver_m3 < 0.06
    assert longrun["validation"]["leak_mape_pct"] < 6


def test_longrun_calendar_and_summary(longrun):
    daily = longrun["daily"]
    assert len(daily) == 14 and len(longrun["hourly"]) == 14 * 24
    assert daily[6]["production_hours"] == 0  # Sunday
    assert daily[5]["production_hours"] < daily[0]["production_hours"]  # Saturday half day
    s = longrun["summary"]
    assert s["lowest_pressure_in_production_bar"] >= 6.0
    assert s["latest_leak_inr_month"] < s["peak_leak_inr_month"]
    assert longrun["data_mode"] == "simulation"


def test_longrun_without_repair_keeps_leak():
    lr = run_longrun(repair=False)
    assert [e["kind"] for e in lr["events"]] == ["new_leak"]


# ---------------------------------------------------------------- Leak Hunter
def test_hidden_leaks_are_seeded_and_vary():
    assert hidden_leaks(11) == hidden_leaks(11)
    assert hidden_leaks(11) != hidden_leaks(12)


def test_layout_never_leaks_hidden_values():
    text = str(plant_layout(11))
    assert "validation_true" not in text and str(hidden_leaks(11)["bay2"]) not in text


def test_survey_finds_dominant_zone_across_plants():
    for seed in (3, 11, 27, 64, 101):
        result = survey(seed, reveal=True)
        truth = hidden_leaks(seed)
        assert result["ranked"][0] == max(truth, key=truth.get)
        for zone in result["zones"]:
            assert abs(zone["est_m3_min"] - zone["validation_true_m3_min"]) < 0.01


def test_survey_hides_truth_unless_revealed():
    assert all("validation_true_m3_min" not in z for z in survey(11)["zones"])


def test_decay_test_more_open_branches_more_outflow():
    closed = decay_test(11, [])
    opened = decay_test(11, BRANCHES)
    assert opened["outflow_m3_min_at_7bar"] > closed["outflow_m3_min_at_7bar"]
    assert len(closed["trace_bar"]) == 121


# ---------------------------------------------------------------- advisors
def test_heat_recovery_arithmetic_and_validation():
    r = heat_recovery(2000, fuel="electric", recoverable_pct=75, usable_pct=100)
    assert r["recovered_heat_kwh_month"] == 1500
    assert r["inr_per_month"] == round(1500 / 0.97 * 7.0)
    with pytest.raises(ValueError):
        heat_recovery(2000, fuel="coal")
    with pytest.raises(ValueError):
        heat_recovery(2000, inlet_c=70, outlet_c=60)


def test_vsd_not_recommended_when_payback_is_long(longrun):
    r = vsd_upgrade(longrun["hourly"], 14)
    assert r["vsd_kwh_month"] < r["fixed_speed_kwh_month"]
    assert r["payback_months"] > 36  # the twin is allowed to say "don't buy"
    assert len(r["profile_by_hour_of_day"]) == 24


def test_pressure_rule_of_thumb():
    r = pressure_opportunity(7.0, 6.0, 1000, 7.0, margin_bar=0.3)
    assert r["excess_bar"] == 0.7 and r["saving_pct"] == pytest.approx(4.9)


def test_ladder_separates_fuel_from_electricity(longrun):
    ladder = opportunity_ladder(longrun)
    assert ladder["electricity_inr_month"] < longrun["summary"]["monthly_cost_inr"]
    assert ladder["fuel_inr_month"] > 0
    assert {i["key"] for i in ladder["items"]} == {"leak", "unloaded", "pressure", "heat", "vsd"}


# ---------------------------------------------------------------- routes
def test_routes_happy_paths(client):
    assert client.get("/api/overview").status_code == 200
    assert client.get("/api/twin/validation").status_code == 200
    assert client.get("/api/leaks/layout?seed=5").get_json()["seed"] == 5
    assert client.post("/api/leaks/survey", json={"seed": 5}).status_code == 200
    assert client.post("/api/advisor/heat", json={"fuel": "diesel"}).status_code == 200
    assert client.post("/api/advisor/vsd", json={}).status_code == 200
    ident = client.post("/api/twin/identify", json={"scenario": "leak"}).get_json()
    assert ident["leak_test"]["ok"] and abs(ident["leak_test"]["leak_m3_min"] - 0.22) < 0.02


def test_routes_reject_bad_input(client):
    assert client.post("/api/leaks/test", json={"seed": 5, "open_branches": ["roof"]}).status_code == 400
    assert client.post("/api/leaks/test", json={"seed": 0}).status_code == 400
    assert client.get("/api/twin/longrun?tariff=abc").status_code == 400
    assert client.post("/api/advisor/heat", json={"fuel": "coal"}).status_code == 400
    assert client.post("/api/twin/identify", json={"source": "satellite"}).status_code == 400
    assert client.post("/api/advisor/vsd", json={"upgrade_cost_inr": -5}).status_code == 400


# ---------------------------------------------------------------- zone repair verification
def test_zone_repair_verification_matches_prediction():
    from leak_locator import verify_zone_repair
    s = survey(11)
    worst = next(z for z in s["zones"] if z["id"] == s["ranked"][0])
    v = verify_zone_repair(11, worst["id"])
    assert v["after_total_m3_min"] < v["before_total_m3_min"]
    assert abs(v["saved_inr_per_month"] - worst["inr_per_month"]) / worst["inr_per_month"] < 0.15


def test_leak_hunter_ticket_verifies_with_repeat_survey(client, tmp_path, monkeypatch):
    import stage4
    monkeypatch.setattr(stage4, "TICKETS_PATH", tmp_path / "m.json")
    body = {"role": "maintenance_engineer", "fault_type": "leak", "title": "Leak in Bay 2",
            "predicted": {"inr_per_month": 4741}, "leak_zone": {"seed": 11, "zone": "bay2"}}
    tk = client.post("/api/maintenance/tickets", json=body).get_json()
    assert tk["leak_zone"] == {"seed": 11, "zone": "bay2"}
    for action in ("acknowledge", "start_repair"):
        client.post(f"/api/maintenance/tickets/{tk['id']}/action", json={"role": "maintenance_engineer", "action": action})
    v = client.post(f"/api/maintenance/tickets/{tk['id']}/verify", json={"role": "maintenance_engineer"}).get_json()
    assert v["method"].startswith("Repeat valve-isolation survey")
    assert abs(v["deviation_pct_vs_predicted"]) < 15
    bad = dict(body, leak_zone={"seed": 11, "zone": "roof"})
    assert client.post("/api/maintenance/tickets", json=bad).status_code in (400, 403)
