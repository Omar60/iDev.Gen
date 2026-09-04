# writer-field-set Specification

## Purpose

Which fields the shoot writer answers in, the bar a new one clears before it
ships, and the rule that keeps a field which is usually empty from inventing
something to fill itself.

## ADDED Requirements

### Requirement: A field ships only after it is measured

The system SHALL NOT enable an additional writer field in a shipped shoot until
that field has cleared the project's documented writer-measurement protocol
against the current field set as its control.

The measurement SHALL report, for each field in the candidate arm:

- **arrival** - in how many written lines of a run the field is present and
  non-empty;
- **survival** - whether the camera position and the framing the catalogue
  handed the writer still appear in the composed line at the rate the control
  arm achieves;
- **the effect on the other fields** - the arrival rate of every field the
  control already had.

A candidate arm that lowers survival SHALL NOT ship. A candidate arm that lowers
the arrival of the fields the control already had SHALL NOT ship, whatever the
new field's own arrival rate.

The measurement SHALL be runnable without a GPU, without a running renderer and
without a rendered photograph.

#### Scenario: A candidate that costs the camera or the framing
- **WHEN** the candidate arm's survival falls below the control arm's
- **THEN** the arm does not ship, regardless of how often the new field arrives

#### Scenario: A candidate that costs the other fields
- **WHEN** adding a field lowers the arrival rate of fields the control already had
- **THEN** the arm does not ship, and the report names the fields that fell

#### Scenario: Measuring without hardware
- **WHEN** the measurement runs on a machine with no GPU and no renderer
- **THEN** it completes and reports arrival, survival and the effect on the other fields

### Requirement: An enabled field is enforced in the instruction

The system SHALL, whenever the writer's field list changes, state in the
writer's instruction that every object carries every field, and SHALL name the
current count.

A field SHALL NOT be added to or removed from the list without that statement
being updated in the same change.

The field list the writer is asked for and the headings the joiner knows SHALL
name exactly the same fields. A field present in one and absent from the other
SHALL fail the test suite, because a field with no heading drops the whole line
out of its block format.

#### Scenario: Adding a field
- **WHEN** the writer's field list gains a field
- **THEN** the instruction's count and its all-fields rule are updated in the same change

#### Scenario: A field with no heading
- **WHEN** a field is added to the writer's list and not to the joiner's headings
- **THEN** the test suite fails and names the field

### Requirement: An occasional field is switched on per run, never left empty in the list

The system SHALL treat a field whose subject is absent from most photographs as
a property of the run, switched on or off before the shoot is written, in the
same shape as the existing run-level properties for a second body and for
furniture.

When such a field is **off**, it SHALL NOT appear in the writer's field list,
in the instruction's key header, or in the joiner's headings. It SHALL NOT be
listed and left empty.

When such a field is **on**, the run SHALL supply what it is about - the marking,
the animal, the fluid - and the writer SHALL carry that supplied text forward
across the shoot, repeating it verbatim in the photographs that do not change
it, in the same way the wardrobe is carried.

The writer SHALL NOT invent the subject of such a field. A field switched on
with nothing supplied SHALL be refused before the shoot is written.

#### Scenario: The field is off
- **WHEN** a run does not switch the field on
- **THEN** the field appears nowhere in the writer's instruction and nothing about its subject reaches any line

#### Scenario: The field is on and supplied
- **WHEN** a run switches the field on and supplies its subject
- **THEN** every line carries it, and the lines that do not change it repeat the supplied words verbatim

#### Scenario: The field is on and nothing is supplied
- **WHEN** a run switches the field on without supplying its subject
- **THEN** the run is refused before the shoot is written, and the refusal names the field

#### Scenario: Nothing is invented
- **WHEN** a shoot is written with such a field off
- **THEN** no line describes its subject

### Requirement: A field does not answer what the session already answers

The system SHALL NOT enable a writer field whose question the session's constant
look already answers.

The look is hair, makeup, the place and the light, written once and prepended to
every photograph of the session. A per-photograph field on any of those puts a
second answer to one question into the same composed line: agreement is
repetition, and disagreement is a contradiction, which this sampler resolves by
dropping the position and the framing the catalogue handed the writer.

The writer measurement SHALL NOT be treated as evidence on this point. Arrival
and survival are read off the written line, and the collision does not exist
until the look is prepended, which happens after the writer is finished.

A field of this kind SHALL ship only if the look stops carrying its subject, and
that SHALL be a deliberate change to what a session holds constant rather than a
side effect of adding a field.

#### Scenario: A field the look already answers
- **WHEN** a candidate field describes the subject's hair or her makeup
- **THEN** it does not ship while the session's look defines them

#### Scenario: The measurement does not settle it
- **WHEN** such a field arrives at a high rate and costs the control arm nothing
- **THEN** it still does not ship, because the measurement never sees the composed look

#### Scenario: Moving the subject off the look
- **WHEN** the look stops defining a subject and a field takes it over
- **THEN** the field may ship, and the change to the look is stated in the same change

### Requirement: A field carries one question and not another field's

The system SHALL confine each writer field to its own subject, and SHALL NOT
ship two enabled fields that answer the same question.

In particular: a field for the lens, the capture medium and the photographic
treatment SHALL NOT carry the camera position, the framing or the crop; and it
SHALL NOT be enabled alongside another field that already describes how the
photograph was taken.

A field describing what the subject's skin shows SHALL carry only her body, and
the defects of the camera SHALL belong to the field that describes how the
photograph was taken.

A composed line SHALL place each field where the measurement placed it, and that
position SHALL NOT change without a fresh measurement.

#### Scenario: Two fields answering one question
- **WHEN** a candidate arm enables two fields that both describe how the photograph was taken
- **THEN** the arm does not ship until they are merged or one is switched off

#### Scenario: A field carrying a position
- **WHEN** the lens and treatment field is written with a camera position in it
- **THEN** the measurement reports it as a fault rather than the line being composed silently

#### Scenario: Moving a field
- **WHEN** a field's position in the joined line is changed
- **THEN** the change carries a fresh measurement of that position
