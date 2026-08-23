# bulk-reshoot Specification

## Purpose
Refusing a session's weak frames in one action, on exactly the terms the app
already refuses them one at a time, so a long shoot does not cost one dialog per
photograph.

## Requirements

### Requirement: Re-queue every finished shot under a threshold

The system SHALL re-queue, for one session, each shot that has finished and
whose rating is below a caller-supplied threshold. A shot that has not finished
SHALL NOT be a candidate: there is no photograph to refuse, and a pending shot is
already queued.

A rejected shot below the threshold SHALL be re-queued like any other. Rejecting
a frame and reshooting it are the same judgement, and leaving rejects behind
would make the action miss exactly the frames the user already refused.

Re-queuing one shot SHALL have the same effect as refusing it on its own: its
image is deleted from the session folder, and its status, filename, prompt id,
error, seed, rejected flag and finish time are cleared. The seed SHALL be cleared
rather than kept — the same prompt on the same noise returns the same photograph,
which is the outcome the action exists to avoid.

#### Scenario: The weak frames go back in the queue
- **WHEN** a session has finished shots rated 1, 3 and 5 and a reshoot is requested below 4
- **THEN** the shots rated 1 and 3 are pending again with their images gone, and the shot rated 5 is untouched

#### Scenario: An unrated shot is below every threshold
- **WHEN** a finished shot has never been rated and a reshoot is requested below 1
- **THEN** that shot is re-queued

#### Scenario: A rejected frame is not spared
- **WHEN** a finished shot below the threshold is marked rejected
- **THEN** it is re-queued and its rejected flag is cleared

#### Scenario: A shot that never finished is not a candidate
- **WHEN** a session holds pending and failed shots alongside finished ones
- **THEN** only the finished shots below the threshold are re-queued, and the others keep the status they had

#### Scenario: The seed does not survive
- **WHEN** a shot with a recorded seed is re-queued
- **THEN** its seed is cleared

### Requirement: The bulk action refuses what the single action refuses

The system SHALL leave untouched, and SHALL NOT fail the whole request over, any
shot the single-shot reshoot would refuse:

- a shot that is currently generating;
- a shot that is one of the session's reference anchors, because the takes that
  edit it would have nothing to edit.

The response SHALL report how many shots were re-queued and how many were left
alone, so the screen can say what happened instead of the user counting cards.

#### Scenario: A generating shot is stepped over
- **WHEN** one shot below the threshold is running and others are finished
- **THEN** the finished ones are re-queued, the running one is untouched, and the response counts it as skipped

#### Scenario: The session's reference is protected
- **WHEN** a shot below the threshold is one of the session's anchors
- **THEN** it is not re-queued, its image is still on disk, and the response counts it as skipped

#### Scenario: Nothing qualifies
- **WHEN** no shot in the session is a candidate for the threshold
- **THEN** the request fails with a client error, and no image is deleted and no row changed

#### Scenario: The session does not exist
- **WHEN** a bulk reshoot is requested for an unknown session
- **THEN** the request fails with a not-found error

### Requirement: A finished session reopens

When a bulk reshoot re-queues anything in a session whose status is done,
cancelled or failed, the session SHALL return to draft. A finished session with
something queued in it is not finished, and the session status is what the Run
button reads.

#### Scenario: A done session goes back to draft
- **WHEN** a session reading done has shots re-queued
- **THEN** the session reads draft

#### Scenario: A running session's status is not rewritten
- **WHEN** a session that is not done, cancelled or failed has shots re-queued
- **THEN** its status is left as it was

### Requirement: The session view offers the bulk reshoot

The session view SHALL offer a control that re-queues the shots below the active
threshold, stating how many it would take. Because the action deletes
photographs, it SHALL ask for confirmation naming that count before sending
anything, and SHALL be unavailable when the threshold selects nothing.

#### Scenario: The count is named before anything is deleted
- **WHEN** the user presses the control with four shots below the threshold
- **THEN** a confirmation naming four shots is shown, and no request is sent until it is accepted

#### Scenario: Nothing to reshoot
- **WHEN** no finished shot is below the active threshold
- **THEN** the control is disabled and no request is made
