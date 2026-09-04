## 1. The guard

- [ ] 1.1 Write every refusal marker and every fixture in escape sequences so no tracked file carries a non-English glyph, and verify a test asserts each escaped marker still matches the text it was written for
- [ ] 1.2 Add the deny-list and the guard function in backend code, covering the four refusal signals (source library, identifier, tags, theme text) and the minor-coded profile keys, and verify a unit test refuses one fixture entry per signal
- [ ] 1.3 Make the guard un-overridable: no flag, env var, config key or caller argument may include a refused entry, and verify a test asserts that passing any such argument raises rather than imports
- [ ] 1.4 Add the reporting path so a refused entry is counted and named by identifier but never has its text written or logged, and verify a test captures the report and asserts no theme text appears in it
- [ ] 1.5 Add invented fixture entries under `tests/` so the guard's tests run with the external libraries absent, and verify the guard suite passes on a checkout with no source directory present
- [ ] 1.6 Add the test that fails when an import path writes seed rows without consulting the guard, and verify it fails against a deliberately unguarded stub before it passes

## 2. The translation pass

- [ ] 2.1 Define the translation map keyed by source string, at an untracked path beside the source material, with the source string, the English translation and the fields it covers, and verify a test rejects a map entry whose translation is empty or still contains non-English characters
- [ ] 2.2 Write the extractor that lists every non-English string in the accepted entries of a source directory given as a required argument, running the guard first so refused entries are never listed, and verify a test asserts a refused fixture's strings are absent from its output
- [ ] 2.3 Translate the highest-value tier first — the `notes` and the four `*_anchor` fields on accepted entries — and verify every translated string is present in the map and no source string in that tier is left uncovered
- [ ] 2.4 Translate the room labels for every accepted entry, and verify the map covers each accepted room's label with no gaps
- [ ] 2.5 Translate the remaining accepted fields — shot variants, non-English pose hints, and the profile display names, descriptions and identity names — and verify the extractor reports zero uncovered strings across the accepted corpus
- [ ] 2.6 Make the map the only source of translations: the importer reads it and never translates during a run, and verify a test asserts an import stops and names the entry and field when a string is missing from the map
- [ ] 2.7 Assert one source string yields one English string everywhere, and verify a test imports a string that appears in two libraries and asserts both rows carry the same translation
- [ ] 2.8 Assert re-running an import rewords nothing, and verify a test runs the import twice and compares every stored translation
- [ ] 2.9 Add the repository-wide test that no tracked file contains a non-English character, and verify it fails against a deliberately planted fixture before it passes
- [ ] 2.10 Add the test that no tracked file carries imported source prose, and verify it fails against a fixture room text planted in a tracked seed before it passes
- [ ] 2.11 Add the test that no string from a refused entry appears in the translation map, and verify it names the entry when it fails

## 3. The registry

- [ ] 3.1 Define the room registry shape in config (library name, seed file, enabled, weight) with a documented default, and verify the app starts with the default and lists the existing nine rooms
- [ ] 3.2 Replace the hard-coded room seed filename with a registry read, and verify the existing room picker still shows the same nine rooms
- [ ] 3.3 Honour the enabled flag so a disabled library contributes no rooms to the picker or a draw while remaining readable, and verify a test toggles a fixture library and asserts both directions
- [ ] 3.4 Add the test that the registry and the seed files on disk agree in both directions, and verify it fails for a registry entry naming a missing file and for a seed file no entry names
- [ ] 3.5 Update README.md and the matching page under `docs/` for the registry setting, and verify the documented default matches the shipped one

## 4. The refresh pipeline

- [ ] 4.1 Declare each source library with the kind of material it carries and every destination its entries reach, and verify a test asserts an undeclared file is refused and writes nothing
- [ ] 4.2 Declare the body-profile source as material this project does not adopt, and verify a test asserts it is refused with that reason and that the reason is distinct from a deny-list refusal
- [ ] 4.3 Implement the import once - refusal, translation lookup, merge, report - and verify a test asserts the app operation and the command-line entry produce identical seed content for one fixture source
- [ ] 4.4 Run the refusal rule and the translation lookup over the whole upload before writing anything, and verify a test asserts a source with one uncovered string leaves every destination byte-identical
- [ ] 4.5 List every uncovered string with the field it came from when an upload is refused for translation, and verify a test asserts a refused entry's strings are absent from that list
- [ ] 4.6 Match a re-import to the rows it already produced by source identifier, updating text and derived fields while keeping verdict and sample size, and verify a test re-imports a changed fixture over a measured row
- [ ] 4.7 Report a row whose source entry has disappeared upstream as orphaned without deleting it, and verify a test asserts the row survives and the report names it
- [ ] 4.8 Report accepted, refused, created, updated, unchanged and orphaned counts per destination, from the same data in both entries, and verify a test compares the two reports
- [ ] 4.9 Add the upload screen and its route, and verify `npm --prefix frontend run build` succeeds and a frontend test shows the report and the uncovered-string list

## 5. The room import

- [ ] 5.1 Write the one-shot translator script that takes the source directory as a required argument with no default, and verify it exits with an error when the argument is absent
- [ ] 5.2 Split the nine existing rooms into register and place, moving the capture-clause, hair, word-count and manner assertions from the stored row to the composed look in the same commit, and verify composing each of them yields the text it yielded before the split
- [ ] 5.3 Derive an imported room's key from the source's own identifier, normalised to ASCII and independent of any label or translation, and verify a test asserts the key is unchanged after a translation is corrected and the source re-imported
- [ ] 5.4 Store an imported room as the place alone with no manner register in its text, and verify a test composes one imported room under two manners and asserts each carries its own register and the same place text byte-identical
- [ ] 5.5 Store each accepted entry's English theme string byte-identical, and verify a test compares a fixture's stored text against its source character for character
- [ ] 5.6 Store the English label and the other translated fields from the map, in seed fields distinct from the ones holding source text, and verify a test reads a seed row and asserts which values are source and which are authored
- [ ] 5.7 Derive `offers` from the room's English source prose intersected with the source prop list, keeping only pieces the prose names, and verify the existing offers-names-the-furniture test passes over every imported room and never reads a translated field
- [ ] 5.8 Compute and store the multi-body marking at import from the room's own source text, and verify a test marks a fixture whose text names other people and leaves a single-subject fixture unmarked
- [ ] 5.9 Store the translated authoring guidance with the room and keep it out of any composed line, and verify a test composes a room carrying guidance and asserts the guidance is absent from the result
- [ ] 5.10 Make the import idempotent: an existing room's text, offers and translations update while its verdict and sample size are preserved, and verify a test runs the import twice over a fixture carrying a verdict and asserts the verdict survives
- [ ] 5.11 Skip and report an entry whose English theme string is empty rather than inventing text, and verify a test asserts the skip and the report
- [ ] 5.12 Import one source library at a time into its own seed file, largest last, and verify `python -m pytest` is green after each library lands

## 6. The room picker

- [ ] 6.1 Keep the project's own rooms tracked and build-time, write imported rooms to an untracked path, and verify `tests/test_catalogue_seed.py` still passes and the frontend builds on a checkout with no import run
- [ ] 6.2 Compose the manner's register with the room's place when a room is picked, adding no register to a hand-written look, and verify a test asserts a look filled from one room under two manners differs only in its register
- [ ] 6.3 Say that a look carries another manner's register when the session's manner changes, offering a refill and rewriting nothing, and verify a frontend test asserts edited text survives a manner change
- [ ] 6.4 Serve imported rooms at runtime through a route, returning an empty list and a stated reason when the library is absent, and verify a frontend test renders the picker with the route empty
- [ ] 6.5 Store verdicts, sample sizes and the manner measured under in a tracked file keyed by room key, carrying no source prose, and verify a test asserts the file holds no room text
- [ ] 6.6 Report a verdict whose room is absent as orphaned without deleting it, and verify a test asserts it survives an import that no longer carries that room
- [ ] 6.7 Replace a room's single `manner` with the set of manners it allows, defaulting to all, and verify a test asserts an imported fixture allows every manner and the studio row allows only the manners where someone is photographing her
- [ ] 6.8 Filter the picker by the allowed set rather than by equality, and verify a frontend test lists a room allowed everywhere under each manner and hides the studio under the others
- [ ] 6.9 Add a verdict and a sample size per manner to the room seed shape with an unverified default, and verify a test asserts every stored verdict is one of the catalogue's words and carries a sample size
- [ ] 6.10 Convert the nine rooms' free-text verdicts to that vocabulary against the manner they were measured under, at the sample size the text states and no higher, keeping the original sentence as a note, and verify a test asserts none of the nine is stored verified and each keeps its sentence
- [ ] 6.11 Show a room as unverified for a session whose manner it has no verdict under, and verify a frontend test asserts a room verified in one manner is not shown as verified in another
- [ ] 6.12 Show the verdict in the picker so verified and unverified rooms are visibly distinct, and verify `npm --prefix frontend run build` succeeds and the frontend test covers the distinction
- [ ] 6.13 Store the source's tags on the room and read the picker's tag filter from them, and verify a frontend test filters a fixture by a tag its source entry carried
- [ ] 6.14 Store the source's mood words with the room's authoring guidance and offer no filter over them, and verify a test asserts they are readable and absent from any composed line
- [ ] 6.15 Add the weighted draw at session creation, over the rooms the session's manner allows, filling the look once and showing the drawn room's verdict, and verify a test asserts the draw is not repeated when the session is reopened
- [ ] 6.16 Report an imported weight far outside the range the library otherwise uses, naming the entry, and verify a test asserts the two outlying fixtures are named and their weights are stored unchanged
- [ ] 6.17 Add text and tag filtering to the picker, matching text against both the translated label and the English source prose, and verify a frontend test finds one fixture room by its label and another by a word inside its prose
- [ ] 6.18 Keep the session's current room reachable regardless of the active filter, and verify a frontend test filters it out and asserts it is still shown as the current selection
- [ ] 6.19 Make a room's authoring guidance readable from the picker without composing it, and verify a frontend test shows the guidance for a fixture room
- [ ] 6.20 Confirm selecting a room fills only the look, and verify a test asserts the session's wardrobe, shots and settings are unchanged

## 7. The compose gates

- [ ] 7.1 Record the key of the room a session's look was filled from - a column on `session`, empty for a hand-written look and for every session that exists today - in `SCHEMA` and in `_migrate` together, and verify `tests/test_db_migrate.py` opens a database written before the column and asserts the migrated session reads an empty key
- [ ] 7.2 Carry the key through the picker and the compose payload, and offer the operator a way to detach it that keeps the look's text and clears the key, and verify a test asserts picking a room stores its key, replacing the room replaces it, detaching clears it while the text stays byte-identical, and a session with no key composes unrefused
- [ ] 7.3 Refuse a compose whose room is marked multi-body when the run has no second body declared, naming the room and the words responsible, and verify a test asserts the refusal text carries both
- [ ] 7.4 Compose the same room unchanged when the run declares a second body, and verify a test asserts the composed room text is byte-identical to the stored source text
- [ ] 7.5 Add the configured room word budget with a conservative documented default, refusing over-budget rooms with the count and the budget in the message, and verify a test asserts both numbers appear
- [ ] 7.6 Assert no silent shortening: every successful compose carries the stored source text byte-identical, and verify a property-style test over every seeded room
- [ ] 7.7 Refuse a run before anything is queued when its room fails a gate, leaving no partial shots, and verify a test asserts the shot count is unchanged after a refused run
- [ ] 7.8 Add the ahead-of-time report listing which rooms a planned run would refuse and why, and verify a test asserts it queues nothing
- [ ] 7.9 Write the measurement arm that varies room length alone against a fixed camera row, run it, and verify the budget default is set from its result and the number is recorded in design.md's Open Questions

## 8. Perspective mining

- [ ] 8.1 Write the curated cut map at an untracked path beside the translation map - one entry per source identifier naming the substring that becomes the camera, the one that becomes the act and the one that becomes the room, omitting what becomes nothing - and verify a test asserts every cut is a substring of its source entry and that an entry missing from the map stops the import instead of being guessed at
- [ ] 8.2 Split a fused source entry by reading that map only, never by parsing its prose, each row recording the source identifier, and verify a test asserts no single row carries both a camera position and an act
- [ ] 8.3 Produce no row for a part the entry does not carry, and verify a test over a camera-and-room-only fixture asserts no act row is written
- [ ] 8.4 Resolve every template placeholder before storage or skip and report the entry, and verify a test rejects a seed row containing an unresolved placeholder
- [ ] 8.5 Add the `pov` manner by inheriting the baseline manner and changing only its identity, with no instruction prose of its own, and verify a test asserts its brief and line are empty
- [ ] 8.6 Declare the manner per source family - the participant-camera families to `pov`, the unattended-camera family to the existing manner that describes one - and verify a test asserts every mined row carries the manner its family declares
- [ ] 8.7 Derive each mined act's requirement from its wording by the reading the catalogue already uses, floored per family, setting the requirement where the reading is uncertain, and verify a test asserts an act whose wording omits the second body still carries it from the family floor
- [ ] 8.8 Report and refuse to write a mined act whose requirement cannot be determined, and verify a test asserts nothing is written and the entry is named
- [ ] 8.9 Keep a mined act carrying the second-body requirement out of a run that has not switched it on, and verify a test composes a single-body run and asserts no such act is drawn
- [ ] 8.10 Store recorded combinations as row keys with no row text, in a tracked file, and verify a test asserts the file carries no prose and that a combination survives its rows being absent
- [ ] 8.11 Compose a recorded combination from the app without hand assembly, and verify a test asserts it is never dealt by a session's draw
- [ ] 8.12 Record the combination each mined source entry was split into, and verify a test composes one recorded combination and asserts it reproduces the source entry's photograph
- [ ] 8.13 Report a recorded combination as broken when one of its rows is retired or reworded, and verify a test asserts it is not composed from the remaining rows
- [ ] 8.14 Store mined camera and act rows unverified with a judge label on each camera, and verify a test asserts the verdict and the presence of the label
- [ ] 8.15 Report a mined row whose wording duplicates an existing catalogue row and create no second row, and verify a test asserts the duplicate is reported and the row count is unchanged
- [ ] 8.16 Record the camera concepts the mining introduces that the catalogue did not hold, and verify the record names the feet-first low POV and the overhead-over-kneeling-subject concepts
- [ ] 8.17 Keep unverified mined rows out of the strict composer's draw, and verify a test asserts an unverified mined camera is never drawn on the strict path
- [ ] 8.18 Judge the new camera concepts under the project's existing blind judging protocol and record their verdicts, and verify the recorded sample size matches the protocol's minimum
- [ ] 8.19 Shoot the recorded combinations against their mined parts drawn separately, and verify the result is recorded as the evidence for whether a `pov` instruction block is worth writing

## 9. The writer field set

- [ ] 9.1 Add `accessories`, `marks`, `style`, `props` and `story` to the writer's field list and to the joiner's headings in the same edit, with the all-fields statement and the count updated to twelve, and verify the test that binds the two lists passes
- [ ] 9.2 Update the JSON skeleton's own heading and the regex that reads it - `THE SIX FIELDS` becomes the eleven the skeleton carries once `technique` is left out - in `frontend/src/kinds.js` and `tests/test_enhance.py` in the same edit, and verify the test still fails when a field is added to one list and not the other
- [ ] 9.3 Keep `hair` and `makeup` out of the field list, the key header and the joiner's headings while the session's look defines them, and verify a test asserts neither name appears in the writer's instruction and that a composed line answers hair once
- [ ] 9.4 Write the field descriptions so `style` carries lens, medium and treatment only and `marks` carries her skin only, with neither carrying a camera position or a framing, and verify a measurement run reports a position written into `style` as a fault
- [ ] 9.5 Keep `technique` out of the key header and out of the JSON skeleton, as the control has it, so the lens is answered once, and verify its arrival stays at the control's rate
- [ ] 9.6 Re-run the writer measurement against the shipped control at the documented protocol, and verify arrival, survival and the arrival of the pre-existing fields all match the measured arm
- [ ] 9.7 Add `with_tattoo`, `with_pet` and `with_liquids` as run-level switches in the same shape as `with_him` and `with_furniture`, defaulting off, and verify a test asserts each name appears nowhere in the writer's instruction when its switch is off
- [ ] 9.8 Require the run to supply the subject when such a switch is on, refusing the run before the shoot is written when it is not, and verify a test asserts the refusal names the field
- [ ] 9.9 Carry a supplied subject verbatim across the shoot in the photographs that do not change it, and verify a test asserts the supplied words are repeated unchanged
- [ ] 9.10 Add the three switches and their subject inputs to the compose panel in `SessionView.jsx`, in the same shape as the `with_him` and `with_furniture` checkboxes it already carries, and verify `npm --prefix frontend run build` succeeds and a frontend test asserts a switched-on field with an empty subject cannot be sent
- [ ] 9.11 Add the test that no line describes a switched-off field's subject, and verify it fails against a fixture line carrying an invented tattoo before it passes

## 10. Close-out

- [ ] 10.1 Run `python -m pytest` and `npm --prefix frontend run build` and `npm --prefix frontend test`, and verify all three are green
- [ ] 10.2 Confirm no tracked file contains a machine path, a real person's name or a non-English character, and verify `tests/test_no_personal_data.py` and the non-English test pass over the new seeds, the translation map and the scripts
- [ ] 10.3 Update README.md and the matching `docs/` pages for the room picker, the registry, the translation map and any shipped writer field, and verify each documented default matches the code
- [ ] 10.4 Rewrite the `use_look` tooltip in `ModelDetail.jsx`, which still warns that a look past ~85 composed words costs the position and framing, to say what session 394 measured and what the word budget now does, and verify the number it quotes matches the budget 7.9 sets

