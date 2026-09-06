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

## One reading answers one question: `axis`

A reading carries an optional `axis` — the question it answers. Directed's
camera vocabulary asks two: `position` (which side of her the camera is on) and
`height` (how far above or below her it sits). They are independent, so a menu
mixing them hands the judge a list where several answers are true at once.
Session 382 measured a camera side-on in 10 of 10 and had it come back
`hip-level` 6 and `side-level` 1 through exactly that door.

So a pass over a vocabulary that asks more than one question **must name one**:
`?axis=position`. A pass that does not is refused 422 listing the axes it found.
A vocabulary where no reading carries an axis asks a single question and needs
no parameter.

## The shipped vocabulary and importing it

Readings ship in `data/readings-seed.json` and are loaded with
`POST /api/readings/import` — with a JSON body of reading objects, or with no
body at all to read that file. The import **keeps** what is already there: an
existing `(slot, manner, key)` is skipped and its label left alone, because
re-importing must never re-word a vocabulary a session was already judged
against. **Import Readings Seed**, beside the base readings on the Catalogue
screen, posts it with no body; the route also takes a JSON body for a
vocabulary of your own.

The seed exists because a judging pass refuses a slot whose photographed
families have no reading — candid had none at all, and writing the twelve it
needed by hand was what stood between a shot session and any number about it.

## Pass Pre-Check and Refusal

A judging pass is requested as
`GET /api/sessions/{sid}/judge-pass?slot=<slot>[&axis=<axis>]`. Three things
refuse it, all before any deck is served:

1. **The slot's catalogue is empty for this session's manner.** Counted per
   manner, not over the whole store: six framings spread across three manners
   still offer nothing to a session in the fourth. Zero components is not a
   question. One component *is* one, now that the screen offers a choice per
   reading rather than per wording — a single reading plus "None or cannot
   tell" is a yes/no question, and it is answerable because the floor is
   measured (the empty prompt renders frontal 10 of 10 on this checkpoint, so
   "did a side view arrive?" has a real negative).
2. **The vocabulary asks more than one question and the caller named none** —
   see `axis` above.
3. **A photographed family lacks a reading in either scope.** The pre-check
   covers both unjudged shots and control shots, and the refusal names the
   missing families.

## Scoring and Control Agreement

- *Hit Scoring*: A judge's answer hits if its mapped family equals the family
  drawn for that photograph (`_reduce(ans) == _reduce(drawn_concept)`).
- *Control Verification*: A sample of already-evaluated photographs is
  interspersed as controls. A re-judged answer agrees with a stored verdict if
  both reduce to the same family. Two empty ("cannot tell") answers agree on
  nothing.
