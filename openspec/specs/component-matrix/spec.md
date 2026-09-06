# component-matrix Specification

## Purpose

What each component wording is actually worth, recorded against the conditions
it was measured under, and the judging screen that fills it — so that "this
works" is a number a program can read rather than a sentence in a comment.

## Requirements

### Requirement: A cell is identified by the trio, manner and checkpoint

The unit of evidence SHALL be a cell identified by all five of: the wording of
the camera, the wording of the act, the wording of the framing, the manner the
photograph was shot in, and the checkpoint it was painted by. A verdict
recorded against fewer than five SHALL NOT be accepted.

A photograph is the trio — camera × act × framing — and a single component
alone misses the combination. Each of the five has already changed an outcome
on its own: the same camera wording is verified under one manner and dead
under another; two checkpoints disagree on the same clause; and a close
framing on the face renders a different act on all nine checkpoints.

A cell whose measurement did not break out a slot carries the literal wording
`none` in that slot: the 9 per-family observations in kinds.js:1962-1986 were
shot on a line that did not name the framing (scripts/shoot_arrangements.py:63-77),
and the act-only and camera-only measurements did not name the other two slots.
The literal `none` is a fact of the measurement, not an invention, and passes
the non-empty CHECK the cell table enforces.

A cell SHALL hold the number of photographs judged and the number that arrived.

#### Scenario: The same trio under two manners
- **WHEN** a trio is measured under one manner and then under another
- **THEN** the two results are separate cells and neither overwrites the other

#### Scenario: A verdict missing a dimension
- **WHEN** a result is recorded without naming the checkpoint it was painted by
- **THEN** it is rejected rather than stored against an unspecified checkpoint

### Requirement: A cell is verified, dead or unknown, and unknown is the default

A cell SHALL hold exactly one of three states.

`unknown` SHALL be the state of a cell never measured, and SHALL be the initial
state of every cell. `unknown` is not `dead`: never measured and measured-and-
failed are different facts, and the system SHALL NOT collapse them.

`verified` SHALL require at least 10 photographs judged, and at least 8 arrived
for every 10 judged. Below that ratio, at 10 or more judged, the cell SHALL be
`dead`. A cell with fewer than 10 photographs judged SHALL remain `unknown`
whatever its ratio.

#### Scenario: A cell passes
- **WHEN** a cell has 10 photographs judged and 8 arrived
- **THEN** the cell is verified

#### Scenario: A cell fails
- **WHEN** a cell has 10 photographs judged and 7 arrived
- **THEN** the cell is dead

#### Scenario: A cell measured too lightly
- **WHEN** a cell has 3 photographs judged and none arrived
- **THEN** the cell is unknown, not dead

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

Each choice SHALL be offered as the component's judge label — the description
written for someone looking at a photograph. The prompt wording SHALL NOT appear
on the judging screen. A choice list quoting the sentence the photograph was
composed from hands the operator the answer as surely as naming the component
does, and it does so for every choice at once.

The answer chosen SHALL be recorded, not merely whether it matched. What a
failed photograph came back as is the finding — components that fail by
collapsing into the same wrong photograph are indistinguishable under a
yes-or-no question.

A judging pass SHALL ask one question across a batch of photographs rather than
several questions per photograph.

#### Scenario: The photograph is shown bare
- **WHEN** a photograph is presented for judging
- **THEN** neither its brief, its composed line, its wording nor any reference image is on screen

#### Scenario: The choices are judge labels
- **WHEN** a slot's choice list is presented
- **THEN** every choice reads as a description of a photograph, and no choice is the text of any component's prompt wording

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


### Requirement: The composer is reachable from the app

The application SHALL offer composing a run of photographs from the catalogue on
an existing session, in either mode, without a script.

A capability that only a script can reach is a capability the operator does not
have. Every endpoint of the composer shipped and was tested from the outside,
and no screen ever called one: of 292 sessions in the working database exactly
one holds composed photographs, and that one was made by a throwaway script
written to measure something else. The judging screen therefore had nothing to
show, which is how the gap was found.

The refusal SHALL be shown to the operator as the composer worded it, naming the
slot, its verified count and the largest fillable count. Strict mode refusing
rather than repeating a component is the behaviour the mode exists for, and a
screen that softens it into a generic failure hides the number the operator
needs in order to choose between lowering the count and switching mode.

#### Scenario: A run is composed from a screen
- **WHEN** the operator asks for a run of N photographs on a session that carries a manner and a checkpoint
- **THEN** the photographs are queued on that session with their components recorded, and are offered to the judging screen

#### Scenario: A strict run that cannot be filled is refused in the operator's view
- **WHEN** a strict run is refused because the verified pool is too small
- **THEN** the slot, its verified count and the largest fillable count are shown as the composer worded them, and nothing is queued

#### Scenario: N photographs of one trio fill a cell from the screen
- **WHEN** the operator picks one trio and a count on an existing session
- **THEN** that many photographs of the same trio are queued on the session with the trio's components recorded on every row, and the cell check (verified in strict, unknown refused in strict but drawable in exploratory, dead refused in both) runs ONCE before any insert — N rows or zero rows, never some
### Requirement: A photograph that contradicts itself is its own answer

Beside the slot's choices and "none or cannot tell", the judging screen SHALL
offer the operator a way to record that the photograph is internally
contradictory: parts of the subject facing one way and other parts facing
another, in a combination no photograph of a real body could show.

The contradiction SHALL be recorded on the photograph and counted on its cell,
separately from a miss. It SHALL be a miss as well — the drawn component did not
arrive — but the cell SHALL be able to report how many of its misses were
contradictions.

A cell that fails by contradiction and a cell that fails by rendering some other
component are two different findings with two different repairs, and a count
that merges them tells the operator to keep re-measuring the same defect.

#### Scenario: The body and the camera disagree
- **WHEN** a photograph shows the subject's lower body turned away from the camera and her torso and face turned into it
- **THEN** the operator records a contradiction, the cell counts a judgement and no arrival, and the contradiction is counted

#### Scenario: A contradiction is not a "cannot tell"
- **WHEN** a cell's counts are read back
- **THEN** the number of contradictions is available separately from the number of misses recorded as "none or cannot tell"

### Requirement: The human judge and the vision judge ask one question

Where a slot is judged both by an operator on screen and by a vision model in a
script, both SHALL take their choices and their wording from the same catalogue.

Two hand-kept lists of the same question drift, and when they drift the two
judges' numbers stop being comparable while still looking like one measurement.

#### Scenario: A component is added and both judges see it
- **WHEN** a component is added to a slot's catalogue
- **THEN** the next human judging pass and the next vision judging run both offer it, with the same judge label
