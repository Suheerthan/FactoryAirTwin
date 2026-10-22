"""Virtual edge node, live gateway, bring-your-own CSV, owner report, real-data self-calibration."""

import io
import time
from datetime import datetime, timedelta

import pytest

import edge_bridge
from app import app
from csv_import import identify_csv, otsu_threshold, parse_csv, sample_csv
from edge_emulator import EmulatorRunner, VirtualCompressor
from longrun import SiteTruth, simulate_idle_window
from metropt_selfcal import analyse, in_documented_leak
from stage4 import sensor_health


@pytest.fixture()
def client():
    edge_bridge.clear()
    yield app.test_client()
    edge_bridge.clear()


@pytest.fixture(scope="module")
def sample_text():
    return sample_csv()


# ---------------------------------------------------------------- virtual edge node
def test_virtual_frames_match_firmware_format():
    frames = VirtualCompressor(start_epoch=1_700_000_000).frames(5)
    assert [f["t"] for f in frames] == list(range(1_700_000_001, 1_700_000_006))
    assert set(frames[0]) == {"t", "pressure_bar", "power_kw", "production"}


def test_faults_are_caught_by_the_bridge():
    plant = VirtualCompressor(start_epoch=1_700_000_000)
    edge_bridge.clear()
    edge_bridge.ingest(plant.frames(50))
    plant.inject("spike")
    result = edge_bridge.ingest(plant.frames(5))
    assert result["reasons"] == {"pressure_bar outside physical range": 1}
    plant.inject("clock")
    result = edge_bridge.ingest(plant.frames(5))
    assert result["reasons"] == {"timestamp not increasing": 1}
    plant.inject("dropout")
    frames = plant.frames(40)
    assert len(frames) == 10  # 30 frames lost in the Wi-Fi gap
    with pytest.raises(ValueError):
        plant.inject("meteor")
    edge_bridge.clear()


def test_production_contact_overrides_proxy():
    edge_bridge.clear()
    edge_bridge.ingest([{"t": 1, "pressure_bar": 7.0, "power_kw": 7.5, "production": False},
                        {"t": 2, "pressure_bar": 7.0, "power_kw": 7.5}])
    rows = edge_bridge.readings()
    assert rows[0]["production_active"] is False and rows[0]["production_source"] == "contact"
    assert rows[1]["production_active"] is True and rows[1]["production_source"] == "proxy"
    edge_bridge.clear()


def test_runner_streams_into_sink():
    received = []
    runner = EmulatorRunner(received.extend, speed=40)
    runner.start()
    time.sleep(1.2)
    runner.stop()
    assert not runner.running and len(received) >= 20


def test_live_identification_finds_virtual_leak(client):
    plant = VirtualCompressor(start_epoch=1_700_000_000)
    plant.set_production(False)
    plant.set_new_leak(True)
    for _ in range(36):
        edge_bridge.ingest(plant.frames(100))
    lt = client.post("/api/twin/identify", json={"source": "edge"}).get_json()["leak_test"]
    true_leak = 0.19 * lt["mean_pressure_bar"] / 7.0
    assert lt["ok"] and abs(lt["leak_m3_min"] - true_leak) / true_leak < 0.08
    assert abs(lt["effective_volume_m3"] - 0.62) / 0.62 < 0.1


def test_emulator_and_live_routes(client):
    assert client.post("/api/edge/emulator", json={"action": "reset"}).status_code == 200
    assert client.post("/api/edge/emulator", json={"action": "fault", "fault": "spike"}).status_code == 200
    assert client.post("/api/edge/emulator", json={"action": "fly"}).status_code == 400
    assert client.post("/api/edge/emulator", json={"action": "start", "speed": 999}).status_code == 400
    live = client.get("/api/edge/live").get_json()
    assert {"emulator", "status", "totals", "rows", "health", "alerts"} <= set(live)
    assert client.get("/api/edge/live?since=abc").status_code == 400


def test_missing_temperature_is_not_fitted_not_failed():
    rows = [{"second": i, "pressure_bar": 7.0 - 0.001 * (i % 50), "power_kw": 7.5 if i % 50 < 10 else 0.0,
             "state": "Loaded" if i % 50 < 10 else "Off", "temperature_c": None} for i in range(300)]
    report = sensor_health(rows)
    assert report["sensors"]["temperature_c"]["status"] == "not_fitted"
    assert report["overall"] != "failed"


# ---------------------------------------------------------------- bring-your-own CSV
def test_otsu_splits_unloaded_from_loaded():
    values = [0.0] * 50 + [2.1] * 200 + [7.5] * 100
    assert 2.5 < otsu_threshold(values) < 7.0


def test_sample_csv_identifies_plant(sample_text):
    r = identify_csv(sample_text)
    assert r["sample_interval_s"] == 5 and r["windows_with_cycling"] == 2
    assert abs(r["summary"]["effective_volume_m3"] - 0.62) / 0.62 < 0.06
    assert r["summary"]["leak_inr_per_month"] > 0


def test_current_column_gives_same_answer(sample_text):
    lines = sample_text.splitlines()
    k = 0.6
    converted = ["timestamp,pressure_bar,current_a"] + [f"{a},{b},{float(c) / k:.3f}" for a, b, c in
                                                        (line.split(",") for line in lines[1:])]
    r = identify_csv("\n".join(converted), kw_per_amp=k)
    assert r["power_from_current"]
    assert r["summary"]["leak_m3_min"] == pytest.approx(identify_csv(sample_text)["summary"]["leak_m3_min"], rel=0.01)


def test_production_flag_picks_idle_windows():
    p, kw = simulate_idle_window(SiteTruth(), seconds=3 * 3600, seed=9)
    rows = ["pressure_bar,power_kw,production"] + [f"{a:.3f},{b:.2f},{1 if i < 1800 else 0}" for i, (a, b) in enumerate(zip(p, kw))]
    r = identify_csv("\n".join(rows))
    assert r["tests"][0]["source"] == "production flag off" and r["windows_identified"] == 1


def test_csv_errors_are_clear():
    with pytest.raises(ValueError, match="pressure column"):
        parse_csv("a,b\n1,2\n")
    with pytest.raises(ValueError, match="fad_nominal"):
        identify_csv("pressure_bar,power_kw\n7,7\n", fad_nominal=0)


def test_upload_route(client, sample_text):
    ok = client.post("/api/twin/upload", data={"csv": (io.BytesIO(sample_text.encode()), "s.csv"), "fad": "0.9"},
                     content_type="multipart/form-data")
    assert ok.status_code == 200 and ok.get_json()["windows_identified"] == 2
    bad = client.post("/api/twin/upload", data={"csv": (io.BytesIO(b"x,y\n1,2\n"), "s.csv")}, content_type="multipart/form-data")
    assert bad.status_code == 400
    assert client.post("/api/twin/upload", data={}, content_type="multipart/form-data").status_code == 400
    assert client.get("/api/twin/sample.csv").status_code == 200


# ---------------------------------------------------------------- owner report
@pytest.mark.parametrize("lang,needle", [("en", "Compressed-air report"), ("ta", "அழுத்தக் காற்று அறிக்கை"), ("xx", "Compressed-air report")])
def test_owner_report_languages(client, lang, needle):
    r = client.get(f"/report/owner?lang={lang}")
    assert r.status_code == 200 and needle in r.get_data(as_text=True)
    assert "SIMULATED" in r.get_data(as_text=True) or "உருவக" in r.get_data(as_text=True)


# ---------------------------------------------------------------- real-data self-calibration logic
def test_documented_leak_nights():
    assert in_documented_leak("2020-05-30") and in_documented_leak("2020-04-18")
    assert in_documented_leak("2020-06-06") and in_documented_leak("2020-06-07")
    assert not in_documented_leak("2020-07-15") and not in_documented_leak("2020-05-01")


def test_selfcal_analysis_ranks_a_leaky_night_first():
    nights = {}
    for d in range(12):
        date = (datetime(2020, 5, 24) + timedelta(days=d)).date().isoformat()
        leak = 0.22 if date == "2020-05-30" else 0.08
        p, kw = simulate_idle_window(SiteTruth(receiver_m3=2.0, base_leak_m3_min=leak), seconds=4 * 3600, seed=d)
        t0 = datetime.fromisoformat(date).replace(hour=1).timestamp()
        nights[date] = {"t": [t0 + i for i in range(0, len(p), 10)], "p": p[::10], "a": [k / 0.6 for k in kw[::10]]}
    _, summary = analyse(nights)
    leak_night = next(n for n in summary["documented_leak_nights"] if n["night"] == "2020-05-30")
    assert leak_night["rank_high_to_low"] == 1 and leak_night["ratio_to_normal_median"] > 2
    assert summary["median_disagreement_pct"] < 10


def test_selfcal_evidence_route(client):
    r = client.get("/api/evidence/selfcal")
    assert r.status_code in (200, 404)


def test_duty_analysis_flags_continuous_and_stuck_nights():
    from metropt_selfcal import duty_analysis
    def night(duty, p_range=0.5):
        n = 720
        loaded = int(duty * n)
        return {"t": list(range(n)), "p": [8.0 + p_range * (i % 7) / 7 for i in range(n)],
                "a": [6.5] * loaded + [0.1] * (n - loaded)}
    nights = {f"2020-05-{d:02d}": night(0.1) for d in range(1, 20)}
    nights["2020-05-30"] = night(1.0)            # documented leak, continuous loading
    nights["2020-05-25"] = night(1.0, p_range=0)  # frozen signal: excluded
    d = duty_analysis(nights, threshold=4.8)
    assert d["stuck_signal_nights_excluded"] == ["2020-05-25"]
    assert d["documented_leak_nights"][0]["rank_high_to_low"] == 1
    assert d["auc_documented_vs_normal"] == 1.0
    assert d["continuous_nights"] == ["2020-05-30"]
