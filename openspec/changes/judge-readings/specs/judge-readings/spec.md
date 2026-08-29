## Purpose

The vocabulary a blind judging pass offers: the readings a photograph can be
given for one slot, right answers and wrong ones alike, so that a photograph
which did not deliver what the line asked for records **what it delivered
instead** rather than only that the ask failed.

## ADDED Requirements

### Requirement: A reading is what a judge can see in one slot

The system SHALL hold, per slot and manner, a set of readings. A reading has a
key and a label. The key SHALL be the name of a component family, so that the
reading a judge picks can be compared against the family the line asked for. The
label SHALL be what someone looking at the photograph would say they see.

A reading SHALL exist independently of whether any component asks for it: the
outcomes a checkpoint produces unasked are readings too.

#### Scenario: A reading names an outcome nothing asks for

- **WHEN** the operator adds a reading whose key matches no component in the
  catalogue
- **THEN** it is accepted, and it is offered to the judge like any other reading

#### Scenario: A reading carries a label written for a viewer

- **WHEN** a reading is created with an empty label
- **THEN** the system refuses it, naming the field

### Requirement: Base readings and session readings cannot contradict each other

Readings SHALL come from two scopes: base readings, which apply to every session
of that slot and manner, and session readings, which apply to one session. The
set offered for a pass SHALL be the union of the two.

The system SHALL refuse a reading whose key already exists in the OTHER scope for
the same slot and manner: a session reading colliding with a base reading, and a
base reading colliding with a session reading of any session. There SHALL be no
precedence rule between the scopes, because the refusal makes a collision
impossible.

The check SHALL run in both directions. Checking only the session side leaves the
collision reachable by adding the base reading second, and the union then offers
the same key twice.

#### Scenario: A session reading collides with a base reading

- **WHEN** the operator adds a session reading whose key already exists in the
  base set for that slot and manner
- **THEN** the system refuses it with an error naming the key and the scope that
  already holds it, and stores nothing

#### Scenario: A base reading collides with a session reading added earlier

- **WHEN** the operator adds a base reading whose key is already used by a
  session reading for the same slot and manner
- **THEN** the system refuses it with an error naming the key and the session
  that already holds it, and stores nothing

#### Scenario: Two sessions add the same key

- **WHEN** two different sessions each add a reading with the same key, and no
  base reading holds that key
- **THEN** both are accepted, and each session's pass offers only its own

### Requirement: A pass refuses when a correct answer would be missing

A judging pass SHALL offer the reading union for its slot, plus an explicit
"cannot tell" answer.

Before serving a deck, the system SHALL check that every component family present
among the deck's photographs has a reading. When one does not, the system SHALL
refuse the pass and name the families that have none, because a judge cannot pick
an answer that is not on the list and every such photograph would be recorded as
a miss.

#### Scenario: Every family in the deck has a reading

- **WHEN** a pass is requested for a slot whose photographed families all have
  readings
- **THEN** the deck is served together with the reading union for that slot

#### Scenario: A photographed family has no reading

- **WHEN** a pass is requested and one of the families photographed in the deck
  has no reading in either scope
- **THEN** the system refuses the pass, names the family or families with no
  reading, and serves no deck

#### Scenario: The judge cannot tell

- **WHEN** the judge answers "cannot tell"
- **THEN** the photograph counts as judged and as not arrived, and the answer is
  recorded

### Requirement: A recorded answer says what was seen

The system SHALL record on the photograph which reading the judge picked, for
each slot answered.

A photograph SHALL count as arrived for a slot when the reading picked is the
family the line asked for on that slot. A reading that is not that family SHALL
count as judged and not arrived, and SHALL remain readable afterwards as what
was seen instead.

#### Scenario: The line asked for a side view and the judge saw the front

- **WHEN** the line asked for a component of family `side` and the judge picks
  the reading `front`
- **THEN** the photograph counts as judged and not arrived, and `front` is
  recorded on the photograph as what was seen

#### Scenario: The reading picked is the family asked for

- **WHEN** the line asked for a component of family `side` and the judge picks
  the reading `side`
- **THEN** the photograph counts as judged and arrived

#### Scenario: A slot the line asked nothing of

- **WHEN** a photograph's line asked for no component in the slot being judged
- **THEN** answering that slot counts toward no cell, whatever reading is picked

### Requirement: Agreement is measured across vocabularies

A control photograph is one already answered, re-presented so the judge can be
measured against themselves. The system SHALL decide agreement on whether the
stored answer and the new answer name the same component family, not on whether
the two strings are equal, so that an answer recorded before readings existed
agrees with the reading of the same family.

#### Scenario: A control answered before readings existed

- **WHEN** a control's stored answer is a component key and the judge answers
  with the reading whose key is that component's family
- **THEN** the two agree, and neither the stored verdict nor any cell count
  changes

#### Scenario: A control answered with a different reading

- **WHEN** a control's stored answer and the new answer name different families
- **THEN** they disagree, and neither the stored verdict nor any cell count
  changes

### Requirement: Readings are managed from the app

The operator SHALL be able to list, add and remove readings for a slot and
manner, and to add readings scoped to one session, without editing the database
by hand.

Removing a reading SHALL be refused while a stored answer references it, so that
a recorded measurement never points at a reading that no longer exists.

#### Scenario: Removing a reading that has been answered

- **WHEN** the operator removes a reading that a stored verdict references
- **THEN** the system refuses and names how many answers reference it
