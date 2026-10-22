/*
  FactoryAir Edge Node - ESP32-S3 firmware (Stage 2 design, NOT yet flashed)

  Reads the two signals FactoryAir Twin needs and nothing else:
    * header pressure  - 4-20 mA transmitter (0-16 bar) across a 165 ohm
                         precision shunt into ADS1115 channel A0 (0.66-3.30 V)
    * motor current    - split-core CT (SCT-013-030, 30 A : 1 V) on one phase
                         into ADS1115 channel A1 (differential, AC-coupled)
    * production flag  - optional dry contact (machine-on relay) on GPIO 7

  Every second it builds one frame
      {"t": <unix s>, "pressure_bar": 6.84, "power_kw": 7.41, "production": true}
  and POSTs batches of 10 frames to  http://<twin-host>:5001/api/edge/ingest
  - the same endpoint and validation the software emulator (edge_emulator.py)
  uses today. If Wi-Fi is down, frames are appended to /buffer.jsonl on the
  microSD card and replayed oldest-first when the link returns (72 h at 1 Hz).

  The node is READ-ONLY: it has no relay or analogue output and never sends a
  command to the compressor.

  Status: written for the Stage 2 design review against Arduino-ESP32 3.x,
  Adafruit_ADS1X15 and ArduinoJson 7. It has NOT yet been compiled or tested
  on hardware (no prototype budget yet); edge_emulator.py exercises the same
  frame format and endpoint in software. Wiring: firmware/README.md.
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <SD.h>
#include <time.h>
#include <Adafruit_ADS1X15.h>
#include <ArduinoJson.h>

// ------------------------------------------------------------------ site config
static const char* WIFI_SSID     = "FACTORY_WIFI";
static const char* WIFI_PASSWORD = "change-me";          // set on site, never committed
static const char* TWIN_URL      = "http://192.168.1.50:5001/api/edge/ingest";
static const float LINE_VOLTAGE  = 415.0f;               // 3-phase line-to-line
static const float POWER_FACTOR  = 0.86f;                // loaded screw compressor, site-measured once
static const float SHUNT_OHMS    = 165.0f;
static const float P_RANGE_BAR   = 16.0f;                // transmitter span 0-16 bar(g)
static const float CT_AMPS_PER_V = 30.0f;                // SCT-013-030
static const uint8_t PIN_SD_CS   = 10;
static const uint8_t PIN_PROD    = 7;                    // dry contact to GND, INPUT_PULLUP
static const uint8_t BATCH       = 10;

Adafruit_ADS1115 ads;
JsonDocument batchDoc;
JsonArray batchArr;
bool sdReady = false;

// ------------------------------------------------------------------ sensing
float readPressureBar() {
  // ADS1115 GAIN_ONE: +/-4.096 V, 125 uV/bit
  float volts = ads.computeVolts(ads.readADC_SingleEnded(0));
  float mA = volts / SHUNT_OHMS * 1000.0f;
  if (mA < 3.6f || mA > 21.0f) return NAN;               // open loop / transmitter fault
  return (mA - 4.0f) / 16.0f * P_RANGE_BAR;
}

float readPowerKw() {
  // RMS over ~10 mains cycles (200 ms) on the differential CT channel.
  const uint16_t N = 160;
  double sumSq = 0;
  for (uint16_t i = 0; i < N; i++) {
    float v = ads.computeVolts(ads.readADC_Differential_1_3());
    sumSq += (double)v * v;
  }
  float irms = sqrt(sumSq / N) * CT_AMPS_PER_V;
  return sqrt(3.0f) * LINE_VOLTAGE * irms * POWER_FACTOR / 1000.0f;   // balanced 3-phase
}

// ------------------------------------------------------------------ transport
bool postBatch(const String& body) {
  if (WiFi.status() != WL_CONNECTED) return false;
  HTTPClient http;
  http.begin(TWIN_URL);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(body);
  http.end();
  return code == 200;
}

void spool(const String& body) {
  if (!sdReady) return;
  File f = SD.open("/buffer.jsonl", FILE_APPEND);
  if (f) { f.println(body); f.close(); }
}

void replaySpool() {
  if (!sdReady || WiFi.status() != WL_CONNECTED || !SD.exists("/buffer.jsonl")) return;
  File f = SD.open("/buffer.jsonl", FILE_READ);
  File keep = SD.open("/buffer.tmp", FILE_WRITE);
  bool linkOk = true;
  while (f.available()) {
    String line = f.readStringUntil('\n');
    if (line.length() < 3) continue;
    if (linkOk && postBatch(line)) continue;
    linkOk = false;
    keep.println(line);                                   // keep what could not be sent
  }
  f.close(); keep.close();
  SD.remove("/buffer.jsonl");
  SD.rename("/buffer.tmp", "/buffer.jsonl");
}

// ------------------------------------------------------------------ main loop
void setup() {
  Serial.begin(115200);
  pinMode(PIN_PROD, INPUT_PULLUP);
  Wire.begin();
  if (!ads.begin()) { Serial.println("ADS1115 missing - check I2C wiring"); }
  ads.setGain(GAIN_ONE);
  ads.setDataRate(RATE_ADS1115_860SPS);
  sdReady = SD.begin(PIN_SD_CS);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  configTime(19800, 0, "pool.ntp.org");                   // IST, UTC+5:30
  batchArr = batchDoc.to<JsonArray>();
}

void loop() {
  static uint32_t lastTick = 0;
  if (millis() - lastTick < 1000) return;
  lastTick = millis();

  time_t now = time(nullptr);
  float p = readPressureBar();
  float kw = readPowerKw();
  if (isnan(p) || now < 1700000000) return;               // no valid pressure or clock yet: skip the frame

  JsonObject frame = batchArr.add<JsonObject>();
  frame["t"] = (uint32_t)now;
  frame["pressure_bar"] = roundf(p * 1000) / 1000;
  frame["power_kw"] = roundf(kw * 100) / 100;
  frame["production"] = digitalRead(PIN_PROD) == LOW;

  if (batchArr.size() >= BATCH) {
    String body;
    serializeJson(batchDoc, body);
    if (!postBatch(body)) spool(body); else replaySpool();
    batchDoc.clear();
    batchArr = batchDoc.to<JsonArray>();
  }
}
