"""SQLite storage for iDev.Gen — models (characters), sessions, shots, workflows.

Single-user local app: one connection, WAL, check_same_thread off. No ORM on
purpose — four tables and hand-written SQL is less code than the mapping layer.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    graph         TEXT NOT NULL,           -- ComfyUI API-format JSON
    node_map      TEXT NOT NULL,           -- {"positive": "6.inputs.text", ...}
    -- What this graph is for: t2i|edit|angles|scene. Empty means untagged, which
    -- is every workflow imported before kinds existed: it stays offered everywhere.
    kind          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    lora_name     TEXT NOT NULL DEFAULT '',   -- as ComfyUI names it
    trigger       TEXT NOT NULL DEFAULT '',
    lora_strength REAL NOT NULL DEFAULT 1.0,
    base_positive TEXT NOT NULL DEFAULT '',
    base_negative TEXT NOT NULL DEFAULT '',
    workflow_id   INTEGER REFERENCES workflow(id) ON DELETE SET NULL,
    settings      TEXT NOT NULL DEFAULT '{}', -- default width/height/steps/cfg
    notes         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session (
    id            INTEGER PRIMARY KEY,
    model_id      INTEGER NOT NULL REFERENCES model(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'draft',  -- draft|running|done|cancelled|failed
    workflow_id   INTEGER REFERENCES workflow(id) ON DELETE SET NULL,
    -- The graph that edits an existing photo instead of painting one from noise.
    -- Empty means the session is text-to-image only, which is every older session.
    reference_workflow_id INTEGER REFERENCES workflow(id) ON DELETE SET NULL,
    anchor_shot_ids TEXT NOT NULL DEFAULT '[]',   -- shot ids feeding reference/reference2/reference3
    look          TEXT NOT NULL DEFAULT '',       -- hair, makeup, place, light: constant for the shoot
    -- The garments. A *default*, not a constant: a take may carry its own, which
    -- is what lets one shoot walk from dressed to undressed without every take
    -- fighting a sentence that says the jacket is still on.
    wardrobe      TEXT NOT NULL DEFAULT '',
    settings      TEXT NOT NULL DEFAULT '{}',     -- resolved gen settings for the run
    -- Free-text tags the user puts on a session: trimmed, compared
    -- case-insensitively, never empty. Stored as JSON so the list route can read
    -- it whole and no second query is needed. No new table on purpose: four
    -- tables is what this app is shaped to, and a `session_tag` join is a column
    -- the whole point of NOT having.
    tags          TEXT NOT NULL DEFAULT '[]',
    -- The session's manner and checkpoint: the two non-trio dimensions the
    -- cell table is keyed on (design.md decision C). Manner is the camera
    -- list the session draws from (directed/candid/selfie, matching
    -- POSITIONS in kinds.js); checkpoint is the base model the workflow
    -- loads. Strict mode (3.2) checks the cell for (trio, manner,
    -- checkpoint) and refuses a draw whose cell is not verified, so a
    -- session without these is refused on a strict compose — a free
    -- compose (the 3.1 path) is unaffected. Empty default = an older
    -- session that predates 3.2, kept unverified rather than guessed.
    manner        TEXT NOT NULL DEFAULT '',
    checkpoint    TEXT NOT NULL DEFAULT '',
    -- Which room in the catalogue the look above was filled from, by key. It
    -- records provenance and nothing else: `look` still carries the whole
    -- text, so a session composes identically whether this is set or empty,
    -- and clearing the key never touches a word of the look. Empty means the
    -- look was hand-written, which is every session that exists today and
    -- every session whose operator detaches its room afterwards. Not a
    -- foreign key: the catalogue lives in seed files a library can be
    -- unregistered from, and a key whose room is gone is a session that still
    -- has to open.
    room_key      TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shot (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    shot_index    INTEGER NOT NULL DEFAULT 0,
    shot_label    TEXT NOT NULL DEFAULT '',
    prompt        TEXT NOT NULL DEFAULT '',
    negative      TEXT NOT NULL DEFAULT '',
    use_reference INTEGER NOT NULL DEFAULT 0,     -- edit the session's anchor instead of painting from noise
    -- Drop the session's wardrobe from THIS shot's line. The reference channels
    -- only deliver an attribute the line does not already write (measured
    -- 2026-08-31: a written garment beats a reference card 0/9 at every
    -- strength), so a take that hands the wardrobe to a reference has to stop
    -- saying it. The act has no twin column: an empty wording is already a
    -- catalogue component (the `none` control arm), and the wardrobe is the
    -- one piece of the line no slot can silence.
    mute_wardrobe INTEGER NOT NULL DEFAULT 0,
    -- The anchors this shot actually ran against. The session's pick can change
    -- later, so "before vs after" has to compare with what was really used.
    reference_shot_ids TEXT NOT NULL DEFAULT '[]',
    -- NULL = follow the session. Not 0: zero is a real value for this dial, so it
    -- cannot double as "unset" the way an empty seed does.
    reference_strength REAL,
    -- The take this row is a copy of, across cloned sessions: the id of the shot
    -- in the ORIGINAL session, so every copy of one take carries the same value
    -- and NULL means "this is the original". It is what pairs two photos for the
    -- comparison. The seed cannot do that job — reshooting (↺) rolls a new one on
    -- purpose, and the pair has to survive exactly that.
    origin_shot_id INTEGER,
    seed          INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'pending', -- pending|running|done|failed|cancelled
    prompt_id     TEXT NOT NULL DEFAULT '',
    filename      TEXT NOT NULL DEFAULT '',        -- relative to the session folder
    rating        INTEGER NOT NULL DEFAULT 0,      -- 0-5
    -- The three drawn components (camera, act, framing) as (concept, wording)
    -- pairs, JSON-encoded. A written shot leaves this at the empty default
    -- '{}', which is the marker 3.6 uses to tell a composed session from a
    -- written one. A future task (6.2) reads the wording off the row to
    -- know which cell to count the photo toward — the prose does not
    -- survive the round-trip, and a column on `shot` is the only home
    -- this change gives it.
    components    TEXT NOT NULL DEFAULT '{}',
    -- The judging screen's verdict per slot. JSON of
    -- {camera: "wording" | "" | null, act: ..., framing: ...}: a non-null
    -- value means the judge answered that slot (a catalogue key is a
    -- match, "" is "none or cannot tell" per the spec), null means the
    -- question was not asked on this pass. The empty default '' means
    -- the shot has not been judged — 6.2's idempotence marker. A
    -- re-judge on a non-empty value is refused at 409 rather than
    -- silently double-counted (the cell's CHECK would surface a
    -- double-increment as IntegrityError, but the column check is the
    -- upstream gate the user sees).
    verdicts      TEXT NOT NULL DEFAULT '',
    rejected      INTEGER NOT NULL DEFAULT 0,
    error         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    finished_at   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS component (
    id          INTEGER PRIMARY KEY,
    concept_key TEXT NOT NULL,
    slot        TEXT NOT NULL,
    manner      TEXT NOT NULL,
    family      TEXT NOT NULL DEFAULT '',
    faces       TEXT NOT NULL DEFAULT '',
    wording     TEXT NOT NULL,
    judge_label TEXT NOT NULL,
    -- An `act` component's compatible camera FAMILIES, comma-separated,
    -- strongest first. Empty for the other slots and for an act nobody has
    -- measured yet. This is the list `fitCameras` walks: an arrangement handed
    -- a camera that cannot see it renders as a different arrangement, measured
    -- session 267, so the planted photographs take their camera from here.
    --
    -- It is a column and not an if-chain in the frontend keyed on `family`,
    -- which is what it was first: that spelling gave every act added through
    -- the catalogue screen an empty list, so the one thing the screen exists
    -- for produced acts the camera plan silently ignored.
    cameras     TEXT NOT NULL DEFAULT '',
    -- What an `act` needs before it can be photographed at all. Empty is the
    -- answer for every act that needs nothing: pure geometry, photographable
    -- dressed, half-dressed or undressed, which is what makes an act walkable
    -- through an undressing arc without a second column saying where in the arc
    -- it sits. `him` is an act with a second person in it.
    --
    -- It is a REQUIREMENT and not a stage number on purpose. A stage belongs to
    -- the photograph — the wardrobe state it was dealt, and whether he is there
    -- — and an act that carries a stage of its own is an act that can only be
    -- shot once. This column says what the photograph has to provide; the run
    -- says what it is providing.
    --
    -- Backfilled from the wording on migration, and the wording is still the
    -- only place the man is described: `two people in frame` in the text and
    -- `needs='him'` in the column are the same fact, and the migration reads one
    -- to write the other rather than asking anybody to type it twice.
    needs       TEXT NOT NULL DEFAULT '',
    retired_at  TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE(slot, manner, wording),
    CHECK (slot IN ('camera', 'act', 'framing')
           AND wording <> ''
           AND judge_label <> ''
           AND judge_label <> wording)
);

CREATE INDEX IF NOT EXISTS ix_component_slot_manner ON component(slot, manner);

-- The cell is the unit of evidence: a (camera_wording, act_wording,
-- framing_wording, manner, checkpoint) tuple holding the counts that 2.2
-- turns into a verdict. NOT NULL on the five keys is what makes a write
-- missing one a hard rejection; PRIMARY KEY is the row identity, not a
-- separate rule. See task 2.1 of the prompt-component-matrix change: the
-- rejection lives in the schema, not in a Python if. The trio is the unit
-- because a photograph is camera × act × framing — the 9 per-family
-- observations in kinds.js:1962-1986 are (act, family) measurements, and
-- the 4-column key that recorded them under a single (concept, wording)
-- pair was the wrong shape.
CREATE TABLE IF NOT EXISTS cell (
    camera_wording  TEXT NOT NULL,
    act_wording     TEXT NOT NULL,
    framing_wording TEXT NOT NULL,
    manner          TEXT NOT NULL,
    checkpoint      TEXT NOT NULL,
    judged          INTEGER NOT NULL DEFAULT 0,
    arrived         INTEGER NOT NULL DEFAULT 0,
    contradicted    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (camera_wording, act_wording, framing_wording, manner, checkpoint),
    -- NOT NULL alone would let '' through, and '' is this schema's idiom for
    -- "not set" (every other TEXT column here is DEFAULT ''). A cell with
    -- an empty wording is one the cell has nothing to index on. The literal
    -- 'none' is a fact of the measurement (scripts/shoot_arrangements.py:63-77
    -- has no framing, and the act-only and camera-only seeds did not name
    -- the other two slots) and is the only value the synthetic keys take.
    CHECK (camera_wording <> '' AND act_wording <> '' AND framing_wording <> ''
           AND manner <> '' AND checkpoint <> ''),
    -- 2.2 reads a verdict off these. More arrivals + contradictions than judgements is not a
    -- state it can answer, so it never gets stored.
    CHECK (judged >= 0 AND arrived >= 0 AND contradicted >= 0
           AND arrived + contradicted <= judged)
);

CREATE TABLE IF NOT EXISTS reading (
    id         INTEGER PRIMARY KEY,
    slot       TEXT NOT NULL CHECK (slot IN ('camera', 'act', 'framing')),
    manner     TEXT NOT NULL,
    session_id INTEGER REFERENCES session(id) ON DELETE CASCADE,  -- NULL = base
    key        TEXT NOT NULL,
    label      TEXT NOT NULL CHECK (length(trim(label)) > 0),
    -- Which QUESTION this reading answers, '' when the slot only asks one.
    -- Directed's camera vocabulary asks two independent things at once -- where
    -- the camera stood around her, and how high it was -- and both are true of
    -- every photograph, so a pass that offers them in one menu measures which
    -- of the two the judge happened to notice. Measured 2026-09-03, session
    -- 382: a camera side-on in 10 of 10 came back `hip-level` 6 and
    -- `side-level` 1. A pass names the axis it is asking and gets a menu with
    -- one true answer in it.
    axis       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS reading_base ON reading (slot, manner, key)
    WHERE session_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS reading_session ON reading (slot, manner, session_id, key)
    WHERE session_id IS NOT NULL;

-- The wardrobe catalogue. Its own tables and NOT a fourth `component` slot:
-- the component CHECK is the three dimensions of a cell, and a garment is not
-- one of them. A cell keyed on four slots would multiply the matrix by the
-- wardrobe and leave every measurement in it unreachable.
--
-- A garment is ONE piece of clothing, written the way it goes in the line. An
-- outfit is an ordered list of them, and the order is the order they COME OFF —
-- which is the whole reason the catalogue exists: N garments derive N+1 wardrobe
-- states, and each state is composed by naming only what she is still WEARING.
-- A state written as what came off ("The leggings are off") puts the garment's
-- word in the line, the crop law reads it as her feet, and every framing above
-- the knee leaves the pool for the whole run. That was a note somebody had to
-- remember; here it cannot be written.
--
-- No `rung` column. Which part of her a garment reaches is `crop.lowest_named`
-- over its own wording, and that is the same calculation the crop law runs on
-- the composed line. A second copy of it here is a second answer to "how low
-- does this go", and this repo has found that bug four times.
CREATE TABLE IF NOT EXISTS garment (
    id         INTEGER PRIMARY KEY,
    key        TEXT NOT NULL,
    wording    TEXT NOT NULL,
    -- The same garment, moved out of the way instead of taken off: "black cotton
    -- knickers, pulled aside". Empty for a garment that cannot be moved — leggings
    -- and jeans come off or they do not, and there is no third state of them.
    --
    -- It buys a stage the arc could not otherwise reach. An act with a toy in it
    -- does NOT need her undressed, which is what `needs: nude` says; it needs
    -- access, and a garment that can be moved gives access while she is still
    -- wearing it. Without this the arc jumps from `knickers` straight to
    -- `nothing at all` and every such act is a photograph of a naked woman.
    aside      TEXT NOT NULL DEFAULT '',
    retired_at TEXT,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS garment_key ON garment (key);

CREATE TABLE IF NOT EXISTS outfit (
    id         INTEGER PRIMARY KEY,
    key        TEXT NOT NULL,
    label      TEXT NOT NULL,
    -- Garment keys, comma-separated, in the order they come off.
    garments   TEXT NOT NULL,
    retired_at TEXT,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS outfit_key ON outfit (key);

CREATE INDEX IF NOT EXISTS ix_shot_session ON shot(session_id);
CREATE INDEX IF NOT EXISTS ix_session_model ON session(model_id);

-- Resource libraries: a named, safe identity for a source-library the
-- operator has registered. Created on demand by the resource-store
-- service; never created implicitly. Carries no source prose: the
-- library_key is a safe identifier and the display_name is a short
-- human label. The kind column follows the six-value vocabulary the
-- preparation contract in ``backend.resource_prompts`` already names
-- (rooms, fused_scenes, translation_map, cut_map, mined_families,
-- mined_labels) and is otherwise empty.
--
-- Additive on purpose: existing tables are untouched, legacy rows
-- survive every upgrade, and the unique key on library_key is what
-- makes library registration idempotent (a re-register of an existing
-- key returns the existing row, no second row is written).
CREATE TABLE IF NOT EXISTS resource_library (
    id            INTEGER PRIMARY KEY,
    library_key   TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL DEFAULT '',
    kind          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);

-- Immutable asset revisions. A revision is the unit of evidence for a
-- single (library, source_id, content_digest) triple: the full
-- accepted source object is stored as JSON in ``payload`` (preserving
-- every nested structure and every original string verbatim), and the
-- translation and field-coverage data live in their own JSON columns
-- so they can be updated without rewriting the original payload.
--
-- A revision is immutable: there is no UPDATE path, and the unique
-- key (library_id, source_id, content_digest) makes the row identity
-- a property of the content. A second call with the same triple does
-- NOT create a duplicate revision; a second call with a different
-- content (a changed source entry) creates a new immutable revision
-- while the prior one stays readable. This is what the spec means by
-- "explicit uniqueness constraints that prevent duplicate revisions
-- for identical content while allowing a changed source entry to
-- create a new immutable revision".
--
-- Additive on purpose: existing tables and legacy rows are untouched.
CREATE TABLE IF NOT EXISTS asset_revision (
    id             INTEGER PRIMARY KEY,
    library_id     INTEGER NOT NULL REFERENCES resource_library(id) ON DELETE CASCADE,
    source_id      TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    payload        TEXT NOT NULL,           -- complete original accepted object, JSON
    translation    TEXT NOT NULL DEFAULT '{}',  -- English translations, JSON, separate from payload
    coverage       TEXT NOT NULL DEFAULT '{}',  -- field-coverage data, JSON, separate from payload
    created_at     TEXT NOT NULL,
    UNIQUE(library_id, source_id, content_digest)
);

CREATE INDEX IF NOT EXISTS ix_asset_revision_library_source
    ON asset_revision(library_id, source_id);

-- Auxiliary resources: a separate table for the parser's auxiliary
-- outcomes (translation_map, cut_map, mined_families, mined_labels).
-- Auxiliary maps are NOT interpreted as scenes and NOT folded into a
-- scene payload: they are stored as their own immutable revisions
-- under a per-(library, kind, content_digest) unique key. The same
-- idempotence rule the asset_revision table pins applies: identical
-- content creates no duplicate row, changed content creates a new
-- immutable row, and prior rows stay readable. The kind column
-- follows the same six-value vocabulary the parser publishes
-- (translation_map, cut_map, mined_families, mined_labels; the
-- two scene kinds rooms and fused_scenes never land here).
--
-- The complete original auxiliary map is stored as JSON in
-- ``payload``, preserving every nested structure and every original
-- string verbatim, exactly the way asset_revision stores its
-- payload. Provenance lives on the row (library_id and created_at);
-- no UPDATE path is exposed in the Python surface, and the
-- immutability triggers below refuse a hand-rolled UPDATE of the
-- protected columns.
--
-- Additive on purpose: existing tables and legacy rows are
-- untouched, and the CREATE TABLE IF NOT EXISTS makes the
-- migration safe to run repeatedly.
CREATE TABLE IF NOT EXISTS auxiliary_resource (
    id             INTEGER PRIMARY KEY,
    library_id     INTEGER NOT NULL REFERENCES resource_library(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    payload        TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    UNIQUE(library_id, kind, content_digest),
    CHECK (kind IN ('translation_map', 'cut_map', 'mined_families', 'mined_labels'))
);

CREATE INDEX IF NOT EXISTS ix_auxiliary_resource_library_kind
    ON auxiliary_resource(library_id, kind);

-- Immutability guard for auxiliary_resource, mirroring the
-- asset_revision guard above. The columns that define a row's
-- identity (id), provenance (library_id, kind, content_digest),
-- accepted content (payload) and recording time (created_at) MUST
-- NOT be rewritten after the row is written. A direct SQL UPDATE
-- that names one of those columns in its SET clause is rejected at
-- the SQL level with an explicit error and the original row is
-- left intact. This pins the immutability to the schema: a future
-- code path that builds an UPDATE statement cannot quietly bypass
-- the service-level record_auxiliary contract.
CREATE TRIGGER IF NOT EXISTS auxiliary_resource_protect_immutable
BEFORE UPDATE OF id, library_id, kind, content_digest, payload, created_at
ON auxiliary_resource
BEGIN
  SELECT RAISE(ABORT, 'auxiliary_resource is immutable: id, library_id, kind, content_digest, payload and created_at cannot be rewritten after the row is written.');
END;

-- Immutability guard for asset_revision. The columns that define a
-- revision's identity (id), its provenance (library_id, source_id,
-- content_digest), its accepted content (payload) and its recording
-- time (created_at) MUST NOT be rewritten after the row is written.
-- A direct SQL UPDATE that names one of those columns in its SET
-- clause is rejected at the SQL level with an explicit error and
-- the original row is left intact. The translation and coverage
-- columns are deliberately NOT in the OF list: a later task can
-- fill them in or update them without touching the original
-- payload, which is the "translation and coverage data remain
-- separate from the original payload" rule the spec requires.
--
-- The trigger is BEFORE UPDATE OF <col>, not a row-level WHEN
-- clause, so a hand-rolled UPDATE that mentions a protected column
-- is rejected whether or not the new value differs from the old.
-- This pins the immutability to the schema: a future code path
-- that builds an UPDATE statement cannot quietly bypass the
-- service-level record_revision contract, and the only way to
-- rewrite a revision is to DROP the trigger, which is a destructive
-- schema change an operator notices.
--
-- The CREATE TRIGGER IF NOT EXISTS makes this additive: fresh
-- databases get the trigger on the first connect, and an older
-- database that has the table but predates the trigger gets it on
-- the next connect (SCHEMA runs on every db.connect call). No
-- destructive down-migration runs.
CREATE TRIGGER IF NOT EXISTS asset_revision_protect_immutable
BEFORE UPDATE OF id, library_id, source_id, content_digest, payload, created_at
ON asset_revision
BEGIN
  SELECT RAISE(ABORT, 'asset_revision is immutable: id, library_id, source_id, content_digest, payload and created_at cannot be rewritten after the row is written. Only translation and coverage remain independently writable.');
END;

-- Resource-v1 session plan drafts. One current draft per session, with a
-- monotonically increasing `plan_revision` that a compare-and-swap save
-- uses to refuse a stale browser save. The JSON in `plan_json` is the
-- validated draft: stable take IDs, ordered take definitions, constant
-- look, initial wardrobe, selected immutable asset revisions, and
-- explicit wardrobe-change events. The draft is data only in 3.1 —
-- effective wardrobe resolution and prompt preparation live in 3.2 and
-- later tasks.
--
-- The UNIQUE on `session_id` is what pins the "one current draft" rule:
-- a session can have at most one row here, and the CAS write either
-- updates that one row or refuses the save on a stale revision. No
-- second row is ever inserted for the same session.
--
-- Additive on purpose: legacy sessions and legacy plans (which never
-- lived in this table) are untouched, the CREATE TABLE IF NOT EXISTS
-- makes the migration safe to run repeatedly, and `ON DELETE CASCADE`
-- keeps the table tidy when a session is dropped.
CREATE TABLE IF NOT EXISTS session_plan (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL UNIQUE REFERENCES session(id) ON DELETE CASCADE,
    mode          TEXT NOT NULL,
    plan_revision INTEGER NOT NULL,
    plan_json     TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    CHECK (plan_revision > 0)
);

-- Authoritative review approval for resource-v1 plan revisions (task 5.4).
-- One row per session; creating or saving a new plan revision invalidates
-- the approval, and submission routes reject unapproved revisions with
-- HTTP 409.
CREATE TABLE IF NOT EXISTS session_plan_approval (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL UNIQUE REFERENCES session(id) ON DELETE CASCADE,
    plan_revision INTEGER NOT NULL,
    approved_at   TEXT NOT NULL,
    CHECK (plan_revision > 0)
);

-- Prepared-take snapshots for the resource-v1 path (task 3.1,
-- design.md:33). One row per (session, plan_revision, take_id)
-- triple, written by a future preparation task (3.2 / 4.x) and
-- read back when the take is submitted to the runner. The row
-- carries the final prompt, the effective state the prompt was
-- resolved from, the resource-mapping and compiler versions that
-- produced it, the source revisions the prompt cites, the take's
-- status, and an optional link to a shot row that has already
-- been queued or generated.
--
-- The composite UNIQUE on (session_id, plan_revision, take_id) is
-- what pins the "one snapshot per take per plan revision" rule: a
-- future re-prepare for the same take under a new plan revision
-- creates a new immutable row alongside the prior one, the same
-- way ``asset_revision`` does for refreshed source content, and a
-- stale prepare for the same triple is refused at the SQL level.
--
-- The session_id foreign key CASCADEs because a deleted session
-- takes its preparation history with it. The linked_shot_id
-- foreign key SETs NULL because a finished shot is history even
-- when the operator rolls the row back: the prepared snapshot
-- keeps the prompt and the provenance, and only the soft link
-- drops. Status names the four states 3.1 reserves for the
-- future preparation and submission flow: ``pending`` (created,
-- not finalized), ``ready`` (finalized, awaiting submission),
-- ``invalidated`` (superseded by a later plan revision), and
-- ``generated`` (submitted, the linked_shot_id points at the
-- queued or done shot). 3.4 writes pending/ready preparation snapshots;
-- later submission work drives generated rows.
--
-- Additive on purpose: the CREATE TABLE IF NOT EXISTS makes the
-- migration safe to run repeatedly, no existing table is touched,
-- and every legacy row survives.
CREATE TABLE IF NOT EXISTS prepared_take (
    id                INTEGER PRIMARY KEY,
    session_id        INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    plan_revision     INTEGER NOT NULL,
    take_id           TEXT NOT NULL,
    final_prompt      TEXT NOT NULL DEFAULT '',
    effective_state   TEXT NOT NULL DEFAULT '{}',
    mapping_version   TEXT NOT NULL DEFAULT '',
    compiler_version  TEXT NOT NULL DEFAULT '',
    provenance        TEXT NOT NULL DEFAULT '{}',
    status            TEXT NOT NULL DEFAULT 'pending',
    linked_shot_id    INTEGER REFERENCES shot(id) ON DELETE SET NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (session_id, plan_revision, take_id),
    CHECK (plan_revision > 0),
    CHECK (take_id <> ''),
    CHECK (status IN ('pending', 'ready', 'invalidated', 'generated'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_prepared_take_linked_shot
    ON prepared_take(linked_shot_id)
    WHERE linked_shot_id IS NOT NULL;

-- Immutability guard for generated prepared_take rows (task 4.5 of
-- ``adopt-resource-session-planning``). Once a prepared take has been
-- submitted and marked ``generated`` with a linked shot ID, its identity,
-- final prompt, effective state, versions and provenance represent
-- immutable generation history. Direct SQL UPDATEs targeting these
-- columns on a generated row are aborted at the SQL level to protect
-- provenance and snapshot integrity.
CREATE TRIGGER IF NOT EXISTS prepared_take_protect_generated
BEFORE UPDATE OF id, session_id, plan_revision, take_id, final_prompt, effective_state, mapping_version, compiler_version, provenance, status
ON prepared_take
FOR EACH ROW
WHEN OLD.status = 'generated'
BEGIN
  SELECT RAISE(ABORT, 'prepared_take in generated status is immutable history: identity, status, final_prompt, effective_state, mapping_version, compiler_version and provenance cannot be rewritten after submission.');
END;

-- Reviewed adaptations of resource-v1 take descriptive inputs
-- (task 4.2 of ``adopt-resource-session-planning``). One row
-- per accepted (session, plan_revision, take_id, resource
-- triple, field) combination; the UNIQUE constraint makes
-- "one adaptation per conflict" enforceable at the SQL level
-- the same way the asset_revision triple is. The row stores
-- the source value the adaptation was reviewed against and
-- the adapted value the user approved, so the layer that
-- later finalises the take can read the original for
-- provenance without re-querying ``asset_revision``. The
-- original asset_revision row is never rewritten and a
-- adaptation is never turned into a new asset_revision.
--
-- The composite UNIQUE on the seven columns
-- (session_id, plan_revision, take_id, library_key, source_id,
-- content_digest, resource_field) is what pins "a persisted
-- adaptation only resolves the exact conflict it was
-- approved for":
--   * a different take_id is a different row;
--   * a different plan_revision is a different row (the
--     plan-revision bump that a draft save produces does NOT
--     inherit the prior revision's adaptations);
--   * a different content_digest is a different row (a new
--     immutable revision of the same source entry does NOT
--     inherit the prior digest's adaptations);
--   * a different resource_field is a different row (an
--     adaptation of ``prompt`` does NOT resolve a conflict on
--     ``scene_theme``).
--
-- The session_id foreign key CASCADEs because a deleted
-- session takes its reviewed adaptations with it, the same
-- way prepared_take does. created_at/updated_at are the only
-- columns the write path touches; no other column of an
-- existing row is mutable, so a second approved review of
-- the same exact conflict UPDATES updated_at and never
-- rewrites source_value or adapted_value underneath the
-- original reviewer.
--
-- Additive on purpose: the CREATE TABLE IF NOT EXISTS makes
-- the migration safe to run repeatedly, no existing table is
-- touched, and every legacy row survives. The
-- BEFORE UPDATE OF <protected> trigger pins "the conflict
-- identity, the source value and the adaptation identity
-- are immutable" at the SQL level, matching the trigger the
-- ``asset_revision`` table already publishes.
CREATE TABLE IF NOT EXISTS take_resource_adaptation (
    id              INTEGER PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    plan_revision   INTEGER NOT NULL,
    take_id         TEXT NOT NULL,
    library_key     TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    content_digest  TEXT NOT NULL,
    resource_field  TEXT NOT NULL,
    source_value    TEXT NOT NULL,
    adapted_value   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (session_id, plan_revision, take_id, library_key, source_id, content_digest, resource_field),
    CHECK (plan_revision > 0),
    CHECK (take_id <> ''),
    CHECK (library_key <> ''),
    CHECK (source_id <> ''),
    CHECK (content_digest <> ''),
    CHECK (resource_field <> ''),
    CHECK (source_value <> ''),
    CHECK (adapted_value <> '')
);

CREATE TRIGGER IF NOT EXISTS take_resource_adaptation_protect_identity
BEFORE UPDATE OF session_id, plan_revision, take_id, library_key, source_id, content_digest, resource_field, created_at
ON take_resource_adaptation
WHEN NOT (NEW.session_id = OLD.session_id
          AND NEW.plan_revision = OLD.plan_revision
          AND NEW.take_id = OLD.take_id
          AND NEW.library_key = OLD.library_key
          AND NEW.source_id = OLD.source_id
          AND NEW.content_digest = OLD.content_digest
          AND NEW.resource_field = OLD.resource_field
          AND NEW.created_at = OLD.created_at)
BEGIN
  SELECT RAISE(ABORT, 'take_resource_adaptation is immutable: session_id, plan_revision, take_id, library_key, source_id, content_digest, resource_field and created_at cannot be rewritten; only source_value, adapted_value and updated_at may change on re-review');
END;

-- The trigger above lists the protected identity columns
-- in its BEFORE UPDATE OF clause so SQLite only fires
-- when a protected column is targeted. A re-review
-- (UPDATE of ``source_value`` and/or ``adapted_value``
-- together with ``updated_at``) is allowed without
-- raising, and ``updated_at`` may always change. The
-- seven-column identity that pins "a persisted
-- adaptation only resolves the exact conflict it was
-- approved for" stays protected at the SQL level.
"""

_conn: sqlite3.Connection | None = None

# Re-entrant counter for active transactions. Resource import (task 2.3)
# needs atomic multi-statement writes through ``resource_store``; a counter
# is the smallest addition that lets ``db.run`` keep its one-statement
# auto-commit behavior outside transactions and skip it inside one. The
# counter is re-entrant so a caller that nests two ``db.transaction``
# blocks does not have to balance them by hand: the outer commit covers
# the inner block's work, and a rollback in the inner block re-raises
# and undoes both.
_tx_depth: int = 0
_tx_lock = threading.RLock()


@contextlib.contextmanager
def transaction():
    """Atomic multi-statement transaction.

    Inside the ``with`` block, ``db.run`` does NOT auto-commit. The
    block commits as a single unit on normal exit, or rolls back
    every change and re-raises on exception. The counter is
    re-entrant: a nested ``with db.transaction()`` participates in
    the outer transaction, and only the outermost block issues the
    final COMMIT or ROLLBACK.
    """
    global _tx_depth
    with _tx_lock:
        c = conn()
        if _tx_depth == 0:
            c.execute("BEGIN IMMEDIATE")
        _tx_depth += 1
        try:
            yield
        except BaseException:
            if _tx_depth == 1:
                try:
                    c.execute("ROLLBACK")
                except sqlite3.Error:
                    # The connection may already be in a broken state;
                    # surface the original exception, not the rollback error.
                    pass
            _tx_depth -= 1
            raise
        else:
            if _tx_depth == 1:
                c.execute("COMMIT")
            _tx_depth -= 1



def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cell_state(judged: int, arrived: int) -> str:
    """The state a cell's two counts imply.

    Per the component-matrix spec:
    - `verified`: at least 10 photographs judged AND at least 8 arrived
      for every 10 judged (the ratio reading).
    - `dead`: at least 10 photographs judged AND below the 8-in-10 ratio.
    - `unknown`: fewer than 10 photographs judged (whatever the ratio).

    The two readings ("at least 8 arrived" absolute, vs "below 8 of 10"
    ratio) are identical at the n=10 boundary the spec names, and they
    diverge above it: 8 of 20 is 40%, not verified. The strict drawer
    (design.md:218) draws only verified, so the ratio reading is what
    feeds strict. Integer math, no float: `arrived * 10 >= judged * 8`.

    `arrived > judged` is unreachable through the cell table: its CHECK
    rejects the write at insert time, so a state derived from such a count
    is not a state the table can hold. The function does not branch for it
    - that would be testing a case the schema already forbids, and a
    "let me also defensively check" branch is what silently swallows a
    future loosening of the CHECK.
    """
    if judged < 10:
        return "unknown"
    if arrived * 10 >= judged * 8:
        return "verified"
    return "dead"


def connect(path: Path) -> sqlite3.Connection:
    global _conn
    path.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.executescript(SCHEMA)
    _migrate(_conn, path.parent)
    _conn.commit()
    return _conn


def _migrate(conn: sqlite3.Connection, db_dir: Path | None = None) -> None:
    """Bring an older database up to the current schema.

    Only renames and added columns so far, so `ALTER TABLE` covers it and the
    rows survive: a session already shot is someone's afternoon of GPU time.
    """
    def columns(table: str) -> set[str]:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}

    # look_index/look_label -> shot_index/shot_label: a "look" is the wardrobe,
    # which is now a property of the session; the rows are its shots.
    shot_cols = columns("shot")
    for old, new in (("look_index", "shot_index"), ("look_label", "shot_label")):
        if old in shot_cols and new not in shot_cols:
            conn.execute(f"ALTER TABLE shot RENAME COLUMN {old} TO {new}")

    if "garment" in {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}             and "aside" not in columns("garment"):
        conn.execute("ALTER TABLE garment ADD COLUMN aside TEXT NOT NULL DEFAULT ''")

    if "look" not in columns("session"):
        conn.execute("ALTER TABLE session ADD COLUMN look TEXT NOT NULL DEFAULT ''")

    # The garments, split off the look. Nothing is back-filled: an older session
    # keeps its whole look in `look` and shoots exactly as it always did, which is
    # right — its takes were written against that one sentence.
    if "wardrobe" not in columns("session"):
        conn.execute("ALTER TABLE session ADD COLUMN wardrobe TEXT NOT NULL DEFAULT ''")

    # Reference sessions: a second workflow that edits an anchor photo, the anchors
    # it edits, and the per-shot flag saying which takes go through it.
    session_cols = columns("session")
    if "reference_workflow_id" not in session_cols:
        # No REFERENCES clause here: SQLite only accepts one on ADD COLUMN when the
        # default is NULL, and spelling it out would need a full table rebuild for
        # a constraint the routes already enforce.
        conn.execute("ALTER TABLE session ADD COLUMN reference_workflow_id INTEGER")
    if "anchor_shot_ids" not in session_cols:
        conn.execute("ALTER TABLE session ADD COLUMN anchor_shot_ids TEXT NOT NULL DEFAULT '[]'")
    shot_cols = columns("shot")
    if "mute_wardrobe" not in shot_cols:
        conn.execute("ALTER TABLE shot ADD COLUMN mute_wardrobe INTEGER NOT NULL DEFAULT 0")
    if "use_reference" not in shot_cols:
        conn.execute("ALTER TABLE shot ADD COLUMN use_reference INTEGER NOT NULL DEFAULT 0")
    if "reference_shot_ids" not in shot_cols:
        conn.execute("ALTER TABLE shot ADD COLUMN reference_shot_ids TEXT NOT NULL DEFAULT '[]'")
    if "reference_strength" not in shot_cols:
        conn.execute("ALTER TABLE shot ADD COLUMN reference_strength REAL")
    # Nothing is back-filled: the copies made before this column existed are
    # paired by their seed instead, which is what they were paired by all along.
    if "origin_shot_id" not in shot_cols:
        conn.execute("ALTER TABLE shot ADD COLUMN origin_shot_id INTEGER")

    # Session kinds: the tag that says which job a graph does, so picking a kind
    # picks the graph. Untagged is a valid state, not a migration to back-fill.
    if "kind" not in columns("workflow"):
        conn.execute("ALTER TABLE workflow ADD COLUMN kind TEXT NOT NULL DEFAULT ''")

    # Free-text tags on a session: a list the user builds, a column that didn't
    # exist before, default '[]' so a session with no tags reads as an empty list
    # and not a NULL the route has to remember to handle.
    if "tags" not in columns("session"):
        conn.execute("ALTER TABLE session ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'")

    # The three drawn components (camera, act, framing) on a composed shot.
    # A written shot leaves the column at the empty default '{}', and 3.6 uses
    # that empty default to tell a composed session from a written one. The
    # pattern matches `tags` and `kind` above: a TEXT default that survives
    # the round-trip through the row, decoded with `db.jload` at read time.
    if "components" not in columns("shot"):
        conn.execute("ALTER TABLE shot ADD COLUMN components TEXT NOT NULL DEFAULT '{}'")

    # The judging screen's verdicts per slot. Same idiom as `components`:
    # a TEXT JSON column, the empty default '' means "not yet judged",
    # 6.2 reads it to enforce idempotence (a non-empty value means a
    # judge already answered, the second call is a 409) and to compute
    # the per-slot (judged, arrived) delta the cell update carries.
    # A separate `shot_verdict` table would be one column's worth of
    # data, and the row already carries the matching input (the trio
    # in `components`) — the verdicts live next to what they answer.
    if "verdicts" not in columns("shot"):
        conn.execute("ALTER TABLE shot ADD COLUMN verdicts TEXT NOT NULL DEFAULT ''")

    # The session's manner and checkpoint: the two non-trio dimensions the
    # cell table is keyed on. Strict mode (3.2) reads them off the row to
    # check the cell for (trio, manner, checkpoint). Empty default for
    # older sessions, which is the right migration answer for "we don't
    # know what manner or checkpoint this session was shot under" - the
    # alternative (guessing from the model or workflow) is the failure
    # mode this default avoids. The default also keeps the column CHECK
    # honest: a session that never set manner or checkpoint reads as
    # 'unknown' and a strict compose on it fails loudly.
    session_cols = columns("session")
    if "manner" not in session_cols:
        conn.execute("ALTER TABLE session ADD COLUMN manner TEXT NOT NULL DEFAULT ''")
    if "checkpoint" not in session_cols:
        conn.execute("ALTER TABLE session ADD COLUMN checkpoint TEXT NOT NULL DEFAULT ''")

    # The room a session's look was filled from (7.1). Nothing is back-filled
    # and nothing can be: the look is one block of text by the time it is
    # stored, and matching it against the catalogue to guess which room wrote
    # it would invent provenance for a look somebody typed. Empty is the honest
    # answer for every session that predates the column.
    if "room_key" not in session_cols:
        conn.execute("ALTER TABLE session ADD COLUMN room_key TEXT NOT NULL DEFAULT ''")

    # The session's origin: written, composed, or mixed. 3.6's spec
    # scenario "a later comparison can tell which produced which
    # photographs" needs this recorded on the session, not derived
    # from its shots: a draft with zero shots has no answer to
    # derive, and a session that carries both kinds of rows (3.4
    # contemplates this) is information a per-shot scan would
    # collapse. The empty default is the same idiom as manner and
    # checkpoint: a brand-new session has no shots yet, and the
    # first shot's write is what stamps the column. Older sessions
    # are back-filled from the shot table below - unlike manner
    # and checkpoint, the shot table IS a source of truth here
    # (3.1 already wrote components to every composed shot), so
    # the derivation is not a guess. A session with at least one
    # shot gets 'written', 'composed', or 'mixed' from the
    # components JSONs on its rows; a session with zero shots
    # keeps the empty default, which the routes read as
    # "draft, no shots yet".
    if "origin" not in session_cols:
        conn.execute("ALTER TABLE session ADD COLUMN origin TEXT NOT NULL DEFAULT ''")
        # Back-fill: read every shot's components once, bucket per
        # session in Python, write the bucket value. The JSON
        # column needs jload, and a five-line Python scan is
        # clearer than a SQL CASE that has to inspect JSON-as-
        # TEXT. Re-runs of `_migrate` skip the back-fill because
        # the column check above fails the second time around.
        per_session: dict[int, set[str]] = {}
        for row in conn.execute("SELECT session_id, components FROM shot").fetchall():
            per_session.setdefault(row["session_id"], set()).add(row["components"] or "")
        for sid, comp_set in per_session.items():
            has_written = "{}" in comp_set
            has_composed = any(c != "{}" for c in comp_set)
            if has_written and has_composed:
                value = "mixed"
            elif has_composed:
                value = "composed"
            else:
                value = "written"
            # Params as a tuple: this is the raw sqlite3 connection, not
            # `db.run`, and sqlite3.Connection.execute takes (sql, params).
            conn.execute("UPDATE session SET origin=? WHERE id=?", (value, sid))

    # A component store written before the act's camera families became a
    # column: add it empty. An act with no list is one no camera plan will
    # move, which is the same thing the frontend's if-chain did for every act
    # it did not recognise — so an un-backfilled row is no worse off than it
    # was, and the seed carries the measured lists for the three that have one.
    component_cols = columns("component")
    if component_cols and "cameras" not in component_cols:
        conn.execute("ALTER TABLE component ADD COLUMN cameras TEXT NOT NULL DEFAULT ''")

    # A component store written before an act could say what it needs. The
    # backfill reads the wording, because that is where the second person was
    # always written: an act saying `two people in frame` or naming him is an
    # act that cannot be photographed alone, and every other act needs nothing.
    # Reading it rather than asking for it keeps the two spellings of one fact
    # from disagreeing on day one.
    if component_cols and "needs" not in component_cols:
        conn.execute("ALTER TABLE component ADD COLUMN needs TEXT NOT NULL DEFAULT ''")
        conn.execute(
            "UPDATE component SET needs='him' WHERE slot='act' AND ("
            "  wording LIKE '%two people%'"
            "  OR wording LIKE '% him %' OR wording LIKE '% him.%'"
            "  OR wording LIKE '% his %' OR wording LIKE '% he %')")

    # Cell table migration: add contradicted column if not present.
    # If an older database holds rows, dump them to cell-backup-<timestamp>.json
    # before recreating the table in the new shape. The cell table starts empty.
    cell_cols = columns("cell")
    if cell_cols and "contradicted" not in cell_cols:
        existing_rows = [dict(r) for r in conn.execute("SELECT * FROM cell").fetchall()]
        if existing_rows:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            target_dir = db_dir if db_dir is not None else Path("data")
            target_dir.mkdir(parents=True, exist_ok=True)
            backup_file = target_dir / f"cell-backup-{ts}.json"
            backup_file.write_text(json.dumps(existing_rows, indent=2), encoding="utf-8")
        conn.execute("DROP TABLE cell")
        conn.executescript(SCHEMA)

    # Reading table: the vocabulary a judging pass offers. Created on migration
    # if not present; starts empty.
    reading_cols = columns("reading")
    if not reading_cols:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS reading (
            id         INTEGER PRIMARY KEY,
            slot       TEXT NOT NULL CHECK (slot IN ('camera', 'act', 'framing')),
            manner     TEXT NOT NULL,
            session_id INTEGER REFERENCES session(id) ON DELETE CASCADE,
            key        TEXT NOT NULL,
            label      TEXT NOT NULL CHECK (length(trim(label)) > 0),
            axis       TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS reading_base ON reading (slot, manner, key)
            WHERE session_id IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS reading_session ON reading (slot, manner, session_id, key)
            WHERE session_id IS NOT NULL;
        """)
    elif "axis" not in reading_cols:
        conn.execute("ALTER TABLE reading ADD COLUMN axis TEXT NOT NULL DEFAULT ''")


def conn() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("db.connect() not called")
    return _conn


def q(sql: str, *args) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql: str, *args) -> dict | None:
    row = conn().execute(sql, args).fetchone()
    return dict(row) if row else None


def run(sql: str, *args) -> int:
    cur = conn().execute(sql, args)
    # Inside a ``transaction()`` block, the surrounding COMMIT/ROLLBACK
    # is the one that takes effect. Outside, the one-statement auto-commit
    # behavior is unchanged so legacy callers do not have to learn the
    # context manager.
    if _tx_depth == 0:
        conn().commit()
    return cur.lastrowid


def jload(row: dict, *fields: str) -> dict:
    """Decode the JSON-as-TEXT columns of a row in place."""
    for f in fields:
        if isinstance(row.get(f), str):
            try:
                row[f] = json.loads(row[f])
            except json.JSONDecodeError:
                row[f] = {}
    return row
