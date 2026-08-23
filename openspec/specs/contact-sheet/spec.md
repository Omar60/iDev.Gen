# contact-sheet Specification

## Purpose
Seeing a whole shoot on one page, so the takes can be compared against each
other instead of one at a time.

## Requirements

### Requirement: A contact sheet of a session's selection

The system SHALL return a single image laying out a session's selected
photographs as a grid, in shooting order, reading left to right and then down.

The selection SHALL be the session's finished, unrejected photographs whose
rating is at or above a caller-supplied threshold. The threshold SHALL default
to 1, so a request with no threshold given carries every photograph the user has
rated at all and none of the unrated ones.

A photograph whose file is missing from the session folder SHALL be left out of
the sheet rather than failing the request, matching the rule that one shot's
failure is not the session's failure.

The route SHALL NOT write anything into the session folder, and SHALL NOT move,
rename or delete anything there.

#### Scenario: The sheet carries the rated photographs
- **WHEN** a session has photographs rated 0, 2 and 5, all finished and present on disk, and a sheet is requested with no threshold
- **THEN** the response is one image carrying the two rated photographs, and the session folder is unchanged

#### Scenario: Raising the threshold
- **WHEN** the same session's sheet is requested with a threshold of 3
- **THEN** the sheet carries only the photograph rated 5

#### Scenario: A rejected photograph stays off the sheet
- **WHEN** a photograph meets the threshold but has been rejected
- **THEN** it does not appear on the sheet

#### Scenario: An unfinished photograph stays off the sheet
- **WHEN** a session has shots that are pending, failed or cancelled
- **THEN** none of them appears on the sheet

#### Scenario: Nothing meets the threshold
- **WHEN** no photograph in the session meets the threshold
- **THEN** the request fails with a client error naming the threshold, and no image is produced

#### Scenario: A selected photograph lost its file
- **WHEN** a photograph meets the threshold but its file is missing from the session folder
- **THEN** it is left out and the remaining photographs are still laid out

#### Scenario: The session does not exist
- **WHEN** a sheet is requested for an unknown session
- **THEN** the request fails with a not-found error

### Requirement: Every cell is labelled

Each photograph on the sheet SHALL carry a label identifying which shot it is,
so a frame picked off the sheet can be found again among the session's files
without opening the app. The label SHALL distinguish two variations of the same
take from each other.

The sheet SHALL be offered under a filename carrying the session's id, so two
sheets do not collide in a downloads folder.

#### Scenario: A frame can be traced back
- **WHEN** a sheet is produced for a session where one take has three variations
- **THEN** each of the three carries a different label, and each label names the file it came from

#### Scenario: The download is named for its session
- **WHEN** any sheet is requested
- **THEN** the response names the file after the session id

### Requirement: The session view offers the sheet

The session view SHALL offer a control that downloads the contact sheet for the
active rating threshold, beside the existing export control. The control SHALL
be unavailable when the threshold selects nothing.

#### Scenario: Downloading the sheet
- **WHEN** the user views a session where photographs meet the active threshold
- **THEN** the control is available and downloads the sheet for that threshold

#### Scenario: Nothing to lay out
- **WHEN** no photograph in the session meets the active threshold
- **THEN** the control is disabled and no request is made
