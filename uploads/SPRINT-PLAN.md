# OA Screening Prototype — 5-Day Sprint Plan

**Assumes:** 6 people, parts in hand on Day 0, submission on Day 5.
**Collapsing to 4 people:** merge Pod D into the other two — D1's clinical work goes to S2, D2's deck/video work goes to H1. Cut the MLX90614 and the district dashboard.

---

## The two rules that decide whether you finish

**1. Contract first.** The JSON and binary schema between the ESP32 and the app is frozen on Day 0, morning, before anyone writes real code. It is already defined in `oa_sensor_node.ino` and mirrored in `mock_node.py`. Nobody changes it after Day 0 without all three pods agreeing in the room.

**2. Always demoable.** From the end of Day 1 onward, there is always a build you could present if the judging happened that afternoon. It gets better each day. It is never broken overnight. This is what stops a team from having nothing at 2 a.m. on Day 4.

The reason `mock_node.py` exists is rule 1. Pod S points at `ws://localhost:8765/ws`, builds the entire capture-process-score-report path against simulated patients, and switches one URL when hardware is ready. **Pod S must never be blocked waiting for Pod H.**

---

## Pod split

### Pod H — Hardware & Firmware (2 people)
**H1 — Sensors & mechanical.** Soldering, mic couplers, IMU straps, dynamometer frame, calibration, physical robustness.
**H2 — Firmware.** `oa_sensor_node.ino`, I2C/I2S bring-up, WebSocket streaming, timing.

### Pod S — Software (2 people)
**S1 — App.** PWA, capture UI, IndexedDB + sync queue, multilingual strings, referral slip, dashboard.
**S2 — Signal & scoring.** `vag_features.py` port or bridge, per-modality scores, fusion, report screen.

### Pod D — Domain, Data & Pitch (2 people)
**D1 — Clinical.** Questionnaire (Nottingham-based), test protocols, published thresholds, citations, consent script, safety notes.
**D2 — Data & story.** Volunteer recruitment, data collection runs, deck, demo video, README, rehearsal.

---

## Day 0 — Setup (half day, everyone)

**All hands, first 90 minutes, in one room:**
- Walk through the data contract line by line. Everyone reads the JSON schema out loud.
- Create the repo. Branches: `firmware/`, `app/`, `analysis/`, `docs/`. Agree on who merges.
- Agree the demo script (see below) *now*. Building toward a known 7-minute demo prevents scope drift.

| Pod | Tasks |
|---|---|
| **H1** | Unpack, inventory, test every module standalone. Solder headers on IMUs and INMP441s. Cut and glue the silicone mic couplers. |
| **H2** | Arduino IDE + **esp32 core 2.0.17** (not 3.x). Install ESPAsyncWebServer + AsyncTCP. Flash blink. Run the I2C scan — confirm `0x68` and `0x69`. |
| **S1** | Scaffold the PWA. Service worker, IndexedDB schema, routing, language switcher with English + Hindi + one NER language stubbed. |
| **S2** | `pip install websockets numpy scipy`. Run `mock_node.py --profile moderate`. Get a browser client connecting and plotting knee angle live. |
| **D1** | Draft the questionnaire. Pull the Nottingham risk model, CDC STEADI chair-stand norms, TUG cutoffs. One page of citations. |
| **D2** | Line up 15–20 volunteers across age ranges. Draft consent script. Book a quiet room for Day 3. |

**Gate:** mock node streaming into a browser chart. If that works, Pod S is unblocked for the whole sprint.

---

## Day 1 — Sensors alive, Tier 0 shippable

| Pod | Tasks |
|---|---|
| **H1** | Build the dynamometer: plywood sandwich, eye-bolt, ankle cuff. Calibrate HX711 with water bottles (1 L = 1.000 kg), cross-check against the luggage scale. Cut the HX711 RATE trace for 80 SPS. |
| **H2** | IMU streaming and complementary filter working. Validate knee angle against a paper goniometer at 0°/45°/90°, ±5°. Flip `ANGLE_SIGN` if needed. **Do not move on until the angle is trustworthy** — everything acoustic depends on it. |
| **S1** | Tier 0 screens done: patient registration, questionnaire, 30-second chair-stand counter, TUG timer. Saves to IndexedDB offline. |
| **S2** | Port the frame-level features from `vag_features.py` into a browser Web Worker, or wire a Python bridge if you'd rather keep it server-side. Validate against the mock stream. |
| **D1** | Write the test protocol card the health worker follows: seating, knee at 60° for the dynamometer, metronome at 30 bpm for flexion cycles, ambient recording first, tap calibration. Laminate it. |
| **D2** | Deck skeleton. Lock the narrative: *gait is downstream, we measure upstream.* |

**Gate (end of Day 1):** you can demo Tier 0 on a phone with no hardware at all. That is your floor for the rest of the week.

---

## Day 2 — Acoustics and strength on real hardware

| Pod | Tasks |
|---|---|
| **H1** | Mount mics on a volunteer. Find someone with audible crepitus and confirm you can *hear* it in the recording. Fix the tape-turn count and write it on the protocol card. |
| **H2** | I2S stereo capture streaming over WebSocket. Verify timestamps line up with the angle telemetry. Tune `AUDIO_SHIFT` — loud enough to see, not clipping. |
| **S1** | Capture screen: connect to node, live angle readout, metronome, record/stop, WAV blob + angle log saved to IndexedDB. |
| **S2** | End-to-end: recorded trial → angle-indexed binning → `vag_profile` plot rendered in the app. Get this working on mock data first, then real. |
| **D1** | Fill the threshold table with sourced numbers: quad torque N·m/kg by age/sex, chair-stand norms, TUG cutoff, extension deficit. Each with a citation. |
| **D2** | Shoot b-roll of the rig being used. You will not have time on Day 4. |

**Gate:** one complete real recording from a real knee, with angle alignment visibly correct.

---

## Day 3 — Data collection and scoring

Morning is a **hard data-collection block**. Everyone except H2 runs sessions.

| Pod | Tasks |
|---|---|
| **All** | 15–20 volunteers, ~12 min each. Consent, questionnaire, chair stand, TUG, tap calibration, ambient, 5 unloaded cycles, 5 sit-to-stands, 3 quad MVICs both legs. Deliberately include some older volunteers and some with known knee pain. |
| **H2** | Meanwhile: graceful degradation. Unplug each sensor in turn and confirm the app reports "sensor absent" instead of crashing. This gets tested on stage whether you plan for it or not. |
| **S2** | Per-modality scores, then logit-space fusion with documented weights. **Keep channels separable in the output** — the report says "quadriceps strength low, joint acoustically noisy," never a bare 73%. |
| **S1** | Report screen, referral slip PDF, prevention/lifestyle guidance content, sync queue flush to the dashboard. |
| **D1** | Compute the healthy reference distribution from the young volunteers. Set the acoustic anomaly threshold at the 90th percentile. |
| **D2** | Deck to 80%. Write the 7-minute script and time it. |

**Gate: FEATURE FREEZE, 8 p.m.** Nothing new after this. Anything unfinished gets cut, not finished.

---

## Day 4 — Polish, rehearse, record

| Pod | Tasks |
|---|---|
| **H1** | Cable-manage everything. Hot glue every connector that isn't screwed down. Build a spare mic assembly. Charge the power bank. |
| **H2** | Bug fixes only. Freeze the firmware by noon and flash the final build. |
| **S1/S2** | Bug fixes only. Test offline mode with airplane mode on. Test the language switcher. Test on the actual phone you will demo with. |
| **D1** | Limitations slide, written in your own words — see below. |
| **D2** | Record the demo video with the working rig **today**, not tomorrow. Full rehearsal, twice, timed. |

**Gate:** a clean run-through with no one touching a keyboard to fix anything.

---

## Day 5 — Submission buffer

Nothing new. README, repo tidy, upload, submit early. Keep the afternoon empty for whatever breaks.

---

## Demo script — 7 minutes

1. **(45 s) The reframe.** "Most teams measure gait. Gait change is pain-avoidance — it appears after symptoms, which appear after structural change. Two steps downstream. We measure the joint surface acoustically and quadriceps strength directly, both upstream, and use gait for severity staging."
2. **(60 s) Tier 0 on a phone.** Questionnaire + chair stand. "This runs on any ₹6,000 Android with no hardware and no network. It is what actually reaches a village."
3. **(90 s) Live capture.** Strap a teammate, five flexion cycles with the metronome, one quad pull. Narrate the protocol card.
4. **(90 s) The money slide.** Acoustic RMS versus flexion angle, healthy knee overlaid on an OA knee. "The peak sits at the same angle every cycle. That repeatability is the signal."
5. **(60 s) The report.** Per-channel breakdown, risk track vs case-finding track, referral slip, offline sync indicator.
6. **(45 s) Limits, stated by you first.** "True early structural detection needs T2/T1ρ MRI, which nobody is doing at a rural health camp. What this does is high-yield case finding in a population with no orthopaedic access. Crepitus occurs in healthy knees too, so specificity is our weak point, and portable ultrasound at district level is the confirmatory step."
7. **(30 s) Cost.** ₹2,400 per unit, one ESP32, all parts available in India.

Practise the answer to **"why not just use a camera and a CNN?"** and to **"what about the knee adduction moment?"** Those are the two questions a knowledgeable judge asks.

---

## Risk register

| Risk | Trigger | Fallback |
|---|---|---|
| INMP441 doesn't arrive | Day 1 | Mechanical stethoscope chest piece + phone mic into the tubing. `vag_features.py` takes the WAV unchanged. |
| I2S garbled / all zeros | Day 2 | Check L/R pin wiring first, then `AUDIO_SHIFT`, then confirm core 2.0.17. |
| I2C NACKs on long leads | Day 1 | Strap the ESP32 to the thigh, shorten the run, drop to 50 kHz. |
| Demo room too noisy | Day 4 | Record the trial in the corridor beforehand; play back a saved session from IndexedDB. Build this replay path on Day 3, not Day 5. |
| Hardware dies on stage | Day 5 | Tier 0 runs on the phone. Pre-recorded sensor traces replay through the same pipeline. |
| Not enough volunteers | Day 3 | 8 is enough to show the pipeline. Say the n out loud rather than hiding it. |

---

## Definition of done

- [ ] Tier 0 works fully offline on a phone with no hardware
- [ ] ESP32 streams angle + force + stereo audio, timestamps aligned
- [ ] One complete trial captures, processes and scores end to end
- [ ] Report shows per-channel results, not a single opaque number
- [ ] App degrades gracefully with any sensor unplugged
- [ ] Airplane-mode capture, then sync when reconnected
- [ ] At least two languages switch correctly
- [ ] Referral slip generates
- [ ] Every threshold on the report traces to a citation in the deck
- [ ] Demo video recorded with working hardware
- [ ] Limitations slide written and rehearsed

---

## A note on the volunteer sessions

Take verbal consent, say plainly that this is an untested student prototype and not a diagnosis, and don't collect identifying details you don't need for the demo. If someone's numbers look genuinely poor, the right output is "it might be worth seeing a doctor about that knee," not a percentage. Also skip the maximal quad test for anyone with uncontrolled blood pressure or a recent cardiac problem, and coach everyone to breathe out during the push rather than hold their breath.
