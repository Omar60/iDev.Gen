# Spec Delta

## ADDED Requirements

### Requirement: Slideshow playback can be paused and resumed

The Slideshow SHALL begin in playing state. Activating Pause SHALL suspend every automatic index advance without changing the selected photograph or deck. Activating Resume SHALL keep the current selection and wait one complete configured interval before the next timer advance.

#### Scenario: Opening starts autoplay
- **WHEN** the user opens Slideshow
- **THEN** playback starts automatically and advances after the configured interval

#### Scenario: Pausing holds the current photograph
- **WHEN** the user pauses playback partway through an interval
- **THEN** the selected photograph and deck order remain unchanged and no automatic advance occurs until playback resumes

#### Scenario: Resuming waits a full interval
- **WHEN** the user resumes playback
- **THEN** the selected photograph remains visible until one complete configured interval has elapsed

#### Scenario: Changing interval while paused
- **WHEN** the user changes the interval while playback is paused
- **THEN** playback remains paused and the new interval is used after Resume

### Requirement: Pause state follows the mounted Slideshow view

Pause state SHALL persist through rating updates, Retry, listing or image errors, and fullscreen transitions while the Slideshow remains mounted. Pause SHALL NOT be stored in the URL. Leaving and reopening Slideshow SHALL start playback.

#### Scenario: Rating updates a paused set
- **WHEN** a rating change rebuilds the set while playback is paused
- **THEN** the rebuilt set remains paused

#### Scenario: Retry recovers a paused set
- **WHEN** Retry succeeds while playback is paused
- **THEN** the refreshed set remains paused

#### Scenario: Fullscreen changes preserve pause
- **WHEN** fullscreen is entered or exited while playback is paused
- **THEN** playback remains paused and the selected index is unchanged

#### Scenario: Reopening starts playback
- **WHEN** the user leaves Slideshow and later opens it again with the same URL
- **THEN** playback starts automatically and the URL contains no playback setting

### Requirement: Playback control is accessible and reflects availability

Slideshow SHALL show a native keyboard-operable playback button named Pause slideshow while playing and Resume slideshow while paused. Activation SHALL retain focus. The button SHALL be disabled whenever the current set cannot advance automatically.

#### Scenario: Playback button exposes its action
- **WHEN** playback is active or paused
- **THEN** the visible button is named Pause slideshow or Resume slideshow respectively and can be activated with Enter or Space

#### Scenario: Unavailable playback control
- **WHEN** the set is empty, has one photograph, has a listing error, or every photograph has failed
- **THEN** the visible playback button is disabled

#### Scenario: Pending rating retains control
- **WHEN** a rating request is pending and the previous multi-photograph set remains visible
- **THEN** the playback button remains enabled

### Requirement: Playback status communicates playback state

Auxiliary text SHALL show the configured interval while playing and state that playback is paused until Resume. A single-photograph set SHALL retain its no-automatic-advance message. While a rating request is pending during pause, the text SHALL communicate both the update and paused state.

#### Scenario: Playback status follows the timer state
- **WHEN** playback is active or paused
- **THEN** auxiliary text states the automatic interval or that playback is paused until Resume

#### Scenario: Paused status remains visible without an available set
- **WHEN** a listing error or empty result leaves no selected photograph while playback is paused
- **THEN** auxiliary text continues to identify the paused state, the playback button remains disabled, and no Resume availability is implied

#### Scenario: Pending rating keeps paused state visible
- **WHEN** a rating request is pending while playback is paused
- **THEN** the auxiliary text communicates that the set is updating and playback remains paused until Resume

## MODIFIED Requirements

### Requirement: The next photographs are decoded before they are shown

The system SHALL prepare a configurable number of upcoming photographs ahead of the one on screen, and preparing one SHALL include decoding it, not only fetching it.

Decoding ahead is what the requirement is for: a photograph that has been fetched but not decoded still costs its decode at the moment it is swapped in, which is the moment this exists to keep smooth.

A photograph that fails to fetch or decode SHALL NOT stop playback while it is active. If the current photograph fails while playback is paused, the selected index SHALL remain unchanged and a visible unavailable message SHALL be shown without repeatedly requesting the failed resource. After playback resumes, the viewer SHALL wait one complete configured interval before advancing. If every photograph in the set fails, the existing unavailable state and explicit Retry SHALL remain available.

Image load and error events from an earlier rendered image instance SHALL NOT change the current photograph state, even if the same photograph identity has since become current again.

#### Scenario: The next photograph is ready before its turn
- **WHEN** a photograph is on screen and the interval has not elapsed
- **THEN** the photographs due after it have already been fetched and decoded

#### Scenario: Preparing further ahead
- **WHEN** the number of photographs to prepare ahead is raised
- **THEN** that many upcoming photographs are fetched and decoded ahead of the one on screen

#### Scenario: One photograph fails
- **WHEN** an upcoming photograph cannot be fetched or decoded while playback is active
- **THEN** the slideshow continues to another available photograph

#### Scenario: One photograph fails while paused
- **WHEN** the current photograph cannot be fetched while playback is paused
- **THEN** the index remains unchanged, an unavailable message is shown, and the failed resource is not requested repeatedly

#### Scenario: A single-photo set fails while pause is retained
- **WHEN** a paused viewer receives a one-photograph set after a rating change and that photograph fails
- **THEN** the existing unavailable state is shown, playback remains paused, and Retry is available without repeated image requests

#### Scenario: An old image event arrives after its photograph returns
- **WHEN** a load or error event arrives from an earlier image element after the same photograph identity becomes current again
- **THEN** the event does not alter the current image state or advance the index

#### Scenario: Every photograph is unavailable
- **WHEN** every photograph in a set fails to display
- **THEN** the timer and lookahead stop, no further advances or repeated preloads occur, and the unavailable state offers explicit Retry
