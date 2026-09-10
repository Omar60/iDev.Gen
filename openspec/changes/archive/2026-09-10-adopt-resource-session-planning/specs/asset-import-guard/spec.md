## MODIFIED Requirements

### Requirement: Source English text is stored unaltered

For private database resource imports, accepted source objects SHALL be preserved in their original language separately from English translations. Missing English text or translations SHALL mark preparation readiness as pending rather than discard accepted source data. This exception SHALL NOT apply to tracked files, generated prompts or English user interface text. The translation and missing-English-text scenarios below apply to legacy seed imports; the import-side refusal of prohibited content is no longer in force on either path.

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

For private database resource imports, accepted source objects SHALL be preserved in their original language separately from English translations. Missing English text or translations SHALL mark preparation readiness as pending rather than discard accepted source data. This exception SHALL NOT apply to tracked files, generated prompts or English user interface text. The translation and missing-English-text scenarios below apply to legacy seed imports; the import-side refusal of prohibited content is no longer in force on either path.

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
