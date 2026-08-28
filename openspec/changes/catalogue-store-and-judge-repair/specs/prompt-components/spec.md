## MODIFIED Requirements

### Requirement: A component's text has exactly one home

The text of a component SHALL appear in exactly one place, and that place SHALL
be the catalogue store. No instruction, no example, no per-manner text and no
compiled-in constant may carry a second copy of a wording that the catalogue
already holds.

A second copy is how a catalogue entry comes to have no effect: whichever copy
the reader meets first is the one that decides the photograph, and the two drift
apart without anything failing.

A file shipped for import is not a second home while nothing reads it
automatically: it is an offer the operator either accepts into the store or does
not. It SHALL be excluded from the single-home check by being the only file
allowed to carry catalogue text, and the check SHALL name it explicitly rather
than skipping any file that happens to look like data.

#### Scenario: A wording is not duplicated as an example
- **WHEN** the prompt system is inspected for the text of any catalogue wording
- **THEN** that text occurs once, in the store, and nowhere else

#### Scenario: The import file is the named exception
- **WHEN** the single-home check runs against a repository carrying the importable measured set
- **THEN** the check passes, and it passes by naming that one file rather than by a pattern that would also excuse a new duplicate

## ADDED Requirements

### Requirement: A component names the view it is written from

A component whose wording places the camera or the body SHALL record which side
of the subject the photograph is taken from, in a form a program can read.

A wording that puts the camera behind her and a wording that describes her from
the front are a contradiction, and this project has measured that the sampler
resolves such a contradiction by keeping the body and discarding the camera. A
contradiction that is only visible by reading English cannot be checked, counted
or reported.

#### Scenario: A camera component declares its view
- **WHEN** a camera component is saved
- **THEN** it carries the view it is taken from, and the value is one the judging and composing paths both read

#### Scenario: Two components disagree about the view
- **WHEN** a camera component taken from behind the subject is drawn together with an act component written from the front
- **THEN** the disagreement is knowable from the components alone, without inspecting their English

### Requirement: A retired component is readable everywhere it was used

A retired component SHALL remain resolvable by key, so that a photograph
composed from it before it was retired still reports what it was composed from,
and its cells still name their components.

#### Scenario: An old photograph names a retired component
- **WHEN** a photograph composed from a component that has since been retired is opened
- **THEN** the component's key, wording and judge label are still shown
