## Purpose

Playing the photographs a shoot produced, across every session at once, in a
random order and full screen, so the keepers can be looked at from across the
room instead of on the machine that made them.

## ADDED Requirements

### Requirement: Listing photographs across sessions

The system SHALL list finished photographs from every session in one response,
narrowed by a minimum rating.

A photograph SHALL be listed only when it has been shot, it was not rejected,
and its rating is at or above the threshold. A threshold of `0` SHALL therefore
list every finished, un-rejected photograph including those never rated, and a
threshold of `4` SHALL list only those rated four or five. The threshold SHALL
match the meaning it already carries elsewhere in the app: at or above, never
strictly above.

The session a photograph belongs to SHALL NOT narrow the list. Each listed
photograph SHALL carry enough to be shown and named — at minimum its own id and
the name of the session it came from — so the screen needs no request per
photograph and no second request to say where a frame is from.

A threshold matching nothing SHALL return an empty list, not an error.

This listing SHALL be read-only: it SHALL NOT alter any photograph, session or
rating.

#### Scenario: Every session at once
- **WHEN** photographs rated four or higher exist in three different sessions
- **THEN** a request at a threshold of four lists photographs from all three, and the response does not depend on any session id

#### Scenario: The threshold is inclusive
- **WHEN** one photograph is rated exactly four and another exactly three
- **THEN** a threshold of four lists the first and not the second

#### Scenario: A threshold of zero takes the unrated
- **WHEN** a finished, un-rejected photograph has never been rated
- **THEN** a threshold of zero lists it

#### Scenario: Rejected and unfinished frames are never listed
- **WHEN** a session holds a rejected photograph, a pending shot and a failed shot, each of which would otherwise pass the threshold
- **THEN** none of the three is listed

#### Scenario: Each entry names its session
- **WHEN** a photograph is listed
- **THEN** the entry carries the name of the session it came from

#### Scenario: Nothing meets the threshold
- **WHEN** no photograph is at or above the requested rating
- **THEN** the response is an empty list and the request succeeds

#### Scenario: Listing changes nothing
- **WHEN** the list is requested
- **THEN** no rating, no photograph and no session is modified

### Requirement: The slideshow plays in a random order that does not repeat

The system SHALL show one photograph at a time and advance to another
automatically, in an order that is not the order the photographs were shot in.

Every photograph in the current set SHALL be shown once before any is shown a
second time. This SHALL hold for a small set as much as a large one: with
thirteen photographs, the thirteenth shown SHALL be the one not yet seen, not a
repeat of the second.

When the set is exhausted the order SHALL be drawn again, so a second pass is
not a replay of the first.

A set holding one photograph SHALL show that photograph and SHALL NOT fail. An
empty set SHALL say so on screen rather than showing a blank frame.

#### Scenario: No repeat before exhaustion
- **WHEN** the set holds thirteen photographs and thirteen advances happen
- **THEN** each of the thirteen has been shown exactly once

#### Scenario: A second pass is reordered
- **WHEN** the set is exhausted and playing continues
- **THEN** the photographs play again in a freshly drawn order

#### Scenario: Not the order they were shot
- **WHEN** the slideshow plays a set drawn from several sessions
- **THEN** the photographs do not appear grouped by session in the order they were shot

#### Scenario: A single photograph
- **WHEN** exactly one photograph meets the threshold
- **THEN** it is shown and the screen does not fail

#### Scenario: Nothing to play
- **WHEN** no photograph meets the threshold
- **THEN** the screen says so plainly instead of showing a blank frame

### Requirement: The interval and the rating threshold are configurable

The system SHALL let the user set how many seconds a photograph is held before
the next one, and the minimum rating a photograph needs to be included.

Both settings SHALL be readable and settable from the address of the screen, so
that saving the address — including adding it to a phone's home screen —
restores the same configuration when it is opened again.

Changing the threshold SHALL rebuild the set being played. Changing the interval
SHALL take effect without restarting the set.

An interval or threshold that is absent, out of range or not a number SHALL fall
back to a working default rather than leaving the screen stopped or blank.

#### Scenario: The address carries the settings
- **WHEN** the user sets an interval and a threshold and then re-opens the same address
- **THEN** the slideshow plays with that interval and that threshold

#### Scenario: Raising the threshold mid-play
- **WHEN** the threshold is raised while the slideshow is playing
- **THEN** the set is rebuilt to the photographs meeting the new threshold and play continues

#### Scenario: Changing the interval mid-play
- **WHEN** the interval is changed while the slideshow is playing
- **THEN** the new interval takes effect and the set being played is not restarted

#### Scenario: A nonsense interval
- **WHEN** the address carries an interval that is not a usable number
- **THEN** the slideshow plays at its default interval rather than stopping

### Requirement: The interval and the threshold are changeable from the screen

The system SHALL offer controls on the slideshow itself for the interval and for
the rating threshold, so neither has to be changed by editing the address.

This is what makes the settings usable on the device the slideshow is for: a
phone held at arm's length, often with the address bar hidden by full screen,
where typing a query string is the most expensive input available.

A control SHALL show the value currently in effect, including one that arrived
from the address, so the screen never disagrees with what it is playing.

Changing a setting from a control SHALL update the screen's address to match, so
that the configuration keeps travelling with a saved address or a home-screen
shortcut. Changing it SHALL take effect on the slideshow under the rules already
stated: the threshold rebuilds the set, the interval retunes the timer without
restarting the set.

The controls SHALL remain read-only with respect to the photographs: none of
them rates, rejects, deletes, edits or queues anything.

#### Scenario: Changing the threshold without touching the address bar
- **WHEN** the user changes the rating threshold from the control on the slideshow
- **THEN** the set is rebuilt to the photographs meeting the new threshold and play continues

#### Scenario: Changing the interval without touching the address bar
- **WHEN** the user changes the interval from the control on the slideshow
- **THEN** the new interval takes effect and the set being played is not restarted

#### Scenario: The address follows the control
- **WHEN** a setting is changed from a control
- **THEN** the screen's address carries the new value, and re-opening that address plays with it

#### Scenario: The control shows what is in effect
- **WHEN** the slideshow is opened at an address carrying an interval and a threshold
- **THEN** each control shows that value rather than a default

#### Scenario: A value the control does not offer
- **WHEN** the address carries a valid interval that is not one of the control's listed choices
- **THEN** the control shows that value rather than appearing blank or silently changing it

#### Scenario: The controls change no photograph
- **WHEN** any control on the slideshow is used
- **THEN** no rating, no photograph and no session is modified

### Requirement: The next photographs are decoded before they are shown

The system SHALL prepare a configurable number of upcoming photographs ahead of
the one on screen, and preparing one SHALL include decoding it, not only
fetching it.

Decoding ahead is what the requirement is for: a photograph that has been
fetched but not decoded still costs its decode at the moment it is swapped in,
which is the moment this exists to keep smooth.

A photograph that fails to fetch or decode SHALL NOT stop the slideshow; play
SHALL continue with the photographs that are available.

#### Scenario: The next photograph is ready before its turn
- **WHEN** a photograph is on screen and the interval has not elapsed
- **THEN** the photographs due after it have already been fetched and decoded

#### Scenario: Preparing further ahead
- **WHEN** the number of photographs to prepare ahead is raised
- **THEN** that many upcoming photographs are fetched and decoded ahead of the one on screen

#### Scenario: One photograph fails
- **WHEN** an upcoming photograph cannot be fetched or decoded
- **THEN** the slideshow continues rather than stopping on it

### Requirement: The slideshow fills the screen and changes nothing

The slideshow SHALL present the photograph filling the screen, without the app's
navigation and surrounding furniture competing with it, and the whole photograph
SHALL remain visible rather than being cropped to fit.

The screen SHALL be reachable from the app's navigation and SHALL be leavable,
returning the user to the app.

The slideshow SHALL offer no way to rate, reject, delete or edit a photograph,
or to queue any generation. It reads.

#### Scenario: The photograph is not cropped
- **WHEN** a photograph whose shape differs from the screen's is shown
- **THEN** the whole photograph is visible

#### Scenario: Reaching it and leaving it
- **WHEN** the user opens the slideshow from the app and then leaves it
- **THEN** the slideshow plays, and leaving returns to the app

#### Scenario: Read-only
- **WHEN** the slideshow is playing
- **THEN** no control on it rates, rejects, deletes, edits or generates anything
