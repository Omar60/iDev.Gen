# wardrobe Specification

## Purpose

What a photograph is wearing, and what that entitles it to. The wardrobe holds
the garments and the outfits made of them, derives the undressing an outfit
implies rather than storing it, and answers per photograph whether the stage it
was dealt gives an act the access it needs.

## Requirements

### Requirement: A garment is one piece of clothing, an outfit is an order

The system SHALL store a garment as a single piece of clothing written the way
it goes into a composed line, and an outfit as an ordered list of garment keys.

The order of an outfit SHALL be the order the garments COME OFF. It is authored
and SHALL NOT be dealt or guessed: a shoot that takes the top off before the
underwear is a different shoot from one that does not, and neither is the
composer's to choose.

A garment MAY carry a second wording for the same garment moved out of the way
rather than taken off. A garment that cannot be moved SHALL carry none, and
that absence is a statement about the garment rather than missing data.

Neither a garment nor an outfit SHALL be a dimension of a measured cell. The
wardrobe multiplies the component matrix by itself and leaves every measurement
in it unreachable, so what she is wearing is a property of the photograph and
never of the trio.

#### Scenario: An outfit is read back in the order it was authored
- **WHEN** an outfit naming three garments is read
- **THEN** its garments come back in the order they were written, and that order is what the undressing follows

#### Scenario: A garment that cannot be moved aside
- **WHEN** a garment carries no aside wording
- **THEN** the arc offers no stage of it moved aside, and it is either worn or off

### Requirement: The undressing is derived, never stored

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

### Requirement: A garment moved aside is a stage of its own

The system SHALL offer, for a garment that carries an aside wording, one extra
stage in which she is still wearing that garment with it moved out of the way,
and that stage SHALL be reported as giving access.

Without it an arc steps from a covering garment straight to nothing at all, and
every photograph of an act that needs access is then a photograph of an
undressed woman — which is a different shoot from the one the outfit describes.

The extra stage SHALL be offered where the garment is the last one left, so one
outfit gains at most one such stage.

#### Scenario: The last garment can be moved
- **WHEN** the final garment of an outfit carries an aside wording
- **THEN** the arc holds a stage of her still wearing it, moved aside, and that stage gives access

#### Scenario: The last garment cannot be moved
- **WHEN** the final garment carries no aside wording
- **THEN** the arc steps from that garment to the bare state with no stage in between

### Requirement: How far a garment reaches is answered once

The system SHALL answer how far down the body a garment reaches, and whether it
covers her below the waist, from the garment's own wording using the SAME
calculation the crop law runs over a composed line.

That answer SHALL NOT be stored in the store and SHALL NOT be recomputed by a
second reader. Two calculations of "how low does this go" disagree, and the
photograph the disagreement refuses is a legal one.

The answer SHALL be served with the garment, so that a caller reads a decided
value rather than comparing body parts it would have to know the order of.

#### Scenario: A garment that covers her below the waist
- **WHEN** the catalogue is read
- **THEN** each garment carries the part of her it reaches and whether it covers her below the waist

#### Scenario: The crop law and the wardrobe agree
- **WHEN** a garment's wording names a part of her body
- **THEN** the part reported for that garment is the one the crop law reads out of a line naming it

### Requirement: Access is answered per photograph, not per run

The system SHALL treat "can a hand or a toy reach her" as a property of the
PHOTOGRAPH. A stage gives access when nothing left on her covers her below the
waist, or when it is the stage of a garment moved aside.

An act that requires access SHALL be drawable only into a photograph whose
dealt stage gives it. It SHALL NOT be excluded from the run because some
photographs do not, and SHALL NOT be dealt to a photograph in a covering
garment because some others do — a run-level answer is wrong in both
directions and was, in both.

Where the caller has no answer for a photograph — a hand-typed arc is prose and
has no garments to read — the run's fallback answer SHALL decide it, so that
every session written before the catalogue existed behaves exactly as it did.

The run's other two conditions — whether a second person is in the room, and
whether the room has furniture — SHALL stay properties of the RUN and narrow
the pool once, before it is built, so that every count the run reports is a
count of acts it could actually draw.

#### Scenario: An act that needs access, in a covering garment
- **WHEN** a photograph is dealt a stage whose garments cover her below the waist
- **THEN** an act requiring access is not drawn into that photograph

#### Scenario: The same act, one stage later
- **WHEN** a photograph is dealt a stage that gives access
- **THEN** an act requiring access may be drawn into it, in the same run

#### Scenario: A hand-typed arc
- **WHEN** the states of a run were typed rather than derived from an outfit
- **THEN** each photograph carries no answer of its own and the run's fallback answer decides them all

### Requirement: A session dresses every photograph, a take may answer for itself

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

### Requirement: Every wardrobe the run may write constrains the draw

The system SHALL take the wardrobe into account when deciding which framings
are drawable, because the wardrobe is part of the composed line and so is part
of what names her lowest visible part.

The constraint SHALL be built from every state the run MAY write, not only from
the session's wardrobe: the pool is drawn once for the whole run, so a trio has
to survive every line any of its photographs could be composed with.

Where a run deals a wardrobe to every photograph, the session's own SHALL NOT
constrain the draw — no line of that run writes it, and reading it in refuses
trios nothing in the run could contradict.

#### Scenario: A state that names her feet
- **WHEN** one state of a run's arc names stockings
- **THEN** framings that crop above the feet are out of the draw for that whole run

#### Scenario: A run that dresses every photograph itself
- **WHEN** a run deals a wardrobe to every photograph it queues
- **THEN** the session's own wardrobe constrains nothing in that draw

### Requirement: The catalogue is imported, idempotent on the key, and refuses a hole

The system SHALL let the operator import garments and outfits, from a supplied
payload or from the file the project ships.

An import SHALL skip a key it already holds and SHALL leave its wording alone.
A garment's wording is the text of every state that carries it, so re-importing
must never reword a session already shot.

An outfit naming a garment neither in the store nor in the same payload SHALL
be refused, and refused BEFORE anything is written. A half-imported outfit is
an outfit whose arc has a hole in the middle, and the operator meets it as a
missing stage rather than as an error.

A garment SHALL be refused without a key and a wording; an outfit SHALL be
refused without a key and at least one garment.

The read SHALL serve garments and outfits together, because an outfit is a list
of garment keys and is unreadable without them.

A retired garment or outfit SHALL be left out of what the catalogue offers,
while remaining readable on request, so that a session shot in it can still be
read back.

#### Scenario: Re-importing the shipped seed
- **WHEN** the shipped wardrobe is imported twice
- **THEN** the second import adds nothing, rewords nothing, and reports what it skipped

#### Scenario: An outfit naming an unknown garment
- **WHEN** an import carries an outfit naming a garment that is neither in the store nor in the same payload
- **THEN** the import is refused naming the missing garments, and no garment or outfit from that payload is written

#### Scenario: A retired garment
- **WHEN** a garment is retired
- **THEN** the catalogue no longer offers it, and it can still be read back on request
