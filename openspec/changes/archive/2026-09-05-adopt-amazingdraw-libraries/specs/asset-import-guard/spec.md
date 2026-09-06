# asset-import-guard Specification

## Purpose

The rule that decides what may enter this repo from an outside asset library,
enforced on every import path rather than left to whoever runs the script, so
that material this project will not carry cannot arrive through a new importer
somebody adds later.

## ADDED Requirements

### Requirement: Refused source material

The system SHALL refuse to import any entry that is school-set, that describes a
minor, or that names a real person, and SHALL do so on every import path without
exception.

An entry is refused when any of the following holds:

- its source library is one of the refused libraries named in the guard;
- its identifier, label, tags or theme text matches the guard's school markers;
- it is a body profile whose key matches the guard's minor-coded profile keys;
- it carries a real person's name, which includes any entry sourced from a
  celebrity or likeness list.

A refused entry SHALL NOT be written to any seed file, any database row, any log
line that reproduces its text, or any scratch file inside the repository. The
importer SHALL report the count of refused entries and their identifiers, and
SHALL NOT report their content.

Refusal SHALL NOT be overridable by a command-line flag, an environment
variable, a config entry or a caller argument.

#### Scenario: A refused library is offered to the importer
- **WHEN** an import is run over a source directory containing a refused library
- **THEN** no entry from that library reaches any output, and the run reports the library as refused with the number of entries skipped

#### Scenario: A refused entry inside an accepted library
- **WHEN** an accepted library contains an entry matching a school marker
- **THEN** that entry alone is skipped, the rest of the library imports, and the run names the skipped identifier

#### Scenario: A minor-coded body profile
- **WHEN** a profile whose key matches the guard's minor-coded keys is offered
- **THEN** it is skipped and no part of its text is written or logged

#### Scenario: An attempt to bypass the guard
- **WHEN** an importer is invoked with any flag, environment variable or argument that asks for refused entries to be included
- **THEN** the run fails with an error and imports nothing

### Requirement: The guard is proven by test

The system SHALL carry an automated test that fails if any import path can
produce a refused entry.

The test SHALL run without a GPU, without a running ComfyUI and without the
network. It SHALL exercise the guard against fixture entries invented for the
test, never against the external source libraries, so that the test passes on a
machine where those libraries are absent.

The test SHALL fail if a new import path is added that does not route through
the guard.

#### Scenario: A new importer skips the guard
- **WHEN** an import path is added that writes seed rows without consulting the guard
- **THEN** the test suite fails and names the unguarded path

#### Scenario: The external libraries are absent
- **WHEN** the test suite runs on a checkout with no external source libraries present
- **THEN** the guard's tests still run and pass against their own fixtures

### Requirement: Source English text is stored unaltered

The system SHALL store an accepted entry's English source text exactly as the
source wrote it, with no trimming, rewording, summarising or reordering.

An entry whose English source text is missing or empty SHALL be skipped and
reported, not filled in by the importer.

#### Scenario: A long source string
- **WHEN** an accepted entry carries a 200-word English theme string
- **THEN** the stored value is byte-identical to the source, including its punctuation and word order

#### Scenario: An entry with no English text
- **WHEN** an accepted entry has an empty theme string
- **THEN** it is skipped and reported, and no placeholder text is invented for it

### Requirement: Non-English source text is carried across as an authored translation

The system SHALL store an English translation of an accepted entry's non-English
fields, and SHALL store it as text this project authored rather than as source
text.

A stored translation SHALL be distinguishable from stored source text by
inspection of the seed file alone, so that no reader or diff can mistake one for
the other. The verbatim guarantee above SHALL apply to source text only, and
SHALL NOT be claimed for a translation.

No tracked file SHALL contain non-English characters, whether as source text, as
a translation, as a comment or as a test fixture.

An accepted entry whose non-English field has no translation available SHALL
stop the import and be reported. The importer SHALL NOT translate on the fly,
invent a value, or fall back to the source text.

#### Scenario: A non-English label on an accepted entry
- **WHEN** an accepted entry's label is not in English
- **THEN** an English translation is stored, marked as authored, and the source label text is not written to any tracked file

#### Scenario: Telling a translation from source text
- **WHEN** a seed row carrying both source text and a translation is read
- **THEN** which is which is apparent from the row itself

#### Scenario: A missing translation
- **WHEN** an accepted entry carries a non-English field that the translation map does not cover
- **THEN** the import stops and reports the entry and the field, and writes nothing for it

#### Scenario: Non-English characters in the tree
- **WHEN** any tracked file contains a non-English character
- **THEN** the test suite fails and names the file

### Requirement: The guard's own markers carry no literal non-English text

The system SHALL express every refusal marker, and every test fixture that
exercises one, without writing a non-English character into a tracked file.

A marker that matches non-English source text SHALL be written as escape
sequences or as codepoints, never as the glyphs themselves. This applies to the
deny-list in code exactly as it applies to a fixture: the rule that no tracked
file carries non-English characters has no exception for the code that enforces
it.

The tests SHALL prove that the escaped markers match the text they are meant to
match, so that escaping cannot silently produce a marker that matches nothing.

#### Scenario: A marker for non-English source text
- **WHEN** the deny-list needs to match a school marker written in the source's own script
- **THEN** it is stored escaped, and no tracked file contains the glyphs

#### Scenario: A fixture exercising that marker
- **WHEN** a test fixture must contain text in the source's script
- **THEN** it is built from escape sequences at test time rather than written into a file as glyphs

#### Scenario: An escaped marker that matches nothing
- **WHEN** a marker is escaped incorrectly
- **THEN** a test fails because the marker no longer matches the text it was written for

### Requirement: Translations are deterministic and reviewable

The system SHALL record every translation once, in a map keyed by the source
string and kept beside the source material rather than committed, and SHALL read translations from that map rather than producing
them during an import.

The same source string SHALL always yield the same English string, in every
library and every seed file it appears in. Re-running an import SHALL NOT reword
a translation already stored.

Producing or changing a translation SHALL be visible as a change to the map, so
that the review of the translations is a review of one file rather than of every
seed row the import touched.

Reading the map SHALL NOT require the network, so that the test suite runs
without it.

#### Scenario: The same source string in two libraries
- **WHEN** one source string appears in two source libraries
- **THEN** both imported rows carry the same English translation

#### Scenario: Re-running an import
- **WHEN** an import runs twice over the same source
- **THEN** no stored translation changes between the runs

#### Scenario: Reviewing a translation change
- **WHEN** a translation is corrected
- **THEN** the correction appears as a change to the map, and the next import carries it into the seeds

#### Scenario: Running the tests offline
- **WHEN** the test suite runs on a machine with no network
- **THEN** the translation tests pass

### Requirement: A refused entry is never translated

The system SHALL apply the refusal rule before translation, and SHALL NOT
translate, store or log any part of a refused entry.

A refused entry's strings SHALL NOT appear in the translation map.

#### Scenario: A refused entry reaching the translation pass
- **WHEN** a refused entry is present in a source library being translated
- **THEN** none of its strings are translated and none appear in the map

#### Scenario: A refused string already in the map
- **WHEN** the translation map is found to contain a string from a refused entry
- **THEN** the test suite fails and names the entry
