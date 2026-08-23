## Purpose

Marking a session with tags, and finding one across every model from its text or
its tags, without knowing which character shot it.

## ADDED Requirements

### Requirement: A session carries tags

The system SHALL store a set of free-text tags on a session, editable at any
time, including after the session has been shot. A tag SHALL be trimmed of
surrounding whitespace and compared case-insensitively, so the same word entered
twice in different case is one tag and not two. An empty tag SHALL be discarded
rather than stored. Tags SHALL survive a session being cloned.

Editing tags SHALL NOT touch the session's shots, its look, its wardrobe or its
settings.

#### Scenario: Tagging a session that has been shot
- **WHEN** the user adds two tags to a session that already has photographs
- **THEN** both are stored, the session's shots are unchanged, and reloading the session shows both tags

#### Scenario: The same tag twice
- **WHEN** a session is given `Balcony` and later `balcony`
- **THEN** the session carries one tag, not two

#### Scenario: An empty tag
- **WHEN** a tag consisting of whitespace only is submitted
- **THEN** it is discarded and the session's remaining tags are unchanged

#### Scenario: A cloned session keeps its tags
- **WHEN** a tagged session is cloned
- **THEN** the copy carries the same tags

### Requirement: Finding a session across models

The system SHALL list sessions across every model, newest first, narrowed by an
optional text query and an optional tag.

The text query SHALL match, case-insensitively and on a substring, the session's
name, its look and its wardrobe. The tag filter SHALL match a whole tag and not
a substring of one. Given both, a session SHALL be listed only when it satisfies
both.

Each listed session SHALL carry the id of its cover photograph, so the list can
show one frame per session without a request per row.

A query matching nothing SHALL return an empty list, not an error.

#### Scenario: Text across models
- **WHEN** two sessions belonging to different models both mention `balcony` in their look, and a third session does not
- **THEN** a text query for `balcony` lists exactly those two, newest first

#### Scenario: The query reads the wardrobe too
- **WHEN** a session mentions `raincoat` only in its wardrobe
- **THEN** a text query for `raincoat` lists that session

#### Scenario: A tag is matched whole
- **WHEN** one session is tagged `night` and another `nightclub`
- **THEN** a tag filter of `night` lists only the first

#### Scenario: Both filters at once
- **WHEN** a text query and a tag are given together
- **THEN** only the sessions satisfying both are listed

#### Scenario: No filters
- **WHEN** neither a text query nor a tag is given
- **THEN** every session is listed, newest first

#### Scenario: Nothing matches
- **WHEN** the query matches no session
- **THEN** the response is an empty list and the request succeeds

#### Scenario: Each row can show a frame
- **WHEN** a listed session has photographs
- **THEN** the row carries the id of the photograph to show for that session

### Requirement: The library screen

The frontend SHALL offer a screen listing sessions across models, with a search
box and the tags currently in use, and each row SHALL link to its session. The
screen SHALL say so plainly when nothing matches rather than showing an empty
area.

#### Scenario: Searching from the library
- **WHEN** the user types into the search box
- **THEN** the list narrows to the matching sessions, and a row opens its session

#### Scenario: Filtering by a tag
- **WHEN** the user selects one of the tags shown
- **THEN** the list narrows to the sessions carrying that tag

#### Scenario: An empty result reads as empty
- **WHEN** the search matches no session
- **THEN** the screen says nothing matched
