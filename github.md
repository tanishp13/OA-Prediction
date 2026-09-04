repo: tanishp13/OA-Prediction
branch: main

## Last sync

date: 2026-09-04T16:45:00Z
commit: 33f66a5d9b4d (tree hash from github_get_tree — not a verified commit sha)

Imported the existing prototype (`OA Screening.dc.html`, `android-frame.jsx`, `support.js`, `_ds/`) and refactored it in place per the upgrade brief. Palette and all technical metadata (ESP32, TFLite GBT 184 KB, BLE, SQLite WAL encryption) preserved.

### Updated in this project

- Added a rich landing page with hero, spec-sheet plate, value props, stats band and two role-entry cards (Field app / Supervisor).
- Re-skinned to warm-classical colors + Industry blueprint structure (square corners, hairline frames, `+` registration marks); fonts switched to Cinzel / Newsreader / Space Mono.
- Full-viewport (100dvh) shell with maximized side-by-side field-app + architecture layout.
- Centralized JS translation dictionary; the entire mobile window re-renders instantly across 8 languages (EN/HI/AS verified, BN/BRX/MNI/KHA/LUS best-effort draft).
- Input guards on Worker ID + 4-digit PIN with red outline, shake and localized inline error badges; real-time Offline / Syncing to PHC / Online network toggle.

## Screen map

| Project screen | Repo files |
|---|---|
| OA Screening.dc.html — landing | (new; authored this project) |
| OA Screening.dc.html — field app | OA Screening.dc.html, android-frame.jsx, support.js, _ds/ |
| OA Screening.dc.html — supervisor dashboard | OA Screening.dc.html, support.js, _ds/ |

## Sync history

- 2026-09-04T00:00:00Z — initial import; repo prototype authored from build guide and sprint plan.
