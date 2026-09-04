# perspective-mining Specification

## Purpose

Taking apart a source entry that fuses camera, act, room and wardrobe into one
string, so that each part enters this repo as its own candidate row and can be
judged on its own, instead of arriving as one row whose success nobody can
attribute.

## ADDED Requirements

### Requirement: A fused entry is never imported whole

The system SHALL refuse to store a source entry that names both a camera
position and a bodily act as a single row in any catalogue or seed.

Such an entry SHALL be split into at most one camera candidate, at most one act
candidate and at most one room, each stored in the store that already holds rows
of that kind. A part the entry does not carry SHALL produce no row rather than
an invented one.

Every row produced from a fused entry SHALL record the source identifier it came
from, so that the parts of one source entry can be found again together.

#### Scenario: An entry naming a camera and an act
- **WHEN** a source entry describes both where the camera stands and what the subject is doing
- **THEN** it produces a separate camera candidate, act candidate and room, and no single fused row

#### Scenario: An entry with no act
- **WHEN** a source entry describes only a camera and a room
- **THEN** no act row is produced and nothing is invented to fill the gap

#### Scenario: Finding the parts of one entry
- **WHEN** the rows produced from one source entry are inspected
- **THEN** each names the same source identifier

### Requirement: Template holes are resolved before storage

The system SHALL NOT store a row whose text still contains an unresolved
template placeholder from the source.

Where the source's text carries a hole meant to be filled by another field, the
importer SHALL either fill it from that entry's own material or skip the entry
and report it. A row containing a placeholder SHALL be rejected by the test
suite.

#### Scenario: An entry carrying a placeholder
- **WHEN** a source entry's text contains a hole meant for a pose
- **THEN** the stored row either carries the resolved text or the entry is skipped and reported

#### Scenario: A placeholder reaching a seed
- **WHEN** a seed row is found to contain an unresolved placeholder
- **THEN** the test suite fails and names the row

### Requirement: Mined rows enter unverified

The system SHALL store every mined camera and act candidate as unverified with
no sample size, and SHALL NOT let an unverified mined row be treated as measured
by any consumer.

A mined camera candidate SHALL carry the judge label its slot requires, so that
it can be put to a blind judge without further editing.

A mined row that duplicates the wording of an existing catalogue row SHALL be
reported and SHALL NOT create a second row for the same wording.

#### Scenario: A mined camera candidate
- **WHEN** a camera clause is mined from a source entry
- **THEN** it is stored unverified, with a judge label, and no consumer treats it as measured

#### Scenario: A mined row that already exists
- **WHEN** a mined clause matches the wording of a row already in the catalogue
- **THEN** it is reported as a duplicate and no second row is created

### Requirement: A mined row is given a manner, declared per family

The system SHALL assign every mined row a manner, and the manner SHALL be
declared once per source family rather than decided per row.

A source family whose camera is a participant in the photograph SHALL NOT be
mined into a manner whose instruction says someone is photographing her. Such a
family SHALL be given a manner of its own, created by inheriting an existing one
and changing nothing but its identity, so that no instruction prose is invented
before anything has been measured.

A source family whose camera is an unattended device in the room SHALL be given
the manner that already describes that, rather than a new one.

A manner created this way SHALL carry no instruction prose of its own until the
rows inside it have been judged. Writing that prose SHALL be a later change,
justified by measurements taken inside the manner.

#### Scenario: A participant-camera family
- **WHEN** a source family whose camera is held by a participant is mined
- **THEN** its rows are given a manner of their own, and that manner adds no instruction prose

#### Scenario: An unattended-camera family
- **WHEN** a source family whose camera is a fixed device in the room is mined
- **THEN** its rows are given the existing manner that already describes an unattended camera

#### Scenario: Measuring inside a new manner
- **WHEN** rows in a newly created manner are judged
- **THEN** their verdicts belong to that manner and do not appear in another manner's matrix

### Requirement: A mined act declares what the photograph must provide

The system SHALL set, on every mined act, what a photograph has to provide
before that act can be drawn, using the vocabulary the act catalogue already
uses.

The value SHALL be derived from the act's own wording by the same reading the
existing catalogue uses, and SHALL additionally be floored per source family, so
that a family whose acts always involve a second body carries that requirement
even where one act's wording does not spell it out.

Where the derivation is uncertain, the system SHALL set the requirement rather
than omit it. The two errors are not symmetrical: a requirement set in error
only narrows the pool of a run that has not switched it on, while a requirement
omitted in error deals a two-body act to a single-body photograph, which is a
failure this repo has already shot.

A mined act whose requirement cannot be determined at all SHALL be reported and
SHALL NOT be written.

#### Scenario: An act whose wording names a second body
- **WHEN** a mined act's wording places another person in the photograph
- **THEN** it carries the second-body requirement

#### Scenario: A family whose acts always involve a second body
- **WHEN** one act of such a family does not spell the second body out
- **THEN** it still carries the requirement, from the family floor

#### Scenario: An uncertain derivation
- **WHEN** the reading cannot tell whether an act needs a second body
- **THEN** the requirement is set rather than omitted

#### Scenario: A single-body run
- **WHEN** a run has not switched the second body on
- **THEN** no mined act carrying that requirement is drawn

### Requirement: The combination that rendered is kept reproducible

The system SHALL record, for each mined source entry, the combination of rows it
was split into, so that the photograph the source entry produced can be composed
again exactly.

Mining separates a camera, an act and a room that one author wrote to agree with
each other, and the agreement is what made the entry render. Keeping the parts
without keeping the combination would trade a working photograph for three rows
that have never been seen together.

A recorded combination SHALL be stored as a reference to the rows it names -
their keys and nothing else - and SHALL NOT reproduce any of their text. It is
therefore a record this project owns, kept whether or not the rows it points at
are, in the same way a verdict is.

The recorded combination SHALL be usable to compose a photograph directly,
through the app and without the operator reassembling it by hand from the source
file. It SHALL NOT take part in any random draw.

A recorded combination whose rows have been retired or reworded SHALL be
reported as broken rather than composed from whatever is left.

#### Scenario: Reproducing a source entry
- **WHEN** an operator asks for the photograph a mined source entry produced
- **THEN** the recorded combination composes it from the rows it was split into

#### Scenario: A combination whose parts changed
- **WHEN** a row in a recorded combination is retired or its wording changes
- **THEN** the combination is reported as broken and is not composed from the remaining rows

#### Scenario: A combination holds keys, not prose
- **WHEN** the recorded combinations are read
- **THEN** they carry row keys and no row text

#### Scenario: Composing one from the app
- **WHEN** an operator asks the app to compose a recorded combination
- **THEN** it is composed without them assembling anything by hand

#### Scenario: The draw
- **WHEN** a session draws its trio
- **THEN** no recorded combination is dealt by that draw

#### Scenario: Judging a part against the whole
- **WHEN** a mined row is judged on its own
- **THEN** its verdict is recorded against the row, and the recorded combination is unaffected

### Requirement: New camera concepts are declared, not assumed

The system SHALL name, in the change's own records, each camera concept the
mining introduces that the catalogue did not previously hold, and SHALL leave
each one unverified until it has been judged under the project's existing
judging protocol.

A mined camera concept SHALL NOT be offered as a default, drawn at random, or
used by the composer's strict path until its verdict says verified.

#### Scenario: A concept the catalogue does not hold
- **WHEN** mining produces a camera concept with no existing counterpart
- **THEN** it is recorded as new and stored unverified

#### Scenario: An unverified concept and the strict composer
- **WHEN** the composer runs its strict path and an unverified mined camera is in the pool
- **THEN** it is not drawn
