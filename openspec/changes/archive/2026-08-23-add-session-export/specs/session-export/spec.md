## Purpose

Getting a rated selection of a session's finished photographs out of the app as
one file, named so the archive reads in shooting order without the app open.

## ADDED Requirements

### Requirement: Export a session's picks

The system SHALL serve the finished images of one session as a single ZIP
archive, restricted to shots whose rating is at or above a caller-supplied
threshold. The threshold SHALL default to 1, so an export with no threshold
given carries every shot the user has rated at all and no unrated ones.

A shot with no image on disk SHALL be omitted from the archive rather than
failing the export, matching the rule that one shot's failure is not the
session's failure.

The export SHALL NOT move, rename or delete anything in the session folder.

#### Scenario: Exporting the rated shots
- **WHEN** a session has shots rated 0, 2 and 5, all with images on disk, and an export is requested with no threshold
- **THEN** the response is a ZIP containing exactly the two rated images, and the session folder is unchanged

#### Scenario: Raising the threshold
- **WHEN** the same session is exported with a threshold of 3
- **THEN** the response is a ZIP containing only the shot rated 5

#### Scenario: Nothing meets the threshold
- **WHEN** no shot in the session has a rating at or above the threshold
- **THEN** the request fails with a client error naming the threshold, and no archive is produced

#### Scenario: A rated shot lost its file
- **WHEN** a shot meets the threshold but its image is missing from the session folder
- **THEN** that shot is left out and the remaining shots are still archived

#### Scenario: The session does not exist
- **WHEN** an export is requested for an unknown session
- **THEN** the request fails with a not-found error

### Requirement: Archive entries are named for reading outside the app

Each entry in the archive SHALL be named from the shot's position within the
session and its rating, preserving the original file extension. Entry names
SHALL sort in shooting order under a plain lexicographic sort, and SHALL be
unique within the archive.

The archive itself SHALL be offered under a name carrying the session's id, so
two exports do not collide in a downloads folder.

#### Scenario: Order survives the file manager
- **WHEN** a session of twelve shots is exported
- **THEN** entry names sort in shooting order lexicographically, so shot 2 precedes shot 12

#### Scenario: The download is named for its session
- **WHEN** any session is exported
- **THEN** the response names the file after the session id

### Requirement: The session view offers the export

The session view SHALL offer a control that downloads the current export and
states how many shots the active threshold selects. The control SHALL be
unavailable when the threshold selects nothing.

#### Scenario: The count is visible before downloading
- **WHEN** the user views a session where three shots meet the active threshold
- **THEN** the control reports three shots and is available

#### Scenario: Nothing to export
- **WHEN** no shot in the session meets the active threshold
- **THEN** the control is disabled and no request is made
