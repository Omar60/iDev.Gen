# Judging Photographs and the Reading Vocabulary

A judging pass is a blind forced-choice evaluation of photographs against a
specific slot (`camera`, `act`, or `framing`). Photographs are presented
bare — without prompt wording, component keys, or reference images — and
evaluated against a fixed reading vocabulary.

## Reading Vocabulary

A reading is a viewer description of a visible outcome in a photograph. Each
reading has a short `key` (matching the component family) and a `label` (the
description shown to the judge).

Readings must be:
1. *Mutually exclusive*: A photograph falls under at most one choice.
2. *Decidable by landmark*: Settled by visible body or frame landmarks, never
   by fuzzy degrees.
3. *Free of jargon or terms of art*: Plain descriptions, not wordings.
4. *Inclusive of unprompted floor outcomes*: Outcomes the checkpoint produces
   by default (e.g. frontal camera views) must be present in the vocabulary.

## Two Scopes

Readings live in two scopes:
- *Base Readings* (`session_id IS NULL`): Shared across all sessions of that slot
  and manner. Managed from the Catalogue screen.
- *Session Readings* (`session_id` set): Wedged into a specific session for unique
  shoots without polluting the base scope. Managed from the Judging setup screen.

## Collision and Deletion Guards

1. *Bidirectional Collision Checks*: A session reading cannot duplicate a base
   reading key, and a base reading cannot be added if any existing session
   already holds that key.
2. *Scoped Deletion Reference Checks*: A reading cannot be deleted if stored
   verdicts reference it. A session reading scans only that session's shots,
   while a base reading scans all sessions of that manner.

## Pass Pre-Check and Refusal

When a judging pass is requested (`GET /api/sessions/{sid}/judge-pass?slot=<slot>`),
the backend pre-checks all photographed families across both unjudged shots
and control shots.

If any photographed family lacks a reading in either scope, the pass is
**refused 422** naming the missing families and serving no deck.

## Scoring and Control Agreement

- *Hit Scoring*: A judge's answer hits if its mapped family equals the family
  drawn for that photograph (`_reduce(ans) == _reduce(drawn_concept)`).
- *Control Verification*: A sample of already-evaluated photographs is
  interspersed as controls. A re-judged answer agrees with a stored verdict if
  both reduce to the same family. Two empty ("cannot tell") answers agree on
  nothing.
