"""Meaningful physics, detection and endpoint checks; no extra test dependency."""

import math
import unittest

from app import app
from simulator import PLANT, compare_runs, detect_alerts, run_simulation


class PrototypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = {name: run_simulation(name) for name in ("normal", "leak", "unloaded")}

    def test_receiver_balance_and_integrated_electricity(self):
        for name, result in self.runs.items():
            with self.subTest(scenario=name):
                self.assertLess(abs(result["balance_residual_reference_m3"]), 1e-9)
                integrated = sum(r["power_kw"] / 3600 for r in result["readings"])
                self.assertAlmostEqual(integrated, result["summary"]["energy_kwh"], places=5)
                self.assertEqual(len(result["readings"]), PLANT.duration_seconds)
                self.assertTrue(all(math.isfinite(r["pressure_bar"]) for r in result["readings"]))

    def test_alerts_are_derived_from_readings(self):
        self.assertEqual(detect_alerts(self.runs["normal"]["readings"]), [])
        for name in ("leak", "unloaded"):
            alerts = detect_alerts(self.runs[name]["readings"])
            self.assertTrue(any(a["key"] == name for a in alerts))
            self.assertTrue(all(0 < a["second"] <= PLANT.duration_seconds for a in alerts))

    def test_pressure_alert_is_detectable(self):
        rows = [dict(r, pressure_bar=5.5) for r in self.runs["normal"]["readings"][:10]]
        self.assertTrue(any(a["key"] == "pressure" for a in detect_alerts(rows)))

    def test_interventions_recompute_and_preserve_demo_output(self):
        for scenario in ("leak", "unloaded"):
            result = compare_runs(scenario)
            self.assertGreater(result["savings_kwh"], 0)
            self.assertEqual(result["baseline"]["accepted_units"], result["after"]["accepted_units"])
            self.assertTrue(result["simulation_guardrails_pass"])
            self.assertAlmostEqual(result["savings_inr"], result["savings_kwh"] * 7, places=1)
        normal = compare_runs("normal")
        self.assertEqual(normal["savings_kwh"], 0)
        self.assertEqual(normal["sec_reduction_pct"], 0)

    def test_price_changes_money_not_physical_results(self):
        zero = compare_runs("leak", 0)
        priced = compare_runs("leak", 9)
        self.assertEqual(zero["savings_kwh"], priced["savings_kwh"])
        self.assertEqual(zero["savings_inr"], 0)

    def test_pages_and_api(self):
        with app.test_client() as client:
            self.assertEqual(client.get("/").status_code, 200)
            for asset in ("style.css", "app.js"):
                response = client.get("/static/" + asset)
                self.assertEqual(response.status_code, 200)
                response.close()
            self.assertEqual(client.get("/api/health").json["status"], "ok")
            run = client.post("/api/simulate", json={"scenario": "leak", "tariff": 7})
            self.assertEqual(run.status_code, 200)
            self.assertEqual(len(run.json["readings"]), 1800)
            compared = client.post("/api/compare", json={"scenario": "unloaded", "tariff": 7})
            self.assertEqual(compared.status_code, 200)
            self.assertGreater(compared.json["savings_kwh"], 0)
            for bad in ({"scenario": "unknown"}, {"scenario": []}, {"tariff": -1}, {"tariff": "7"}, {"tariff": True}, []):
                self.assertEqual(client.post("/api/simulate", json=bad).status_code, 400)
            self.assertEqual(client.post("/api/simulate", data="not json").status_code, 400)


if __name__ == "__main__":
    unittest.main()
