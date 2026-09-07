# FactoryAir Twin — Stage 3: optimiser, fault-type AI and impact reporting

Team Volts and Bolts · Yuva Yodha Challenge 04: Smart Manufacturing

Stage 3 keeps every Stage 2 behaviour (same simulator, same scenarios, same
Stage 2 Isolation Forest, same tests) and adds the intelligence layer the
submission promises: it explains **why** energy is wasted, names the probable
fault, and ranks **what to do first** in ₹, kg CO₂e and payback days.

All data remains simulated. No physical compressor is connected.

## 1. What is new

| Area | Addition | Where |
| --- | --- | --- |
| Predictive maintenance | Two new scenarios: progressive filter blockage and worn compressor, with dedicated engineering rules (loaded pressure-climb rate, power-versus-map ratio) | `simulator.py`, scenario dropdown |
| Explainable leak alerts | Leak alerts now carry estimated loss in kWh/day, ₹/month and kg CO₂e/month, computed from recorded readings only | alert cards |
| Stage 3 AI | Supervised RandomForest that names the fault type (leak / unloaded / filter / worn) with per-class probability gates; 12 causal features incl. 300 s trailing context | `ai_stage3.py`, `train_stage3.py` |
| Setpoint optimiser | Searches lower cut-in/cut-out bands with full matched re-runs; only accepts options that keep accepted output, ≥ 99% pressure compliance and the minimum-pressure guardrail | Optimiser panel, card 1 |
| Multi-compressor sequencing | Separate illustrative two-compressor model comparing lead/lag orders on the same demand | Optimiser panel, card 2 |
| Tariff-aware scheduling | Illustrative arithmetic for shifting production air energy to off-peak / solar-rich tariff windows | Optimiser panel, card 3 |
| Intervention catalogue | Ranked action list (repair, setpoint, sequencing) with monthly ₹, kg CO₂e, assumed cost and payback days | Optimiser panel table |
| Impact strip | kWh / 30 min, projected ₹/month, projected kg CO₂e/month with editable grid factor | Optimiser panel |
| Connectivity sketch | MQTT topics, sample payload and illustrative Modbus register map; PowerTag/edge alignment note | "Edge connectivity" section |
| MetroPT-3 loader | Optional offline script demonstrating feature transfer on the UCI dataset (not bundled) | `metropt3.py` |

## 2. Run it (Windows)

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe train_model.py
.\.venv\Scripts\python.exe train_stage3.py
.\.venv\Scripts\python.exe app.py
```

Open http://127.0.0.1:5001 and press Ctrl+F5. The header now says STAGE 3.
Train once; afterwards only `app.py` is needed. Retrain both models after
changing features, simulator assumptions or the scikit-learn version.

## 3. Demonstration script (about 6 minutes)

1. Load **Normal operation**: replay, show charts, impact strip, empty alert list.
2. Load **Suspected air leak**: replay past ~4:15. Read the quantified alert
   ("Estimated loss ≈ 40 kWh/day ≈ ₹8,400/month"). Note the Stage 3 panel names
   "Air leakage" with its probability.
3. Click **Approve & simulate action** → matched before/after run (≈ 34%
   electricity reduction, guardrails pass).
4. Open **Optimiser / Savings roadmap**: click setpoint, sequencing and the
   tariff scheduler; read the ranked catalogue (leak repair payback ≈ 1 month).
5. Load **Progressive filter blockage** and **Worn compressor**: show the
   maintenance rules and Stage 3 fault naming; approve each intervention.
6. Scroll to **Evaluation**: Stage 2 unsupervised baseline and Stage 3
   supervised results side by side; download both reports.
7. Open **Edge connectivity** to show the Modbus/MQTT integration sketch.

## 4. Stage 3 AI methodology (kept honest)

* **Data splits.** Stage 3 uses its own seed blocks: 20 training runs per
  scenario (seeds 3100–3599), 20 calibration normal runs (3600–3619), and a
  reserved test block of 20 runs per scenario (seeds 9500–9999). The Stage 2
  test seeds (9100–9399) are never reused, so the Stage 2 baseline remains an
  untouched comparison.
* **Labels** come from injected fault timing; windows crossing the onset are
  excluded from window metrics. Features remain causal sensor-derived values;
  labels are used only during training.
* **Gates.** A fault is declared only when a class probability clears its
  per-class gate (99.5th percentile of that class's probability on separate
  calibration normal runs, floor 0.5). This keeps false alarms rare.
* **Result (packaged development run).** Window-level macro-F1 85.9%
  (normal 88.1%, leak 85.5%, unloaded 96.6%, filter 77.2%, worn 82.0%);
  hybrid event detection 77/80 events with median delay 120 s; 2 false-alarm
  episodes in 10 hours of entirely normal runs. Your local run may differ
  slightly with other package versions.
* **Why window recall has a ceiling.** An unloaded-stop fault is only visible
  during unload events; a filter fault is weak during its first five minutes.
  Event-level detection is therefore reported alongside window metrics.
* **Targets.** The submission's ≥ 85% leak/fault detection F1 target is met at
  window-level macro-F1 on synthetic data. Treat this as a prototype result,
  not field evidence; prospective SME data remains future work.

## 5. Optimiser assumptions (all editable / labelled)

* Monthly extrapolation assumes the observed 30-minute pattern repeats
  continuously (1440 blocks per month). Real duty cycles differ.
* Grid factor defaults to 0.79 kg CO₂e/kWh (editable in the impact strip).
* Sequencing uses a separate illustrative two-compressor model (0.8 m³
  receiver, higher demand, simplified lag start/stop logic).
* Tariff scheduling is arithmetic on the simulated specific energy — it does
  not re-simulate physics.
* Repair costs (leak ₹8,000, filter ₹4,500, overhaul ₹25,000) are SME
  illustration defaults, shown next to every payback figure.

## 6. Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Stage 2 suites are unchanged and still guard the original demo numbers.
`tests/test_stage3.py` adds checks for the new scenarios, rules, optimiser
guardrails, catalogue ranking, endpoints and the Stage 3 report (macro-F1
target, event detection ≥ 95%, false-alarm rate, seed disjointness).

## 7. Known limits

* Everything is simulated; savings are matched-run calculations, not measured.
* The MetroPT-3 script is a transfer demonstration only (railway compressor,
  assumed current→kW conversion, no production signal).
* Multi-compressor sequencing uses a simplified controller, not OEM logic.
* Stage 3 probabilities are model outputs, not calibrated field failure odds.
* Operator approval is always required before any real intervention.
