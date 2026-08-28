## MODIFIED Requirements

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

## ADDED Requirements

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

## REMOVED Requirements

### Requirement: Existing verdicts are seeded with the sample size they were taken at

**Reason**: The seed was a second, hand-translated copy of measurements whose
first copy lived in comments beside the catalogue constants. With the catalogue
becoming operator-owned data that starts empty, a seed carrying verdicts about
components the installation does not have is evidence with nothing to attach to.
Every seeded verdict below ten photographs already read `unknown`, so most of
the seed asserted nothing.

**Migration**: The measurements remain in the repository as the importable
measured set and in the history of the files they were read from. An operator
who wants them imports the components and re-measures through the judging
screen; a cell with no row reads `unknown`, which is what almost every seeded
row read anyway.
