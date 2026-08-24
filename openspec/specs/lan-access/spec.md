# lan-access Specification

## Purpose
Reaching the app from another device on the same network — a phone, in
practice — through an entry point that has to be chosen deliberately, so the
default stays a server only this machine can talk to.

## Requirements

### Requirement: Reaching the app from the network is opt-in

The system SHALL offer a way to start the app so that another device on the same
network can reach it, and that way SHALL be distinct from the ordinary way of
starting it.

Starting the app the ordinary way SHALL keep serving on the loopback interface
only, exactly as it does today. Reaching the app from the network SHALL require
choosing the other entry point; it SHALL NOT be what happens by default, and it
SHALL NOT be reachable by omitting an argument or by mistyping one.

The entry point that opens the app to the network SHALL be named so that what it
does is legible without opening it or reading documentation.

#### Scenario: The default is unchanged
- **WHEN** the app is started the ordinary way
- **THEN** it serves on the loopback interface only, and another device on the network cannot reach it

#### Scenario: Opening it to the network
- **WHEN** the app is started through the entry point meant for network access
- **THEN** a device on the same network can reach the app

#### Scenario: Not reachable by accident
- **WHEN** the ordinary entry point is started with no arguments, or with an argument it does not understand
- **THEN** it serves on the loopback interface rather than opening to the network

### Requirement: Opening the app to the network says what it exposes

The entry point that serves the app on the network SHALL state, at the moment it
is used, that the whole app is being exposed and that it has no authentication —
so that a device on that network can read the photographs, delete sessions and
queue generations.

The warning SHALL appear where the choice is being made rather than only in
documentation.

#### Scenario: The warning is where the choice is
- **WHEN** the app is started through the entry point meant for network access
- **THEN** it states that the whole app is exposed to the network and has no authentication

### Requirement: The two entry points share one setup path

Preparing the app to run — its environment and its built interface — SHALL be
described in one place and SHALL behave identically whichever entry point is
used.

Adding the network entry point SHALL NOT create a second copy of that
preparation, so that a change to how the app is prepared cannot leave one entry
point working and the other broken.

#### Scenario: One place prepares the app
- **WHEN** the way the app is prepared to run changes
- **THEN** both entry points pick the change up, and neither carries its own copy of the preparation

#### Scenario: Either entry point starts a prepared app
- **WHEN** the app has never been prepared and is started through either entry point
- **THEN** it is prepared and starts, and which entry point was used changes only the interface it serves on
