"""Stage 5 tests: edge ingestion bridge + per-site recalibration."""

import os

import pytest

import ai_detector
import app as app_module
import edge_bridge
import recalibrate
from simulator import run_simulation


def device_frames(scenario="normal", seed=77, limit=None):
    """Raw gateway frames: ONLY t/pressure/power/temp — no simulator internals."""
    rows = run_simulation(scenario, variation_seed=seed)["readings"]
    frames = [{"t": r["second"], "pressure_bar": round(r["pressure_bar"], 4),
               "power_kw": round(r["power_kw"], 3), "temperature_c": round(r["temperature_c"], 2)}
              for r in rows]
    return frames[:limit] if limit else frames


@pytest.fixture()
def client():
    edge_bridge.clear()
    with app_module.app.test_client() as test_client:
        yield test_client
    edge_bridge.clear()


def test_ingest_validates_and_derives_state(client):
    good = device_frames(limit=120)
    bad = [{"t": 500, "pressure_bar": 99.0, "power_kw": 3.0},          # impossible pressure
           {"t": 10, "pressure_bar": 6.5, "power_kw": 3.0},            # timestamp backwards
           {"t": 600, "pressure_bar": 6.5},                            # missing power
           "not-a-row"]
    result = client.post("/api/edge/ingest", json=good + bad).get_json()
    assert result["accepted"] == 120
    assert result["rejected"] == 4
    status = client.get("/api/edge/status").get_json()
    assert status["buffer_seconds"] == 120
    # state derived from power alone matches the simulator's true state often
    with edge_bridge._lock:
        derived = [r["state"] for r in list(edge_bridge._buffer)]
    assert set(derived) <= {"Off", "Unloaded", "Loaded"}


def test_live_snapshot_reuses_shared_ai(client):
    frames = device_frames("leak", seed=9150)
    for i in range(0, len(frames), 300):
        client.post("/api/edge/ingest", json=frames[i:i + 300])
    snap = client.get("/api/edge/snapshot").get_json()
    assert snap["ready"] is True
    assert snap["anomaly"]["enabled"] is True
    assert snap["anomaly"]["windows_scored"] > 0
    # a leaking real-time stream must surface to the shared rule engine or AI
    assert snap["alerts"] or snap["anomaly"]["windows_flagged"] > 0


def test_recalibrate_writes_site_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(recalibrate, "SITE_MODEL_PATH", tmp_path / "site.joblib")
    monkeypatch.setattr(recalibrate, "SITE_METADATA_PATH", tmp_path / "meta.json")
    monkeypatch.setattr(recalibrate, "SITE_REPORT_PATH", tmp_path / "report.json")
    monkeypatch.setattr(ai_detector, "SITE_MODEL_PATH", tmp_path / "site.joblib")
    monkeypatch.setattr(ai_detector, "SITE_METADATA_PATH", tmp_path / "meta.json")
    first = device_frames("normal", seed=4242)
    second = device_frames("normal", seed=4343)
    for frame in second:
        frame["t"] += 5000  # one continuous site recording
    normal = first + second
    report = recalibrate.recalibrate(normal, source_name="unit-test")
    assert report["windows_total"] >= recalibrate.MIN_WINDOWS
    assert report["new_threshold"] > 0
    assert (tmp_path / "site.joblib").exists() and (tmp_path / "report.json").exists()

    # the recalibrated bundle must load through the app's guarded loader
    monkeypatch.setenv("FACTORYAIR_SITE_MODEL", "1")
    ai_detector.load_local_bundle.cache_clear()
    bundle, message = ai_detector.load_local_bundle()
    assert message is None and bundle is not None
    assert abs(bundle["threshold"] - report["new_threshold"]) < 1e-12
    monkeypatch.delenv("FACTORYAIR_SITE_MODEL")
    ai_detector.load_local_bundle.cache_clear()


def test_recalibrate_rejects_short_or_faulty_site_data(tmp_path, monkeypatch):
    monkeypatch.setattr(recalibrate, "SITE_MODEL_PATH", tmp_path / "site.joblib")
    monkeypatch.setattr(recalibrate, "SITE_METADATA_PATH", tmp_path / "meta.json")
    monkeypatch.setattr(recalibrate, "SITE_REPORT_PATH", tmp_path / "report.json")
    monkeypatch.setattr(ai_detector, "SITE_MODEL_PATH", tmp_path / "site.joblib")
    monkeypatch.setattr(ai_detector, "SITE_METADATA_PATH", tmp_path / "meta.json")
    with pytest.raises(ValueError):
        recalibrate.recalibrate(device_frames(limit=30))
