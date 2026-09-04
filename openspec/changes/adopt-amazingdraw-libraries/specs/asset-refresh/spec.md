# asset-refresh Specification

## Purpose

One pipeline for bringing an outside asset library into this repo and for
bringing it in again when it changes upstream, so that a refreshed source is a
file the operator drops in and a report they read, rather than a hand-run script
somebody has to remember the arguments for.

## ADDED Requirements

### Requirement: One import pipeline, more than one entry

The system SHALL implement the import of an asset library once, and SHALL expose
it both as an operation inside the app and as a command-line entry that calls
the same implementation.

The two entries SHALL produce identical output for identical input. A second
implementation of any step - the refusal rule, the translation lookup, the merge
- SHALL NOT exist.

#### Scenario: The same source through both entries
- **WHEN** one source library is imported through the app and through the command line
- **THEN** the resulting seed content is identical

#### Scenario: A rule changed in one place
- **WHEN** the refusal rule, the translation lookup or the merge changes
- **THEN** both entries change with it, because there is one implementation

### Requirement: Every source kind declares its handler and destination

The system SHALL require each source library to be declared with the kind of
material it carries and the destination its entries are written to, and SHALL
refuse to import a library whose kind is not declared.

A source whose entries are split across more than one destination SHALL declare
all of them, so that an operator reading the declaration can see everything an
upload of that file will touch.

An upload of a file the system cannot identify as a declared source SHALL be
refused, naming what it could not identify, and SHALL write nothing.

#### Scenario: A declared source
- **WHEN** a declared source library is uploaded
- **THEN** its entries are written to the destinations its declaration names, and to no others

#### Scenario: An undeclared source
- **WHEN** a file that matches no declared source is uploaded
- **THEN** the import is refused, the reason names what could not be identified, and nothing is written

#### Scenario: A source with several destinations
- **WHEN** a source whose entries are split across destinations is uploaded
- **THEN** the declaration names every destination and the report accounts for every entry

#### Scenario: A source this project does not adopt
- **WHEN** a source declared as carrying material this project does not adopt is uploaded
- **THEN** it is refused with that reason, distinct from both an unidentified file and a refusal by the deny-list, and nothing is written

### Requirement: Refusal and translation run before anything is written

The system SHALL apply the refusal rule to every entry of an uploaded source
before any of it is written, and SHALL resolve every non-English string against
the translation map before any of it is written.

An upload carrying non-English strings the map does not cover SHALL be refused
in whole. The refusal SHALL list every uncovered string and the field it came
from, so that the map can be completed and the same file uploaded again.

A refused entry SHALL NOT be listed among the uncovered strings, because it is
never translated.

Nothing SHALL be written by an upload that is refused. A refused upload SHALL
leave every destination exactly as it was.

#### Scenario: An updated source with new untranslated text
- **WHEN** a refreshed source is uploaded carrying strings the map does not cover
- **THEN** the upload is refused in whole, every uncovered string and its field are listed, and no destination changes

#### Scenario: Completing the map and uploading again
- **WHEN** the listed strings are added to the map and the same file is uploaded
- **THEN** the import proceeds

#### Scenario: A refused entry in an uploaded source
- **WHEN** an uploaded source contains a refused entry
- **THEN** that entry is skipped, its strings are not listed as uncovered, and its text is not written or logged

### Requirement: A refresh preserves what has been measured

The system SHALL treat a second import of a source as an update of the rows it
already produced, matched by the source identifier the rows carry.

An updated row SHALL take the source's new text, its new derived fields and its
new translations, and SHALL keep any verdict and sample size recorded against
it. A row whose source entry has disappeared upstream SHALL be reported and
SHALL NOT be deleted, because a verdict measured against it is a measurement
this repo owns.

An import SHALL NOT skip a row that already exists: the drift this repo has
already found came from an import that left existing rows alone and let a stale
file survive a green test suite.

#### Scenario: Re-importing over a measured row
- **WHEN** a source is uploaded again and one of its entries has changed
- **THEN** the row's text and derived fields update and its verdict and sample size survive

#### Scenario: An entry that disappeared upstream
- **WHEN** a refreshed source no longer contains an entry that was imported before
- **THEN** the row is reported as orphaned and is not deleted

#### Scenario: An unchanged entry
- **WHEN** a source is uploaded again unchanged
- **THEN** no destination content changes

### Requirement: An import reports what it did before it is trusted

The system SHALL report, for every import, how many entries were accepted,
refused, created, updated, left unchanged and orphaned, broken down by
destination.

The report SHALL be shown for an app upload and printed for a command-line run,
from the same data.

Because the imported material is not committed, the report is the only review an
import gets. It SHALL therefore carry what a diff would have shown: which rows
were created, which were updated and what changed in each, not counts alone.

The report SHALL be written to a file as well as displayed, so that it can be
read after the import and kept beside the material it describes.

#### Scenario: Reading what an upload did
- **WHEN** an upload completes
- **THEN** the counts of accepted, refused, created, updated, unchanged and orphaned entries are shown per destination

#### Scenario: Reviewing the change without a diff
- **WHEN** an import has written its result into files that are not committed
- **THEN** the report names every row created or updated and what changed in each

#### Scenario: Reading the report later
- **WHEN** an import finished some time ago
- **THEN** its report can still be read from the file it wrote
