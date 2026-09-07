## MODIFIED Requirements

### Requirement: A fused entry is never imported whole

The requirements below apply to decomposition into measured component rows and legacy room seeds. Accepted complete source objects SHALL also be stored intact in the private resource database, including unresolved placeholders as source data. Such placeholders SHALL block prompt finalization until explicitly resolved. Decomposition SHALL NOT be a prerequisite for resource storage or resource-session preparation.

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

The requirements below apply to decomposition into measured component rows and legacy room seeds. Accepted complete source objects SHALL also be stored intact in the private resource database, including unresolved placeholders as source data. Such placeholders SHALL block prompt finalization until explicitly resolved. Decomposition SHALL NOT be a prerequisite for resource storage or resource-session preparation.

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
