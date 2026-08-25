## Purpose

Building the line a photograph is painted from by drawing components out of the
catalogue instead of asking a model to write one — so a shoot can be made from
parts that are known to work, and so the written path finally has something to
be measured against.

## ADDED Requirements

### Requirement: A shot is composed from drawn components with no writer involved

The composer SHALL build a queued line by drawing one component per slot it
fills and joining them, without calling the writer.

The composed line SHALL be joined the same way a written line is, so that a
composed shot and a written shot differ only in where their text came from.

Every composed shot SHALL record the components it was drawn from, so the
photograph it produces can be judged back into the cells that made it.

#### Scenario: A composed shot is queued
- **WHEN** a shot is composed and queued
- **THEN** no writer request is made, and the shot records each component it was composed from

#### Scenario: A composed photograph is judgeable
- **WHEN** a composed photograph is later judged
- **THEN** the result is recorded against the cells of the components that shot was composed from

### Requirement: Strict mode draws only verified cells

In strict mode the composer SHALL draw only components whose cell is verified
for the manner and checkpoint the session will be shot on.

It SHALL NOT draw an unknown cell, and SHALL NOT draw a dead wording.

#### Scenario: An unknown component is not drawn in strict mode
- **WHEN** a strict composition is made and a component is verified for a different checkpoint than the one being shot
- **THEN** that component is not drawn

### Requirement: Strict mode refuses rather than degrades when a slot has no pool

If strict mode cannot fill a slot for the requested number of photographs
without repeating a component, it SHALL refuse the composition and SHALL name
the slot that ran out and how many verified components it had.

The refusal SHALL apply to the whole composition and SHALL NOT deliver a shorter
run than was asked for. The ordering constraints are computed over the whole
run, and a shoot silently delivered short is a surprise in the gallery rather
than a decision the operator made.

The refusal SHALL carry the largest number of photographs strict mode could
fill, so the operator can ask for that number instead of finding it by hand, and
SHALL say that exploratory mode is the way to compose with what is not yet
measured.

It SHALL NOT silently repeat a component, and SHALL NOT fall back to unknown
cells. A shoot whose camera repeats is one photograph taken many times, which is
the defect the catalogue exists to prevent; and falling back to unknown makes
strict mode indistinguishable from exploratory.

#### Scenario: The verified pool is too small
- **WHEN** a strict composition of 40 photographs is requested and the camera slot has 3 verified components
- **THEN** the composition is refused, naming the camera slot and its count of 3, reporting 3 as the largest fillable count, and nothing is queued

#### Scenario: The pool is exactly large enough
- **WHEN** a strict composition is requested and every slot has at least as many verified components as photographs asked for
- **THEN** the composition succeeds and no component is drawn twice

### Requirement: Exploratory mode draws unknown cells and records what came back

In exploratory mode the composer SHALL be allowed to draw components whose cell
is unknown, and SHALL mark each such shot as exploratory.

It SHALL NOT draw a dead wording in any mode.

A judged exploratory photograph SHALL count towards the cell it was drawn from,
so that ordinary use fills the matrix without a dedicated run.

#### Scenario: An exploratory draw is recorded
- **WHEN** an exploratory shot drawn from an unknown cell is judged
- **THEN** that cell's judged count and arrived count increase, and the cell becomes verified or dead once it reaches the threshold

#### Scenario: Dead is never drawn
- **WHEN** an exploratory composition is made
- **THEN** no dead wording is drawn

### Requirement: Two photographs are duplicates when their components match

The composer SHALL refuse to place two photographs in one session carrying the
same combination of drawn components, decided on the components before anything
is queued.

This check SHALL be in addition to the existing rule that no line of a shoot
repeats a line the shoot already has, which SHALL continue to apply — to written
lines unchanged, and to composed lines as well. Neither check subsumes the
other: different component combinations can join into near-identical text, and a
session may carry both composed and written lines, which is the case neither
check would own alone.

#### Scenario: The same combination is drawn twice
- **WHEN** a draw would produce a photograph whose components match one already in the session
- **THEN** that draw is replaced rather than queued

#### Scenario: Two different combinations join into the same line
- **WHEN** two composed photographs are drawn from different components but produce lines the repeat check reads as the same
- **THEN** the second is refused by the line-level check, the same as a written line would be

### Requirement: A single shot and a session are the same composition

The composer SHALL serve both a single independent shot and a whole session.

A session SHALL be the same draw with ordering constraints applied on top —
spreading a slot's components across the photographs and placing components that
belong late — rather than a separate composer.

A single composed shot SHALL be subject to every rule above except those that
only have meaning across several photographs.

#### Scenario: One shot composed alone
- **WHEN** a single shot is composed
- **THEN** it is drawn under the same mode rules, and the pool-exhaustion and duplicate rules that span photographs do not apply

#### Scenario: A session spreads a slot
- **WHEN** a session of several photographs is composed
- **THEN** no two consecutive photographs share a component family in the spread slots

### Requirement: The written path is unchanged and remains available

Composing SHALL be an alternative to the written path, not a replacement.

A session SHALL be composed or written, and the choice SHALL be recorded on the
session so a later comparison can tell which produced which photographs.

#### Scenario: The written path still runs
- **WHEN** a session is created on the written path
- **THEN** it behaves exactly as before this capability existed

#### Scenario: The origin is recorded
- **WHEN** a session is finished
- **THEN** the session records whether its lines were composed or written
