# room-library Specification

## Purpose

Rooms as a registered library rather than a hand-kept list: a declared set of
seed files holding the starting text for a session's look, each room carrying
the furniture it offers and its own verdict, so a shoot can be given a room that
has been measured instead of one somebody typed.

## ADDED Requirements

### Requirement: Rooms live in registered seed files

The system SHALL read its rooms from seed files declared in a registry rather
than from filenames written into code.

Each registry entry SHALL name the library, the seed file it lives in, whether
it is enabled, and a weight used when a room is drawn at random. A library that
is disabled SHALL contribute no rooms to any draw or picker, and SHALL remain
readable so that its rooms can be enabled without re-importing.

Adding a room library SHALL require only a registry entry and its seed file. It
SHALL NOT require a code change.

The registry and the seed files on disk SHALL agree: a registry entry naming a
missing file, or a seed file no entry names, SHALL be reported as an error by
the test suite.

#### Scenario: Adding a library
- **WHEN** a new seed file is placed on disk and declared in the registry
- **THEN** its rooms are available to the picker with no code change

#### Scenario: A disabled library
- **WHEN** a registered library is marked disabled
- **THEN** none of its rooms appear in a draw or in the picker, and re-enabling it makes them available again without a re-import

#### Scenario: Registry and disk disagree
- **WHEN** the registry names a seed file that does not exist
- **THEN** the test suite fails and names the entry

### Requirement: A room is the place, and the manner supplies the register

The system SHALL store a room as the description of the place alone, and SHALL
compose the manner's photographic register with it when the room is used.

A room SHALL NOT carry the register of any one manner in its stored text. A
stored room that did carry one would have to exist once per manner, which
multiplies the library by the manners and leaves the same place stored several
times over.

The rooms that already exist and fuse register with place SHALL be split so that
every room in the library has the same shape, and the split SHALL preserve what
each of them already renders.

Composing a room for a session SHALL produce the register followed by the place,
and the place text SHALL appear in the composition byte-identical to what is
stored.

#### Scenario: One room under two manners
- **WHEN** the same room is used by a session of one manner and a session of another
- **THEN** one stored room serves both, and each composition carries its own manner's register

#### Scenario: An imported room
- **WHEN** a room arrives from an external library
- **THEN** it is stored as the place alone, with no register in its text

#### Scenario: The rooms that already exist
- **WHEN** the existing rooms are split into register and place
- **THEN** composing each of them yields the text it yielded before the split

### Requirement: A room declares which manners it is allowed in

The system SHALL store on every room the set of manners the room may be offered
under, and SHALL default that set to all of them.

Before the register was separated from the place, a room's manner said which
register was fused into its text. That is not a property of a place, and the
field SHALL NOT keep that meaning after the split. What it SHALL mean instead is
whether the place makes sense at all under a manner - a room that is itself a
photographic set-up makes no sense under a manner where nobody is photographing
her.

An imported room SHALL be allowed in every manner, because the source says
nothing about manners and the honest default is not to invent a restriction. A
restriction SHALL be written only where somebody has a reason for it, and the
reason SHALL be recorded with it.

The picker SHALL offer a room only under a manner the room allows.

#### Scenario: An imported room
- **WHEN** a room arrives from an external library
- **THEN** it is allowed in every manner, and the picker offers it under each of them

#### Scenario: A room that is a photographic set-up
- **WHEN** a room whose place is a studio with lighting equipment is stored
- **THEN** it is allowed only under the manners where someone is photographing her, and the reason is recorded

#### Scenario: The picker under one manner
- **WHEN** the picker is opened for a session of a given manner
- **THEN** it lists only the rooms that allow that manner

### Requirement: A verdict names the manner it was measured in

The system SHALL record, with a room's verdict and sample size, the manner the
measurement was taken under.

Being allowed in a manner is not being measured in it. A room verified under one
manner SHALL NOT be presented as verified under another, and the picker SHALL
make the difference visible rather than showing one verdict as if it covered
every manner the room is allowed in.

A room measured under more than one manner SHALL keep a verdict per manner
rather than one verdict overwritten by the latest run.

#### Scenario: A room verified under one manner, offered under another
- **WHEN** a room measured under one manner is listed for a session of a different manner
- **THEN** it is not shown as verified for that session

#### Scenario: The same room measured twice
- **WHEN** a room is measured under a second manner
- **THEN** it carries a verdict for each, and neither overwrites the other

#### Scenario: Re-importing a room measured under two manners
- **WHEN** an import updates a room that carries two verdicts
- **THEN** both survive

### Requirement: A room's key is stable across re-imports and re-translations

The system SHALL derive an imported room's key from an identifier the source
itself carries and that does not change when its text or its translation
changes.

The key SHALL NOT be derived from a label, a translation or any prose. A
translation corrected in the map is expected and encouraged by this change, and
a key derived from a label would change silently on the next import - orphaning
the verdict measured against that room and showing it as unverified with no
record of why.

The key SHALL be plain ASCII, and the derivation SHALL be deterministic: two
imports of the same source entry produce the same key.

#### Scenario: Correcting a translation
- **WHEN** a room's translated label is corrected and the source is imported again
- **THEN** the room keeps its key and its verdict

#### Scenario: The source rewords an entry
- **WHEN** an upstream entry's text changes but its identifier does not
- **THEN** the room keeps its key and its verdict, and its text updates

#### Scenario: Two imports of one entry
- **WHEN** the same source entry is imported twice
- **THEN** both produce the same key

### Requirement: A room offers only what its prose names, and may offer nothing

The system SHALL allow an imported room to offer no furniture at all.

A room offers a piece only when its own prose names it. Where no piece of the
source's prop list survives that test, the room offers nothing, and that is the
correct answer rather than a degraded one: a room that offers nothing licenses
no act that names furniture, which is exactly what its prose supports.

The check that a room's prose names what it offers SHALL therefore not require a
room to offer something. The rooms this project wrote itself SHALL keep the
requirement to offer at least one piece, because each of them was written to.

#### Scenario: An imported room whose prose names no furniture
- **WHEN** no piece of a source entry's prop list appears in its prose
- **THEN** the room is stored offering nothing and the import is not blocked

#### Scenario: An act naming furniture in such a room
- **WHEN** a run uses a room that offers nothing
- **THEN** an act that names a piece of furniture is not licensed by that room

#### Scenario: The project's own rooms
- **WHEN** a room this project wrote offers nothing
- **THEN** the test suite fails and names it

### Requirement: The register meets the place when a room is picked

The system SHALL compose the manner's register with the room's place at the
moment a room is picked, and SHALL write the composed text into the session's
look.

The look therefore stays what it is today: one constant text, prepended to every
photograph, and editable by hand afterwards. Nothing about how a photograph is
composed changes, and a look written from scratch by hand SHALL NOT have a
register forced onto it.

Changing a session's manner after a room has been picked SHALL NOT silently
rewrite the look. The system SHALL say that the look carries another manner's
register and offer to fill it again, because the text may have been edited by
hand since and overwriting it would discard that.

#### Scenario: Picking a room
- **WHEN** a room is picked for a session of a given manner
- **THEN** the look is filled with that manner's register followed by the room's place

#### Scenario: A look written by hand
- **WHEN** the operator writes a look without picking a room
- **THEN** no register is added to it

#### Scenario: Changing the manner afterwards
- **WHEN** a session's manner changes after its look was filled from a room
- **THEN** the look is not rewritten, and the operator is told it carries another manner's register and offered a refill

### Requirement: A room carries the furniture it offers

The system SHALL store, for every room, the set of furniture pieces the room
makes available, and the room's prose SHALL name every piece it offers.

A room whose `offers` set names a piece its prose does not mention SHALL be
rejected by the test suite, because the picker would then promise a piece no
photograph can contain.

Where a room is imported from a source that has no such field, the importer
SHALL derive it from the room's own prose and from the source's prop list,
keeping only pieces the prose actually names. A piece that appears in the
source's prop list but not in the prose SHALL NOT be offered.

Comparison SHALL ignore spaces and hyphens, so that the field can be a key
(`sink-edge`) while the prose stays prose.

#### Scenario: An imported room with props the prose does not name
- **WHEN** a source entry lists a prop its theme string never mentions
- **THEN** that prop is not offered by the imported room

#### Scenario: A room offering a piece it does not describe
- **WHEN** a room declares it offers a bench and its prose does not mention one
- **THEN** the test suite fails and names the room

#### Scenario: A key against prose
- **WHEN** a room offers `sink-edge` and its prose says "the edge of the sink"
- **THEN** the check passes

### Requirement: Imported room text is read at runtime, never bundled

The system SHALL read imported rooms at runtime rather than at build time, so
that a checkout without them builds and runs with the rooms it does carry.

The rooms this project wrote itself SHALL remain available without any import
having been run.

An absent or empty imported library SHALL produce an empty list and a stated
reason, never an error and never a failed build.

#### Scenario: A checkout with no imported rooms
- **WHEN** the app is built and started on a checkout where no import has been run
- **THEN** the build succeeds and the picker offers the rooms the project wrote itself

#### Scenario: An imported library present
- **WHEN** imported rooms are present
- **THEN** the picker offers them alongside the project's own

#### Scenario: An imported library that cannot be read
- **WHEN** an imported library is absent or unreadable
- **THEN** the picker shows the rooms it does have and states why the rest are missing

### Requirement: A verdict is stored apart from the text it was measured against

The system SHALL store a room's verdict, its sample size and the manner it was
measured under separately from the room's own text, keyed by the room's key.

A verdict SHALL be retained by this project regardless of whether the text it
was measured against is retained. A measurement is this project's own work and
SHALL NOT be lost with material that is only stored here.

A verdict whose room is not present SHALL be reported as orphaned and SHALL NOT
be deleted, so that a room which disappears upstream does not take its
measurement with it.

Nothing in the verdict store SHALL reproduce the source's prose.

#### Scenario: A room measured and then removed
- **WHEN** the room a verdict was measured against is no longer present
- **THEN** the verdict is reported as orphaned and is kept

#### Scenario: A verdict and its room reunited
- **WHEN** a room is imported again and a verdict exists for its key
- **THEN** the two are shown together

#### Scenario: No source prose in the verdict store
- **WHEN** the verdict store is read
- **THEN** it carries keys, verdicts, sample sizes and manners, and no room text

### Requirement: A room carries a verdict

The system SHALL store a verdict and a sample size on every room, using the same
vocabulary the component catalogue already uses, and SHALL default an imported
room to unverified.

The picker SHALL show a room's verdict, and SHALL make an unverified room
visibly distinct from a verified one, so that a room nobody has shot is not
mistaken for one that has been measured.

Importing a room that already exists SHALL update its text and its offers and
SHALL NOT reset a verdict already recorded against it.

A room carrying a verdict written as free text SHALL be converted to that
vocabulary rather than left as prose, because a picker cannot tell verified from
unverified by reading a sentence. A conversion SHALL NOT raise a room above what
its sample size supports: a room rendered once is unverified at a sample size of
one. The original sentence SHALL be kept as a note beside the converted verdict,
because it records which run and which question, and the vocabulary records
neither.

#### Scenario: A freshly imported room
- **WHEN** a room arrives from an external library
- **THEN** it is stored unverified with no sample size, and the picker shows it as unverified

#### Scenario: Re-importing over a measured room
- **WHEN** an import runs over a room that already carries a verdict
- **THEN** its text and offers are updated and its verdict and sample size are preserved

#### Scenario: Reading the picker
- **WHEN** the picker lists a verified and an unverified room
- **THEN** the two are visibly distinct

#### Scenario: A room whose verdict is a sentence
- **WHEN** a room carries a free-text verdict recording a single rendered photograph
- **THEN** it is stored unverified at a sample size of one, and the sentence is kept as a note

### Requirement: A room carries its translated fields beside its source text

The system SHALL store, on a room whose source carried non-English fields, the
English translations of those fields alongside the room's source text, and SHALL
keep the two distinguishable.

Where the source carries authoring guidance — the reason an entry is shaped the
way it is, or a constraint on what would break it — that guidance SHALL be
stored with the room and SHALL be readable by whoever picks or edits it. It
SHALL NOT be composed into a prompt.

The check that a room's prose names the furniture it offers SHALL run against
the room's English source prose only, never against a translation, so that a
reworded translation can never change whether a room passes.

#### Scenario: A room imported from a non-English entry
- **WHEN** a room arrives whose source label and notes were not in English
- **THEN** the stored room carries English translations of both, marked as authored, beside its untouched source prose

#### Scenario: Authoring guidance is not composed
- **WHEN** a room carrying authoring guidance is composed into a photograph
- **THEN** the guidance does not appear in the composed line

#### Scenario: The offers check under translation
- **WHEN** a room's translated label is edited
- **THEN** whether the room passes the offers check is unchanged

### Requirement: A room carries the source's tags and its authoring notes

The system SHALL store the tags a source entry carries, and the picker's tag
filter SHALL read those.

The system SHALL store the source's mood words with the room's authoring
guidance rather than as a second filter. Three quarters of that vocabulary is
used by exactly one entry, so offering it as a filter would be a list of filters
that each return one room.

Neither tags nor mood words SHALL be composed into a prompt.

#### Scenario: Filtering by a source tag
- **WHEN** the picker filters by a tag
- **THEN** it lists the rooms whose source entry carried that tag

#### Scenario: Mood words
- **WHEN** a room whose source carried mood words is opened in the picker
- **THEN** the mood words are readable with its other authoring guidance and are not offered as a filter

#### Scenario: Neither reaches a line
- **WHEN** a room is composed into a photograph
- **THEN** neither its tags nor its mood words appear in the composed line

### Requirement: A room can be drawn at random, weighted, once per session

The system SHALL let the operator either choose a room or have one drawn for
them when a session is created.

A drawn room SHALL be picked from the rooms allowed under that session's manner,
weighted by the weight its source entry carried, and SHALL fill the session's
look exactly as a chosen room does. The draw SHALL happen once: the drawn room
becomes the session's default and SHALL NOT be re-drawn afterwards, and the
operator SHALL be able to replace it by hand at any time.

The verdict of a drawn room SHALL be shown with it, so that drawing an
unverified room is visible rather than silent.

A weight outside the range the rest of the library uses SHALL be reported by the
import naming the entries, because a handful of entries at twenty times the
common weight take a share of every draw that nobody chose deliberately.

#### Scenario: Drawing a room
- **WHEN** the operator asks for a room to be drawn for a new session
- **THEN** one is picked from the rooms that manner allows, weighted by its source weight, and it fills the look

#### Scenario: The draw is not repeated
- **WHEN** a session with a drawn room is reopened or edited
- **THEN** the same room stays and nothing is re-drawn

#### Scenario: Replacing a drawn room
- **WHEN** the operator picks a different room, or edits the look text
- **THEN** their choice stands and is not overwritten

#### Scenario: An unverified room is drawn
- **WHEN** the draw returns a room with no verdict under that manner
- **THEN** that is shown with the result

#### Scenario: An outlying weight
- **WHEN** an imported entry carries a weight far outside the range the library otherwise uses
- **THEN** the import reports it and names the entry

### Requirement: The picker stays usable at scale

The system SHALL let the user narrow the room list by text and by the tags a
room carries, and SHALL keep the room a session already uses reachable
regardless of the current filter.

A text filter SHALL match against both the room's translated label and its
English source prose, so that a room can be found by its name or by something
inside it.

Selecting a room SHALL fill the session's look with that room's text, and SHALL
leave the session's wardrobe, shots and settings untouched.

#### Scenario: Filtering hundreds of rooms
- **WHEN** the library holds several hundred rooms
- **THEN** the user can narrow the list by typing part of a room's text or by selecting a tag

#### Scenario: The session's current room under a filter
- **WHEN** a filter excludes the room the open session uses
- **THEN** that room is still shown as the current selection

#### Scenario: Picking a room
- **WHEN** a room is selected for a session
- **THEN** the session's look is filled from it and its wardrobe, shots and settings are unchanged
