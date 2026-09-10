## MODIFIED Requirements

### Requirement: The undressing is derived, never stored

These requirements govern the legacy wardrobe catalogue and composer. Resource-session plans SHALL use explicit scoped wardrobe changes as defined by session-plan, SHALL keep wardrobe constant by default, and SHALL NOT automatically apply the legacy derived progression. Existing legacy outfit derivation and take override behavior SHALL remain unchanged.

The system SHALL derive the wardrobe states of an outfit from its garment
order: N garments SHALL produce N+1 states of undressing, one garment leaving
at each step, and the last state SHALL be bare.

N+1 counts the undressing alone. The stage of a garment moved aside is added on
top of it by the requirement below, so an outfit whose last garment can be moved
derives N+2 states in total — and every outfit the project ships ends in one
that can, so N+1 is the count of an arc nobody has, not the count of a real one.

A stored list of states SHALL NOT exist. It would be a second copy of the same
fact and would disagree with the garments the moment one of them is reworded.

A state SHALL be composed by naming only what she is STILL WEARING. A state
written as what came off SHALL NOT be produced: naming a garment that is no
longer on her puts its word into the line, where the crop law reads it as the
part of her that garment reaches and every framing above that part leaves the
draw for the whole run.

A state in which nothing is left SHALL be a sentence saying so, never an empty
string. A line that says nothing about clothing renders her undressed by
accident rather than by the arc reaching its end — measured 3/3 with no
reference attached.

A garment key the catalogue does not know SHALL be dropped from the arc rather
than written into a line. A key is an identifier, and an identifier in a prompt
is words the sampler will paint.

#### Scenario: Three garments derive four states
- **WHEN** the arc of an outfit of three garments, none of which can be moved aside, is derived
- **THEN** there are four states, each naming only what is still worn, ending with the bare one

#### Scenario: The bare state says it
- **WHEN** the last state of an arc is composed into a line
- **THEN** the line states that nothing is worn rather than omitting clothing altogether

#### Scenario: An outfit naming a garment that is not there
- **WHEN** an outfit names a garment key the catalogue does not hold
- **THEN** that key contributes no state and its identifier never reaches a composed line

### Requirement: A session dresses every photograph, a take may answer for itself

These requirements govern the legacy wardrobe catalogue and composer. Resource-session plans SHALL use explicit scoped wardrobe changes as defined by session-plan, SHALL keep wardrobe constant by default, and SHALL NOT automatically apply the legacy derived progression. Existing legacy outfit derivation and take override behavior SHALL remain unchanged.

The system SHALL let a session carry one wardrobe that every photograph of it
starts from, and SHALL let a single take carry its own, which wins over the
session's.

A take's own wardrobe is what lets one shoot walk from a jacket to nothing with
each frame stating its own truth. Prepending the session's wardrobe to a take
that undresses her produces a photograph wearing neither, so the two SHALL NOT
both reach one line.

A composed run MAY be dealt one wardrobe per photograph, in the order the
photographs are queued. A photograph the run does not deal one to SHALL be
composed in the session's wardrobe, and a photograph dealt an empty one SHALL
be composed with nothing written about clothing — the two are different
photographs and SHALL NOT be conflated.

The system SHALL let a run compose its lines without the session's wardrobe at
all, so that a reference image can deliver the clothing instead.

#### Scenario: A take that names its own clothes
- **WHEN** a take carries a wardrobe and its session carries another
- **THEN** the take's wardrobe is written and the session's is not

#### Scenario: A run shorter than the states dealt to it
- **WHEN** a composed run is dealt fewer wardrobes than it queues photographs
- **THEN** the photographs past the end are composed in the session's wardrobe rather than wrapping round to the first state

#### Scenario: The wardrobe handed to a reference instead
- **WHEN** a run is composed with the session's wardrobe muted
- **THEN** no line of that run carries the session's wardrobe
