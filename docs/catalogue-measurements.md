# Catalogue Measurements

This document records the measurement history and empirical observations behind
the initial prompt component catalogue.

## Directed Cameras

- Measured in sessions 227 and 228 on directed shoots.
- The 9 furniture-free forms (`front-direct`, `shoulder-left`, `shoulder-right`,
  `side-right`, `side-left`, `behind-direct`, `overhead-direct`, `overhead-high`,
  `floor-low-angle`) reliably establish camera perspective without requiring room
  or furniture detection.
- Bed-anchored forms (such as `Overhead camera directly above the bed` or
  `Side-angle camera at mattress level`) are intentionally omitted from the standard
  catalogue to prevent false furniture insertions in non-bed settings.

## Candid Cameras

- Measured on renders 2026-08-23 (sessions 245-250) across 9 arms and 5 follow-up
  runs with blind evaluation.
- `behind` wordings failed under candid (0/6, returning frontal views).
- `floor` positions failed under candid (0/3).
- Propped mounts (`Phone propped on a high shelf...`) reliably reach overhead perspective
  without requiring a height word.
- Mentioning `phone` does not paint unwanted handheld phones in the frame, except in
  `mirror-selfie` where the mirror reflection naturally shows the device.

## Selfie Cameras

- Based on selfie sessions (sessions 155 and 161).
- Includes candid forms plus two POV forms: `pov-low-chest` (looking down body) and
  `pov-above-back` (looking down while lying on back).

## Arrangements (Acts)

- Evaluated across sessions 265, 266, 269, 270, and 271 with blind judging:
  - `astride`: 18/22 photographs arrived; compatible with front, overhead, mirror, and pov camera families.
  - `reverse`: 3/3 arrived from shoulder family; mirror and overhead rendered weakly.
  - `wall`: 3/3 arrived from mirror family; shoulder is second fallback.
- Dropped arrangements:
  - `back` (0/12 on finepornV4, 0/12 on Krea 2 mix) and `side` (0/9 on finepornV4, 0/8 on Krea 2 mix)
    reliably collapsed to upright front poses.
  - `behind` (all fours) failed across multiple sessions (sessions 155, 161, 267, 268) and produced front or kneeling poses.
