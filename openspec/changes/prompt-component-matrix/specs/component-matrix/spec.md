## Purpose

What each component wording is actually worth, recorded against the conditions
it was measured under, and the judging screen that fills it — so that "this
works" is a number a program can read rather than a sentence in a comment.

## ADDED Requirements

### Requirement: A cell is identified by concept, wording, manner and checkpoint

The unit of evidence SHALL be a cell identified by all four of: the concept, the
wording, the manner the photograph was shot in, and the checkpoint it was
painted by. A verdict recorded against fewer than four SHALL NOT be accepted.

Each of the four has already changed an outcome on its own: the same camera
wording is verified under one manner and dead under another, and two checkpoints
disagree on the same clause.

A cell SHALL hold the number of photographs judged and the number that arrived.

#### Scenario: The same wording under two manners
- **WHEN** a wording is measured under one manner and then under another
- **THEN** the two results are separate cells and neither overwrites the other

#### Scenario: A verdict missing a dimension
- **WHEN** a result is recorded without naming the checkpoint it was painted by
- **THEN** it is rejected rather than stored against an unspecified checkpoint

### Requirement: A cell is verified, dead or unknown, and unknown is the default

A cell SHALL hold exactly one of three states.

`unknown` SHALL be the state of a cell never measured, and SHALL be the initial
state of every cell. `unknown` is not `dead`: never measured and measured-and-
failed are different facts, and the system SHALL NOT collapse them.

`verified` SHALL require at least 10 photographs judged with at least 8 of them
arriving. Below 8 of 10 the cell SHALL be `dead`. A cell with fewer than 10
photographs judged SHALL remain `unknown` whatever its ratio.

#### Scenario: A cell passes
- **WHEN** a cell has 10 photographs judged and 8 arrived
- **THEN** the cell is verified

#### Scenario: A cell fails
- **WHEN** a cell has 10 photographs judged and 7 arrived
- **THEN** the cell is dead

#### Scenario: A cell measured too lightly
- **WHEN** a cell has 3 photographs judged and none arrived
- **THEN** the cell is unknown, not dead

### Requirement: Existing verdicts are seeded with the sample size they were taken at

Verdicts carried over from earlier measurement SHALL be recorded with their real
sample size, manner and checkpoint, and SHALL then be subject to the same
thresholds as any other cell.

A seeded result below 10 photographs SHALL therefore land as `unknown`. Most of
what this project treats as ruled out was measured at three photographs, which
admits a true success rate far too wide to rule anything out.

#### Scenario: A three-photograph verdict is seeded
- **WHEN** a verdict of 0 of 3 is seeded
- **THEN** the cell is unknown and available to be measured again

#### Scenario: A well-measured failure is seeded
- **WHEN** a verdict of 0 of 24 is seeded against the wording and checkpoints it was shot on
- **THEN** the cell is dead for that wording, and other wordings of the same concept remain unknown

### Requirement: A row is opened at its anchor cell and abandoned there on failure

The first cell measured for a new wording SHALL be its anchor cell: one fixed
line with everything but the component under test held constant.

If the anchor cell is dead, the remaining cells of that wording SHALL NOT be
measured. This is the whole saving of measuring one at a time.

A verified anchor cell SHALL NOT mark the wording verified anywhere else. The
rest of the line is held during the anchor measurement, so a pass there means
the row is worth continuing and nothing more.

#### Scenario: The anchor fails
- **WHEN** a wording's anchor cell is measured dead
- **THEN** no further cells are offered for that wording

#### Scenario: The anchor passes
- **WHEN** a wording's anchor cell is measured verified
- **THEN** the wording's other cells remain unknown and are offered for measurement

### Requirement: Judging is blind and answered as a forced choice

The judging screen SHALL present a photograph without its brief, without the
wording it was composed from, and without any reference image.

It SHALL ask which member of the slot's full list is in the photograph, offered
as a choice over that whole list plus an explicit "none or cannot tell" answer.
It SHALL NOT ask whether a named component is present: an operator shown the
expected answer will find it.

The answer chosen SHALL be recorded, not merely whether it matched. What a
failed photograph came back as is the finding — components that fail by
collapsing into the same wrong photograph are indistinguishable under a
yes-or-no question.

A judging pass SHALL ask one question across a batch of photographs rather than
several questions per photograph.

#### Scenario: The photograph is shown bare
- **WHEN** a photograph is presented for judging
- **THEN** neither its brief, its composed line, its wording nor any reference image is on screen

#### Scenario: A wrong answer is kept
- **WHEN** a photograph composed from one camera component is judged as showing a different one
- **THEN** the cell records a miss and records which component was seen

#### Scenario: The judge cannot tell
- **WHEN** the operator cannot identify any member of the list in the photograph
- **THEN** "none or cannot tell" is recorded as the answer and the cell records a miss

### Requirement: The judge is checked against known answers

A judging pass SHALL periodically re-present photographs whose verdict is
already recorded, without marking them as such, and SHALL report the rate at
which the operator's answers agree with the stored ones.

A human judge drifts between sessions, and a judging instrument with no control
on itself is how a measurement returns believable numbers that are wrong.

#### Scenario: A control photograph disagrees
- **WHEN** an operator answers a re-presented photograph differently from its stored verdict
- **THEN** the disagreement is reported at the end of the pass rather than silently overwriting the stored verdict
