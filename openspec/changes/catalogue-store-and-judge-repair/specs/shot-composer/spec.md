## ADDED Requirements

### Requirement: An empty catalogue refuses and says what is missing

Where a slot's catalogue holds no component available to the session, the
composer SHALL refuse and SHALL name the slot and the manner it found nothing
for.

It SHALL NOT fall back to another manner's catalogue, to a retired component, or
to any text not in the store. A silent fallback is how a shoot is composed from
a catalogue the operator did not choose, and it produces photographs that look
like a measurement of something they were never drawn from.

#### Scenario: Composing on a fresh installation
- **WHEN** a shot is composed before any component has been added
- **THEN** the composition is refused, naming the empty slots, and nothing is queued

#### Scenario: One slot has components and another does not
- **WHEN** the camera catalogue holds components for the session's manner and the act catalogue holds none
- **THEN** the composition is refused naming the act slot, and no shot is queued from the camera alone

### Requirement: The written path refuses a camera plan it cannot draw

Where the writer's path plans a camera per photograph and the manner's camera
catalogue is empty, the session SHALL be refused at creation, naming the empty
catalogue.

The writer's path is not exempt from an empty catalogue. A shoot planned with no
camera positions is the failure this project has already measured: thirty lines
with no camera clause came back as thirty frontal photographs, which reads as a
result and is an accident.

#### Scenario: A written session on an empty camera catalogue
- **WHEN** a written session is created for a manner whose camera catalogue is empty
- **THEN** the session is refused, naming the manner and the empty slot, and no shots are written

#### Scenario: A kiss frame with no camera to override to
- **WHEN** a photograph's camera would be replaced by a kiss frame's camera and that component is absent from the manner's catalogue
- **THEN** the session is refused rather than the photograph silently keeping the camera it was dealt
