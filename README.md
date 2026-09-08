# FactoryAir Twin — Stage 2: experimental AI prototype

Team Volts and Bolts · Yuva Yodha Challenge 04: Smart Manufacturing

> **Stage 3 is here.** See **STAGE3.md**: predictive-maintenance scenarios
> (filter blockage, worn compressor), supervised fault-type classifier
> (macro-F1 85.9% on held-out synthetic runs), pressure-setpoint optimiser,
> two-compressor sequencing, tariff-aware scheduling, ranked intervention
> catalogue in ₹ / kg CO₂e / payback days, and an MQTT/Modbus integration
> sketch. Stage 2 behaviour, demo numbers and tests are unchanged.
> New commands: `python train_stage3.py` after `python train_model.py`.

This update keeps your working simulator, dashboard, engineering-rule alerts and
matched intervention comparison. It adds a locally trained Isolation Forest
anomaly detector and a reproducible evaluation on separate simulated runs.
All data and savings remain simulated. No physical compressor is connected.

**The AI is experimental:** the included reference evaluation detected 22 of 24
fault events, but flagged only 15.8% of labelled fault windows. High precision
does not compensate for those misses. The dashboard shows both results, and the
engineering rules remain available. This is not a validated failure predictor.

## 1. Put the files in your existing VS Code project

1. Stop your running app by pressing **Ctrl+C** in its VS Code terminal.
2. Download the update ZIP and use Windows **Extract All**.
3. Copy its contents into your existing `FactoryAirTwin` folder. Choose
   **Replace the files in the destination** when asked. Keep your `.venv` folder;
   it should sit alongside `app.py`, `train_model.py` and `simulator.py`.
4. Open this `FactoryAirTwin` folder in VS Code and choose **Terminal → New Terminal**.

The files are:

| File | Purpose |
| --- | --- |
| `app.py` | Flask application and JSON endpoints |
| `simulator.py` | Physical simulation, production proxy, rules and calculations |
| `ai_detector.py` | Window features, saved-model loading and anomaly scores |
| `train_model.py` | Separate training, threshold calibration and evaluation runs |
| `templates/index.html` | Dashboard structure |
| `static/style.css` | Dashboard appearance and responsive layout |
| `static/ai.css` | Additional styles for the AI and evaluation panels |
| `static/app.js` | Replay, charts, rule/AI results, downloads and action comparison |
| `requirements.txt` | Flask, NumPy, scikit-learn and joblib dependencies |
| `tests/test_prototype.py` | Physics, alert, comparison and API checks |
| `tests/test_ai.py` | Feature isolation, split integrity, model and report checks |
| `reports/reference_validation.json` | Example evaluation from the packaged source; not your live local report |

## 2. Run on Windows

Run these commands **one at a time**. Wait for each to finish:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe train_model.py
.\.venv\Scripts\python.exe app.py
```

Open **http://127.0.0.1:5001** in your browser. Keep the VS Code terminal running.
Use **Ctrl+F5** to refresh the old page. The header should say **STAGE 2**, and the
toolbar should say **Isolation Forest trained locally**. Training prints four
progress steps and finishes with **Training complete**. Train once, then use
only the `app.py` command for later sessions. Retrain after changing model
features, simulator assumptions or the scikit-learn version.

The update deliberately contains no prebuilt model weights. Your training
command creates a model using the packages installed on your own computer.
If you start the app before training, the original simulation still works and
the AI panel explains that the local model is not ready.

If you later move to a fresh folder or computer, create the environment first:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The training script and dashboard require no internet after installing the
dependencies. There are no remote scripts, fonts, APIs or dataset downloads.
Charts are drawn from the simulator output using SVG. pandas is not required.

## 3. Try your first demonstration

1. Open the app. The normal scenario loads and starts replaying.
2. Check that power, pressure, electricity and simulated accepted units change.
3. Select **Suspected air leak**, then click **Load scenario**.
4. Replay beyond approximately 4:15 of simulated time, or move the replay slider.
5. Inspect the suspected-leak alert and its evidence.
6. Click **Approve & simulate action**. This assumes an inspection and repair
   reduces leakage, then recomputes a full run using the same demand schedule.
7. Review calculated electricity savings, specific energy, pressure and output.
8. Repeat with **Extended unloaded running**. The action shortens an illustrative
   stop delay; real restart and wear constraints are not modelled in this stage.
9. Use **Export full run** to download all 1,800 readings as a CSV, regardless of
   the current replay position. The filename explicitly marks them SIMULATED.
10. Watch the **Experimental anomaly detector** panel. It scores 60-second
    windows every 30 seconds and displays only scores up to your replay position.
    It may flag a window and later return below threshold while a fault remains.
    In the reference run, the fixed leak demo first receives an AI flag at 4:00.
    The fixed unloaded demo receives a rule alert but no AI flag. That miss is
    a known limitation of this detector, not evidence that the equipment is normal.
11. Review **How well did the detector perform?** and download the evaluation
    report. These metrics come from separate test simulations, not the three
    fixed replay examples or the leak-repair savings calculation.

Thirty minutes of simulated time take about 30 seconds to replay at 60× speed.
The approval button affects the simulator only. It does not imply a real repair
has happened or grant permission to control equipment.

## 4. Understand the physical model

One compressor supplies a 0.50 m³ receiver. All flows use a common reference
pressure of 1.01325 bar and an assumed constant reference/tank temperature.
The receiver pressure balance for a one-second interval is:

```text
pressure_change_bar = reference_pressure_bar / receiver_volume_m3
                     × (inlet_flow − production_flow − leakage_flow) / 60
```

Flow units are m³/min at the reference conditions. Pressure displays are gauge
bar. This is a simplified isothermal mass balance, not a validated industrial model.

The compressor loads at 6.4 bar(g), unloads at 7.2 bar(g), and stops after the
configured unloaded delay. Its illustrative loaded power is about 7.5 kW,
unloaded power is 2.1 kW, and nominal reference flow is 0.90 m³/min. The small
pressure-dependent flow/power curves are assumptions, not manufacturer data.

Production repeats 210 seconds active and 90 seconds paused. Active reference
air demand is about 0.45 m³/min with a deterministic variation. All scenarios
share the exact same demand schedule and starting pressure.

| Scenario | Leakage at 7 bar(g) | Unloaded stop delay |
| --- | --- | --- |
| Normal | 0.015 m³/min | 20 s |
| Suspected leak | 0.220 m³/min | 20 s |
| Extended unloaded running | 0.015 m³/min | 240 s |

Virtual total outflow is estimated from the known compressor performance map
and receiver pressure change. It includes legitimate production demand and
leakage. The leak rule checks for unusually high estimated outflow during
45 seconds of recorded production inactivity. It cannot locate a branch or
distinguish leakage from unrecorded legitimate air consumption.

The unloaded rule flags 60 consecutive seconds of unloaded operation. These
are illustrative engineering-rule thresholds. AI scores are separate and are
not calibrated probabilities or confidence in a particular fault cause.

## 5. Interpret the results correctly

- Electricity is the integral of simulated kW over time.
- Specific energy is compressor kWh divided by simulated accepted units.
- Each production cycle takes 15 seconds. It counts as accepted only if the
  assumed minimum pressure holds throughout the cycle. This pressure-only proxy
  does not predict product quality or validate real factory throughput.
- Every action reruns the model. Savings are not hardcoded and are not forced to
  match the submitted 10% target. Repeating normal operation gives zero savings.
- Costs use the editable, explicitly assumed electricity price, initially ₹7/kWh.
- The baseline and action runs start identically and cover the same 30 minutes.
  Their final receiver pressures can differ, as shown in the comparison table;
  terminal stored-air changes are not normalized. Longer evaluation periods and
  matched final storage conditions are needed for stronger energy comparisons.
- The original replay and before/after comparison use noise-free readings and
  fixed parameters so your existing demonstration remains reproducible. AI
  training/test runs add parameter variation, pressure bias and sensor noise.
  Startup transients, motor wear, minimum restart times, moisture, detailed
  temperature dynamics and real product quality remain unmodelled.
- Results apply to this simulation and its assumptions. They are not measured
  factory results, guaranteed savings, or a validated predictive-maintenance claim.

## 6. How the AI experiment works

**Inputs:** simulated pressure, electrical power, compressor state and production
active/paused status. A future installation would need the state from the
controller/PLC and a calibrated compressor map. Temperature is displayed but
is not an input to this first detector.

Each trailing window produces four features:

| Feature | Interpretation |
| --- | --- |
| Demand-adjusted air balance | Estimated total outflow minus nominal production demand, in reference m³/min |
| Unloaded fraction | Fraction of the window spent unloaded |
| Longest unloaded spell | Longest continuous unloaded spell within the window, in seconds |
| Idle motor power | Power consumed during production pauses, averaged across the whole window, in kW |

The air estimate uses the receiver mass balance averaged over the window.
It uses an assumed nominal map and receiver size, which differ from the true
parameters in the varied runs. It is not a measured flow value or an exact leak
estimate during production. Window extraction never reads scenario names,
injected fault labels, repair settings or future readings.

| Dataset split | Runs | Purpose |
| --- | ---: | --- |
| Training | 40 normal | Fit an Isolation Forest with 160 trees |
| Calibration | 12 other normal | Set the threshold at the 98th percentile of normal anomaly scores |
| Test | 36 other runs: 12 normal, 12 leak, 12 unloaded | Measure performance without fitting or tuning on test labels |

Each run lasts 30 minutes. Runs have distinct reproducible seeds, and all
windows from a run stay in its split. Sixty-second windows overlap at a
30-second stride, so windows within a run are correlated. There are 2,360
training windows, 708 calibration windows and 2,100 evaluated test windows.

Faults are injected at second 600 in the fault test runs. Labels come from that
injection schedule, not the engineering-rule alerts. The 24 windows that cross
the fault onset are excluded from the window metrics. Some fault-labelled
windows are difficult to distinguish from normal operation, especially while
an unloaded-stop fault has no effect on the current compressor state.

The varied simulations use these illustrative ranges:

| Setting | Range |
| --- | --- |
| Receiver volume | 0.47–0.53 m³ |
| Flow and power map multipliers | 0.96–1.04 each |
| Production demand multiplier | 0.80–1.15 |
| Production block / active fraction | 240, 300 or 360 seconds / 55–80% |
| Normal leakage | 0.008–0.025 m³/min at 7 bar(g) |
| Injected leakage | 0.12–0.30 m³/min at 7 bar(g) |
| Normal / injected unloaded-stop delay | 15–30 / 90–240 seconds |
| Cut-in / cut-out pressure | 6.35–6.50 / 7.10–7.25 bar(g) |
| Initial pressure | 6.8–7.2 bar(g) |
| Pressure sensor bias | −0.02 to +0.02 bar |
| Gaussian measurement noise, standard deviation | 0.004 bar pressure; 0.025 kW power |

All splits use the same simplified simulator family. This is limited
distribution variation, not evidence of performance in unseen factories.
The script does not model missing readings or learn a map from real data.

The normal-range observations displayed with the score compare individual
features with their 2.5–97.5 percentile training ranges. They are neither causal
explanations nor formal attributions of the Isolation Forest's decision.
The score has no percentage interpretation. The AI does not identify a precise
leak location, forecast a failure, choose an intervention or change savings.

## 7. Read the evaluation report

Training creates these files locally:

| Generated file | Contents |
| --- | --- |
| `models/factoryair_isolation_forest.joblib` | Your locally fitted model |
| `models/model_metadata.json` | Feature schema, threshold and package version |
| `reports/validation_report.json` | Your actual evaluation results and limitations |
| `data/training_features.csv` | Normal training features and their run seeds |
| `data/test_predictions.csv` | Test features, independent injection labels, scores and predictions |

The packaged `reports/reference_validation.json` records the development run
with Python 3.12.13, NumPy 2.3.5 and scikit-learn 1.8.0. Its window precision is
96.7%, recall 15.8%, F1 27.2% and normal-window false-positive rate 0.43%.
Counts: 148 true positives, 5 false positives, 788 false negatives and 1,159
true negatives. Two of 24 fault events were entirely missed. The median first
detection delay among the 22 detected events was 105 seconds.

There were no alarm episodes in the 6 hours of entirely normal test runs.
The five false-positive windows occurred before fault injection in other test
runs; they are included in the overall false-positive rate. Event detection
means at least one post-onset flag, so it can be high even when window recall
is low. Do not describe 22/24 event detection or 96.7% precision as overall
accuracy. The interface shows your locally generated report, and package
versions may change the exact numbers.

Keep this evaluation as a baseline. Improve future versions using a separate
development set, and reserve fresh test runs for the final evaluation. Do not
repeatedly tune thresholds on these test runs and still call them unseen.
Real fault/maintenance histories and prospective testing are needed for a
predictive-maintenance claim.

Only load model files produced by your own trusted training script. This app
loads one fixed local path and has no model upload endpoint. Scikit-learn model
files should not be transferred between incompatible package versions.

## 8. Run the checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Checks cover the original receiver balance, energy, rules and comparisons,
along with separate dataset splits, feature/label separation, absence of future
data in windows, missing-model behavior, report arithmetic and application
routes. Model-dependent checks require the training command to have completed.
They do not require a particular accuracy or force a savings percentage.

The simulator can also run without the dashboard:

```powershell
.\.venv\Scripts\python.exe simulator.py
```

## 9. What to build next

After you can run and explain this version:

1. Collect separately labelled development data to investigate missed fault
   windows, then evaluate changes on a fresh, untouched test set.
2. Add missing readings, changing demand and equipment ageing; calibrate the
   performance map and receiver model using measured data.
3. Validate engineering rules on noisy signals and review event definitions
   with an engineer before drawing operating conclusions.
4. Test a separate recorded compressor-data workflow, for example MetroPT-3,
   with appropriate railway-to-SME transfer limitations. Do not use its motor
   current as measured kW or infer real accepted production counts from it.
5. Later add metered power, production counts, calibrated pressure sensors and
   an operator-approved pilot. Advanced failure prediction needs suitable
   failure histories and prospective validation.

## Troubleshooting

- **Python file not found:** open the folder that actually contains `app.py`,
  then open a new VS Code terminal there.
- **Virtual environment executable not found:** use your original project folder
  containing `.venv`, or create the environment using the commands above.
- **Browser cannot connect:** keep `app.py` running and use port **5001**.
- **Port 5001 already in use:** stop the other instance of this app with Ctrl+C.
- **Changes do not appear:** stop/restart the app and refresh the browser with Ctrl+F5.
- **Model not trained or version changed:** stop the app, run `train_model.py`,
  wait for completion and start `app.py` again.
- **ModuleNotFoundError:** run the requirements installation command using the
  same `.venv` Python executable you use to start the app.
- **No compatible package wheel:** check the package's Python support; a separate
  Python 3.12 or 3.13 environment is an alternative to building packages manually.

## Stage 4 — production-readiness layer

Stage 4 adds a calibration panel, sensor-health monitoring, unknown/multiple
fault handling, a persistent maintenance workflow with measured post-repair
verification, a double-count-proof combined savings calculator, a
downloadable PDF report and a stress-test mode. Details: **STAGE4.md**.

One extra dependency for the PDF report:

```
.\.venv\Scripts\python.exe -m pip install fpdf2
```

No model retraining is needed: the simulator's Stage 3 behaviour is unchanged.

## References for the next learning steps

- Flask installation: https://flask.palletsprojects.com/en/stable/installation/
- Isolation Forest: https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html
- Model persistence: https://scikit-learn.org/stable/model_persistence.html
- MetroPT-3 dataset: https://archive.ics.uci.edu/dataset/791/metropt%2B3%2Bdataset

No third-party dataset or trained weights are distributed in this update.
