## Purpose

The parts a photograph is composed from — where the camera stands, what the
bodies are doing, how much of her is in frame — held as data with one home each,
so that adding one has an effect and failing one does not erase what was tried.

## ADDED Requirements

### Requirement: A component is a concept carrying candidate wordings

A component SHALL be a concept: a stable key, the slot it fills, and one or more
candidate wordings. A wording SHALL be the exact text placed into the prompt.

Evidence SHALL attach to the wording and never to the concept, because a
photograph that failed to arrive failed from one sentence and not from an idea.
A concept SHALL be usable as long as at least one of its wordings is drawable.

#### Scenario: A concept with two wordings, one of them failed
- **WHEN** a concept holds two wordings and one of them has been measured as failing
- **THEN** the concept remains drawable through its other wording, and the failed wording is not drawn

#### Scenario: Evidence is not shared between wordings
- **WHEN** one wording of a concept is measured
- **THEN** the other wordings of that concept are unaffected and retain their own state

### Requirement: A failed wording is retained and marked, never deleted

A wording measured as failing SHALL be kept in the catalogue, marked failed, and
carry the sample size, manner and checkpoint it failed under.

The system SHALL NOT express failure by removing a component from the catalogue.
Removal destroys the failed wording, which is the one thing a re-test needs, and
a concept whose every wording failed is a candidate for a new wording rather
than a settled question.

A failed wording SHALL NOT be drawn for composition.

#### Scenario: A wording that failed stays readable
- **WHEN** a wording has been measured as failing
- **THEN** it is still present in the catalogue with its sample size, manner and checkpoint, and it is never drawn

#### Scenario: A concept whose every wording failed
- **WHEN** every wording of a concept has failed
- **THEN** the concept is not drawable, and it remains listed so a new wording can be added against it

### Requirement: A component's text has exactly one home

The text of a component SHALL appear in exactly one place. No instruction, no
example and no per-manner text may carry a second copy of a wording that the
catalogue already holds.

A second copy is how a catalogue entry comes to have no effect: whichever copy
the reader meets first is the one that decides the photograph, and the two drift
apart without anything failing.

#### Scenario: A wording is not duplicated as an example
- **WHEN** the prompt system is inspected for the text of any catalogue wording
- **THEN** that text occurs once, in the catalogue, and nowhere else

### Requirement: Every source of a slot's text is a component

Where a slot can be filled from more than one place — including a source that
overrides a previously chosen value for particular photographs, such as the
camera used for a kiss frame — every one of those sources SHALL be a component
in the catalogue.

A source that overrides SHALL record that it overrides, so that a photograph's
final component is knowable rather than inferred from the order things ran in.

#### Scenario: An overriding source is a component
- **WHEN** a photograph's camera is replaced by a source other than the one it was originally dealt
- **THEN** the replacing text is itself a catalogue component, and the photograph records the component it ended up with

### Requirement: A concept may carry a reference image, and it is never shown to a judge

A concept MAY carry a reference image showing the photograph that concept aims
at. The image SHALL be stored as a file alongside the catalogue and SHALL be
available when a wording is being written or reviewed.

The reference image SHALL NOT be shown while a photograph is being judged.
Showing the judge the target is the bias that blind judging exists to remove.

Nothing in the running application SHALL call an external image service to
obtain a reference image; the image is authored outside the app.

#### Scenario: The reference is available while writing a wording
- **WHEN** a new wording is being written for a concept that has a reference image
- **THEN** the image is shown alongside the existing wordings

#### Scenario: The reference is absent from judging
- **WHEN** a photograph is presented for judging and its concept has a reference image
- **THEN** the image is not shown and the brief is not shown
