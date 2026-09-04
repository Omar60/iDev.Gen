# room-composition-gate Specification

## Purpose

The two refusals a long imported room needs before it reaches a prompt: a room
that puts other people in the frame must not be used for a photograph of one
person, and a room long enough to spend the line's camera must be caught at
compose time rather than in the rendered frame. Both resolve against the room
the session recorded, never against the text of a look somebody may have
edited.

## ADDED Requirements

### Requirement: A gate reads the room the session recorded, not the look's text

The system SHALL record on a session the key of the room its look was filled
from, and SHALL resolve a gate against that key.

A gate SHALL NOT identify the room by reading the composed look back and
matching it against stored room text. The look is editable by hand, and an
edited look would fail that match while still carrying the words the gate
exists to refuse.

A session whose look was written by hand SHALL carry no room key, and SHALL NOT
be gated. Sessions that exist before this change SHALL carry no room key and
SHALL compose exactly as they do today.

Picking a room SHALL record its key. Replacing the room SHALL replace the key,
and clearing the look SHALL clear it.

The system SHALL let the operator detach a look from its room: the key is
cleared and the text is left exactly as it stands. Without that, an operator who
edits the other people out of a room's text by hand is refused by a gate reading
a room whose words are no longer in the line, and the only way out is to delete
a look they have just finished writing. Detaching SHALL NOT alter one byte of
the look, and a detached look SHALL be treated as hand-written from then on.

#### Scenario: An edited look
- **WHEN** the operator edits a look filled from a room marked multi-body, and composes it into a run with no second body
- **THEN** the compose is refused, because the gate reads the recorded room and not the edited text

#### Scenario: A look written by hand
- **WHEN** a session's look was typed rather than filled from a room
- **THEN** no gate applies and the compose proceeds

#### Scenario: Editing the other bodies out
- **WHEN** the operator removes the words that named other people and detaches the look from its room
- **THEN** the key is cleared, the text is unchanged, and the compose proceeds

#### Scenario: A session from before this change
- **WHEN** a session created before rooms recorded their key is composed
- **THEN** it composes as it did before, with no refusal

### Requirement: A room that names other bodies is gated

The system SHALL record, for every room, whether its text places people other
than the subject in the frame, and SHALL refuse to compose that room into a
photograph unless the run has declared that a second body is present.

The refusal SHALL name the room and the words that placed the other bodies, so
that the user can pick another room or turn the second body on deliberately.

A room marked this way SHALL still be selectable for a run that carries the
second-body declaration, and SHALL be composed unchanged when it is.

Where a room is imported, this marking SHALL be derived from the room's own text
and stored, not recomputed at compose time from a second reading of the prose.

#### Scenario: A crowd room in a single-subject run
- **WHEN** a room whose text names other people is composed into a run with no second body declared
- **THEN** the compose is refused and the refusal names the room and the words responsible

#### Scenario: The same room with a second body declared
- **WHEN** the run declares a second body is present
- **THEN** the room composes unchanged

#### Scenario: Marking on import
- **WHEN** a room arrives from an external library with other people in its text
- **THEN** the marking is stored with the room

### Requirement: A room over budget is refused, not truncated silently

The system SHALL enforce a word budget on the room text a composed line carries,
and SHALL refuse a compose whose room would exceed it rather than shortening the
room without saying so.

The refusal SHALL state the room's word count and the budget, so the user can
choose a shorter room or raise the budget deliberately.

The budget SHALL be a configured value with a documented default, and changing
it SHALL NOT alter any stored room text.

A room SHALL NOT be edited, trimmed or summarised to fit. The stored text stays
as imported.

#### Scenario: A room longer than the budget
- **WHEN** a room whose text exceeds the configured budget is composed
- **THEN** the compose is refused and the message gives the room's word count and the budget

#### Scenario: Raising the budget
- **WHEN** the budget is raised above a room's word count
- **THEN** that room composes, and its stored text is unchanged

#### Scenario: No silent shortening
- **WHEN** any room is composed successfully
- **THEN** the room text in the composed line is byte-identical to the stored room text

### Requirement: Gates report before a run, not during it

The system SHALL let the user learn which rooms a planned run would refuse
before any photograph is queued.

A run whose room fails either gate SHALL be refused before anything is sent to
the renderer, and SHALL leave no partially queued shots behind.

#### Scenario: A run planned against a refused room
- **WHEN** a run is started whose room fails a gate
- **THEN** nothing is queued and the refusal is reported first

#### Scenario: Checking ahead
- **WHEN** the user asks which rooms a planned run would refuse
- **THEN** the refused rooms and the reason for each are listed without queueing anything
