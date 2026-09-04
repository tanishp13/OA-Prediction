# OA Screening Prototype — Hardware Build Guide

**Target:** working Tier-1 demo in 4–5 days, ~₹3,000 of parts, everything available online in India or off-the-shelf in Hyderabad.

**Design principle carried over from the earlier analysis:** measure *upstream* signals (joint surface acoustics, quadriceps strength) for detection, and use gait/ROM for severity staging. Build the phone-only Tier 0 first so you always have something that runs.

---

## 1. Bill of materials

### 1.1 Core kit — build this

| # | Item | Qty | Unit ₹ | Total ₹ | What it gives you |
|---|------|-----|--------|---------|-------------------|
| 1 | ESP32 DevKit V1 (30- or 38-pin, CP2102/CH340) | 1 | 400 | 400 | Runs everything; Wi-Fi SoftAP so no router needed at a camp |
| 2 | MPU6050 / GY-521 6-axis IMU | 2 | 150 | 300 | Knee flexion angle (thigh + shank) |
| 3 | INMP441 I2S MEMS microphone module | 2 | 300 | 600 | Joint acoustic emission, medial + lateral |
| 4 | Load cell, 200 kg bar type (or 100 kg S-type @ ~₹1,100) | 1 | 450 | 450 | Isometric quadriceps strength |
| 5 | HX711 24-bit load cell amplifier | 1 | 120 | 120 | ADC for the load cell |
| 6 | Breadboard 830-point + 65 jumper wires (M-M and M-F) | 1 set | 250 | 250 | — |
| 7 | Micro-USB **data** cable (not charge-only) | 1 | 80 | 80 | Flashing + power |
| 8 | Velcro strap, 25 mm × 2 m | 1 | 120 | 120 | Strapping IMUs to thigh/shank |
| 9 | Nylon webbing 25 mm × 1 m + 2 D-rings + carabiner | 1 | 180 | 180 | Ankle cuff for the dynamometer |
| 10 | 6 mm plywood or acrylic, 2 × 200×150 mm + M5 bolts/nuts | — | 250 | 250 | Dynamometer frame |
| 11 | Micropore surgical tape, 1 inch | 1 | 60 | 60 | Mounting mics at constant tension |
| 12 | Silicone earbud tips, medium | 2 | 40 | 40 | Acoustic coupler for the mics |
| | **Core total** | | | **₹2,850** | |

### 1.2 Cheap add-ons worth having

| Item | ₹ | Why |
|------|---|-----|
| MLX90614 IR thermometer (I2C, 0x5A) | 350 | Left-vs-right knee skin ΔT — cheap inflammation proxy, shares the I2C bus |
| **Digital hanging luggage scale, 50 kg** | 300 | Doubles as your load-cell calibration reference **and** as a zero-code backup dynamometer if the HX711 rig misbehaves. Buy this. |
| USB power bank, 5000 mAh | 500 | Field power for the ESP32 |
| Velostat A4 sheet + copper tape | 400 | DIY pressure insoles (Tier 2, skip for now) |

### 1.3 Tools

Soldering iron 25 W + solder (₹450), multimeter (₹500), hot glue gun (₹250), craft knife, measuring tape, permanent marker. The INMP441 and MPU6050 modules usually ship with headers **unsoldered** — you need the iron.

### 1.4 Deliberately not in the prototype

- **MLX90640 thermal array** (~₹4,000) — Tier 2, keep it as a roadmap slide.
- **Portable ultrasound** — the only modality that sees actual structure. Name it in the pitch as the confirmatory step at district hospital. Do not try to buy one.
- **sEMG (MyoWare 2.0)** — noisy, needs disposable electrodes, adds a day of debugging for a moderate-evidence signal. Cut it.

### 1.5 Where to buy

Online (2–4 day delivery to Hyderabad): Robu.in, Robocraze, ThinkRobotics, Sunrom, Amazon.in.
Walk-in Hyderabad: the electronics market around **Chenoy Trade Centre / SD Road, Secunderabad** carries ESP32, MPU6050, HX711 and load cells same-day. Call ahead for INMP441 — if unavailable, see the piezo fallback in §4.2.

---

## 2. Pin map

Wire everything to one ESP32. Nothing conflicts.

| Peripheral | Signal | ESP32 GPIO |
|---|---|---|
| I2C bus (both MPU6050 + MLX90614) | SDA | 21 |
| | SCL | 22 |
| MPU6050 #1 (thigh) | AD0 → GND | address `0x68` |
| MPU6050 #2 (shank) | AD0 → 3V3 | address `0x69` |
| I2S mics (both share these three) | SCK / BCLK | 14 |
| | WS / LRCL | 15 |
| | SD / DOUT | 32 |
| INMP441 #1 (medial) | L/R → GND | left channel |
| INMP441 #2 (lateral) | L/R → 3V3 | right channel |
| HX711 | DT / DOUT | 18 |
| | SCK | 19 |
| Trial start button | to GND | 4 (internal pull-up) |
| Status LED | — | 2 (onboard) |

Power: MPU6050, INMP441 and MLX90614 all on **3V3**. HX711 on **5V (VIN)** for a bit more headroom — its DT/SCK lines are 3.3 V tolerant in practice, but if you want to be safe, run HX711 on 3V3 too; you lose a little resolution and nothing else.

**I2C cable length:** the IMUs sit on the thigh and shank, so you have 60–100 cm of wire. Use twisted pairs, keep SDA/SCL together with a ground wire, and stay at 100 kHz. The two modules' on-board 4.7 kΩ pull-ups in parallel give ~2.35 kΩ, which is what you want for long runs. If you get NACKs, strap the ESP32 to the thigh and shorten the run.

---

## 3. Software setup (do this before parts arrive)

1. Arduino IDE 2.x → Boards Manager → **esp32 by Espressif, version 2.0.17**. Pin to 2.0.17 deliberately: core 3.x changed the I2S API and every INMP441 tutorial you'll find online is written for 2.x. Do not fight this during a hackathon.
2. Libraries → install **ESPAsyncWebServer** and **AsyncTCP** (Espressif/ESP32Async forks).
3. Board: "ESP32 Dev Module". Upload speed 921600. If upload fails, hold BOOT while it says "Connecting…".
4. Python side: `pip install numpy scipy matplotlib`. `librosa` optional (MFCCs).

---

## 4. Per-sensor build, calibration and protocol

### 4.1 Knee ROM — 2× MPU6050

**Mount.** IMU #1 on the anterior thigh, ~10 cm above the patella. IMU #2 on the anterior shank, ~10 cm below the tibial tuberosity. Both boards flat against the limb, **long axis pointing down the leg**, chip side away from skin, same orientation on both. Velcro strap, snug — a loose IMU is your biggest error source.

**Angle.** Each IMU gives a segment inclination in the sagittal plane from `atan2(ax, az)`, fused with the gyro Y-rate by a complementary filter (α = 0.98 at 100 Hz). Knee flexion = θ_thigh − θ_shank − offset.

**Calibration, every session:**
1. Leave the sensors dead still for 3 s at boot → firmware averages 500 samples for gyro bias. Do not move during the LED blink.
2. Patient stands fully upright → press the button → this becomes 0° flexion.
3. Validate once against a printed paper goniometer or a protractor app at 0°, 45°, 90°. You should be within ±5°. If the angle runs backwards, flip the sign in the firmware — mounting conventions vary and it is not worth deriving.

**What you report:**
- Maximum flexion (normal ≥ 135°; OA commonly loses flexion first)
- **Extension deficit** — angle at maximum voluntary straightening. >5° of fixed flexion is a meaningful clinical sign and an easy win for your report.
- Peak angular velocity during sit-to-stand (functional, not diagnostic)

### 4.2 Joint acoustic emission — 2× INMP441

This is your differentiator. It probes the articular surface instead of inferring from behaviour.

**Coupler.** Cut a silicone earbud tip down to ~8 mm, hot-glue it over the INMP441's port hole so the mic looks into a small sealed air chamber — the same trick a stethoscope diaphragm uses. Bare skin only, no clothing.

**Placement.** One over the **medial** joint line, one over the **lateral** joint line, both with the knee at ~30° flexion so you can palpate the joint space. Tape with micropore, and use the *same number of tape turns every time* — coupling pressure changes amplitude a lot.

**Recording protocol:**
1. Quiet room. Record 3 s of **ambient only** first — you need this for spectral subtraction.
2. **Tap calibration** (do not skip): tap the patella once with a fingertip through a fixed-height drop, record the amplitude at both mics. Soft tissue attenuates, so a heavier patient reads quieter. Normalising every amplitude feature by this tap response stops your model from learning "high BMI = healthy knee." This is cheap and it is exactly the kind of detail a judge will reward.
3. **Unloaded trial:** patient seated on a high chair, foot off the ground, 5 flexion–extension cycles 0–90° paced by a phone metronome at 30 bpm (2 s each direction).
4. **Loaded trial:** 5 sit-to-stand cycles.

Loaded and unloaded signals differ diagnostically — capture both.

**Sync is the point.** The firmware timestamps audio and IMU on the same clock, so every audio frame is indexed to a knee angle. Analysing crepitus *per degree of flexion* rather than per second is what separates this from "we recorded some knee sounds."

**Sampling:** 16 kHz, 32-bit I2S slots, take the upper bits. Most crepitus energy is below 2 kHz but transients go higher — 16 kHz is enough and keeps the Wi-Fi stream comfortable.

**Confounds to control and to say out loud:** skin/clothing rub (bare skin, taped sensor), ambient camp noise (two mics → a genuine joint sound appears at both with a small delay, room noise arrives in phase; use that), coupling pressure (fixed tape turns), BMI (the tap calibration above).

**If INMP441 is unavailable:** 27 mm piezo contact discs (₹30 each) into a TL072 non-inverting buffer — 10 MΩ input resistor, gain ~30×, biased at Vcc/2 — feeding ESP32 ADC pins 34/35 sampled by timer at 16 kHz. It works, but it is an evening of analog debugging you don't need. Order the INMP441s.

### 4.3 Quadriceps strength — load cell + HX711

Best-evidenced upstream marker in the whole stack, and it costs ₹570. Quadriceps weakness precedes and predicts incident symptomatic knee OA.

**Rig.** Sandwich the bar load cell between the two plywood plates (standard bathroom-scale mounting: one end bolted to the bottom plate, the other end to the top plate, with spacers so the beam can flex). Bolt an eye-bolt to the top plate. Anchor the bottom plate to a sturdy chair leg or a wall hook. Webbing cuff around the ankle just above the malleoli → D-ring → carabiner → eye-bolt.

**Geometry that makes the number meaningful:** patient seated, hip 90°, **knee at 60° flexion** (the standard angle for isometric knee extension testing), strap perpendicular to the shank. Measure the lever arm — knee joint centre to the middle of the ankle cuff, in metres — and record it per patient.

```
Torque (N·m) = Force (N) × lever_arm (m)
Normalised   = Torque / body_mass_kg      →  report in N·m/kg
```

Normalising is what makes a 55 kg grandmother comparable to an 85 kg farmer. Raw kilograms are useless.

**Calibration:**
1. Tare with nothing hanging.
2. Hang a known mass. Water bottles are exact: 1 L = 1.000 kg. Use 5 kg and 20 kg points.
3. `scale_factor = (raw_reading − offset) / known_kg`. Put it in the firmware.
4. Cross-check against the ₹300 luggage scale in series. If they disagree by more than 3%, your rig is flexing somewhere.

**HX711 speed:** the module defaults to 10 SPS, which is too slow for rate-of-force-development. Most boards have a RATE pin (pin 15) tied to GND by a trace — cut it and pull to VCC for **80 SPS**. Five minutes with a knife and it is worth it.

**Protocol:** 3 maximal isometric contractions, 5 s each, 60 s rest between. Take the peak of the best trial. Test both legs. Standardised verbal encouragement (say the same thing every time — this genuinely changes results by 5–10%).

**Safety:** maximal isometric effort raises blood pressure. Coach patients to breathe out during the push rather than hold their breath, and skip the test for anyone with uncontrolled hypertension or recent cardiac events. Note this in your protocol slide.

**What you report:** peak torque in N·m/kg, rate of force development (slope over the first 200 ms), and the left–right asymmetry index `|L−R| / max(L,R) × 100%`. Asymmetry >10–15% is a flag.

### 4.4 Skin temperature — MLX90614 (optional, ₹350)

Point it at the medial joint line of each knee from a fixed 2 cm distance, same room, after 10 minutes of acclimatisation (no sun, no recent walking). Report **ΔT between knees**, not absolute temperature — absolute values are dominated by ambient conditions and are worthless in a camp.

A sustained asymmetry above ~0.5–1.0 °C suggests inflammatory activity. Be honest that this is better at flagging inflammatory arthritis than degenerative OA — it is a supporting signal, not a primary one.

### 4.5 Tier 0 — phone only, zero hardware

**Build this first and make sure it works standalone.** It covers the most people and it is your fallback if a wire comes loose during the demo.

- **Risk questionnaire** driving Track 1 — age, sex, BMI, prior knee injury, occupational kneeling/squatting/load-carrying, family history. Build on the published **Nottingham knee OA risk model** rather than inventing weights; citing a validated model beats a bespoke logistic regression you can't defend.
- **30-second chair stand test** — phone timer + tap counter. Age/sex-referenced norms exist (CDC STEADI).
- **Timed Up and Go** — phone timer. >12 s flags mobility impairment.
- **Camera ROM and gait** via MediaPipe Pose (BlazePose) in the browser — knee angle from the hip–knee–ankle landmark triangle, plus cadence, stance-time asymmetry, step length symmetry.
- **WOMAC / KOOS-derived pain and function items**, translated.

Remember the framing: gait is a **severity and functional-impact** measure, not an early-detection measure. It satisfies requirement (a) and shows you know what each signal actually carries.

---

## 5. Data flow

```
ESP32 (SoftAP "OA-Screen", 192.168.4.1)
  ├─ WebSocket /ws
  │    ├─ JSON @50 Hz : {t, angle, thigh, shank, force_kg, temp_c}
  │    └─ Binary frames: 256 stereo int16 audio samples @16 kHz
  └─ HTTP /  → your existing offline PWA
        ├─ IndexedDB write + sync queue        (already built)
        ├─ Feature extraction in worker
        └─ Per-modality scoring + fusion
```

Audio is 16 kHz × 2 ch × 16 bit = 512 kbps. ESP32 Wi-Fi does several Mbps on SoftAP with one client, so streaming is fine and you avoid needing PSRAM. Record to a WAV blob in the browser, run features there or ship to Python for the demo.

---

## 6. The model layer — be realistic

You will not have a trained, validated classifier in four days, and you should not pretend to. What you *can* build and defend:

**Keep modalities separate.** Train or score per-modality heads, fuse in logit space with documented weights, and degrade gracefully when a sensor is missing — which in the field it usually will be. A health worker needs to see "quadriceps strength is low and the joint is noisy" rather than a bare 73%.

**Two outputs, never conflated:**
- **Track 1 — Risk.** No OA now; will they develop it? Questionnaire-driven, Nottingham model.
- **Track 2 — Case finding.** Already symptomatic, never diagnosed. Sensor-driven. For NER this is the higher-value problem: the bottleneck isn't prediction, it's the untreated population that never reaches a clinician.

**For the demo:** rule-based thresholds from published norms, plus an anomaly score for the acoustic channel computed against a small reference set of healthy young knees you collect from teammates (n = 15–20 is enough to show the pipeline). Label the screen honestly — "prototype, not clinically validated, not a diagnosis."

**Draft thresholds to start from** (tune, and cite your sources on the slide):

| Signal | Flag if |
|---|---|
| 30-s chair stand | below age/sex norm (CDC STEADI tables) |
| Timed Up and Go | > 12 s |
| Quad peak torque | < ~1.5 N·m/kg |
| L–R quad asymmetry | > 15% |
| Extension deficit | > 5° |
| Max flexion | < 120° |
| Acoustic anomaly score | > 90th percentile of healthy reference |
| Knee ΔT | > 1.0 °C |

---

## 7. Day-by-day plan

**Day 0 (today)** — Order parts. Install Arduino IDE + esp32 core 2.0.17. Build the Tier 0 PWA screens: questionnaire, chair-stand timer, TUG timer. This alone is a demoable product.

**Day 1** — ESP32 blink, then I2C scan (expect `0x68`, `0x69`, and `0x5A` if you have the MLX). Get both IMUs streaming. Implement the complementary filter. Validate knee angle against a paper goniometer. **Do not move on until the angle is trustworthy** — the acoustic work depends on it.

**Day 2** — Build the dynamometer frame. Wire HX711, calibrate with water bottles, cross-check against the luggage scale. Get peak torque and RDF out. Test on three teammates and confirm the numbers are plausible (young adult male peak knee extension torque is roughly 2.5–3.5 N·m/kg).

**Day 3** — Solder INMP441s, build couplers, get stereo I2S recording to a WAV over WebSocket. Confirm you can *hear* crepitus on a volunteer who has it. Get audio frames tagged with knee angle. Run `vag_features.py` and look at the angle-binned RMS plot — a noisy knee looks obviously different from a quiet one, and that plot is your best demo visual.

**Day 4** — Feature extraction → per-modality scores → fusion → report screen. Collect a small reference set. Wire it into the existing PWA/IndexedDB/referral-slip code.

**Day 5** — Rehearse. Break the rig deliberately (unplug a sensor) and confirm graceful degradation. Prepare the fallback: if the hardware dies on stage, run Tier 0 and show pre-recorded sensor traces.

---

## 8. Pitch notes

Lead with the reframe:

> "Most teams measure gait. Gait change is a pain-avoidance behaviour — it appears after symptoms, which appear after structural change, so it is two steps downstream. We measure the joint surface acoustically and quadriceps strength directly, both of which are upstream, and we use gait for severity staging rather than detection."

Then state the limit before a judge finds it:

> "True early *structural* detection needs T2/T1ρ MRI, and nobody is doing that at a rural health camp. What this system does is high-yield case finding in a population with no orthopaedic access, plus risk stratification from validated factors. Portable ultrasound at the district hospital is the confirmatory step."

Have the honest limits ready: crepitus is common in asymptomatic knees, so specificity is the weak point; published vibroarthrography accuracies come from small selected samples validated against arthroscopy; treat acoustics as a research contribution, not a validated test.

If someone raises the **knee adduction moment** — yes, KAM is genuinely predictive of medial compartment progression and altered loading is thought to be partly causal. But KAM needs force plates plus 3D motion capture and is not recoverable from monocular video, so it does not rescue camera-based gait for early detection. Knowing this answer cold is worth a lot.

---

## 9. Testing on people

Even for an internal hackathon: take verbal consent, explain that this is an untested student prototype and not a diagnosis, don't record identifiable data you don't need, and don't tell anyone they have or don't have osteoarthritis. If a volunteer's numbers look genuinely bad, the correct output is "please see a doctor," not a percentage.
