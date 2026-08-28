## Purpose

The catalogue as something an operator owns: components held in the database,
added and edited from the app rather than from source, starting empty so that
every wording in it is one this installation chose and measured.

## ADDED Requirements

### Requirement: The catalogue is stored data, not source

Components SHALL be held in the application's database and served to every
reader from there. No component's wording SHALL be a constant compiled into the
frontend or the backend.

The catalogue is the thing this project is trying to learn. A catalogue that
lives in source can only be changed by editing source, which puts every finding
behind a build and puts the operator who has just looked at the photograph
outside the loop entirely.

#### Scenario: A component added from the app is drawable
- **WHEN** a component is added through the catalogue screen
- **THEN** it is offered by the composer, the writer's camera plan and the judging screen without the application being rebuilt

#### Scenario: No wording is compiled in
- **WHEN** the frontend and backend sources are searched for the text of any catalogue wording
- **THEN** the text is found in neither, except in a seed file that is never loaded automatically

### Requirement: A fresh installation starts with an empty catalogue

A database created for the first time SHALL contain no components in any slot.

Nothing SHALL be inserted on startup, on migration, or on first use of a screen.

#### Scenario: First run
- **WHEN** the application is started against a database that has just been created
- **THEN** every slot's catalogue is empty and the catalogue screen says so

### Requirement: A component carries a prompt wording and a judge label

Every component SHALL carry the exact text placed into the prompt, and
separately the text shown to a person judging a photograph.

The judge label SHALL describe what the photograph looks like, in the words of
someone looking at it. It SHALL NOT be the prompt wording. The two are different
questions: one composes a photograph, the other recognises one, and using the
first for the second tells the judge the answer.

A component SHALL also carry the slot it fills, the manner or manners it is
available in, and the family it belongs to for spreading purposes.

#### Scenario: A component is saved without a judge label
- **WHEN** a component is saved with an empty judge label
- **THEN** the save is refused, naming the missing field

#### Scenario: The two texts are not the same
- **WHEN** a component is saved with a judge label identical to its prompt wording
- **THEN** the save is refused

### Requirement: A component is retired, never deleted, once it has evidence

A component that has been measured SHALL NOT be removable. It SHALL be
retirable: kept, readable, excluded from every draw.

A component with no evidence against it MAY be deleted outright.

Evidence is the expensive thing in this project. Deleting a measured wording
throws away the sample it cost and invites the same wording back under a new
key.

#### Scenario: Retiring a measured component
- **WHEN** an operator retires a component that has judged photographs against it
- **THEN** it stops being drawn and offered as a judging choice, and its cells and their counts remain readable

#### Scenario: Deleting a measured component
- **WHEN** an operator asks to delete a component that has judged photographs against it
- **THEN** the deletion is refused and retiring is offered instead

### Requirement: The measured catalogue is importable and never automatic

The wordings this project has already measured SHALL be available as a file the
operator can import from the catalogue screen, one slot at a time or whole.

The import SHALL NOT run on startup, on migration, or as a fallback when the
catalogue is empty.

#### Scenario: Import is offered, not applied
- **WHEN** the application starts with an empty catalogue
- **THEN** the catalogue screen offers the import and the catalogue stays empty until the operator asks for it

#### Scenario: Importing twice
- **WHEN** the operator imports a set that is already partly present
- **THEN** components already present by key are left as they are and the result reports what was added and what was skipped
