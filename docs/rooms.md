# Rooms

The `#/rooms` screen imports legacy room seeds from a source directory and an
authored translation map on the backend machine. It is separate from Resources,
which manages private SQLite resource revisions and readiness. Rooms is not an
inventory editor. Runtime room selection and room preflight remain in the
existing model/session flows.

## Explicit import

Enter **Source directory** and **Translation map**, then select **Import rooms**.
The form posts only trimmed `source_dir` and `map_path` to `/api/rooms/import`;
it does not upload files, choose a data directory, run preflight, or generate
photographs. Destinations and registration are determined by the existing backend.
Both fields are required and stay in the form after success or failure. While a
request is pending, fields are locked and duplicate submits are blocked. Leaving
the screen does not cancel a request already sent. Returning while that request is still pending reattaches to the existing promise
and restores its paths, without sending another import. Unmounted instances do
not update state when their requests settle.

## Read the result

The submitted paths identify the result even after editing the next import's
paths. Accepted, Refused, Written, Created, Updated, Unchanged, Orphaned and
Skipped (empty theme) retain their existing order and values. These are different
measures; accepted entries are not necessarily written room rows. Missing counts
still display as zero. Refusal reasons and identifiers are displayed unchanged.
Outlying weights are diagnostic and remain stored unchanged; orphaned verdicts
are reported and retained, separately from orphaned seed rows. **Full report and
destination details** exposes the complete response, including per-destination
identities and placeholder diagnostics, without altering its content.

No report exists before an explicit import. A report containing no written rows
shows that state without inventing successful changes. On narrow screens the
form and explanation stack, counts use two columns, and tables scroll locally
in named regions. Native form controls, associated help, visible focus and a
focusable result summary support keyboard use. A result never takes focus out
of an active modal or inert screen.

## Recovery limits

A missing-translation refusal returns the entire worklist of source identifiers,
fields and original strings. Complete the authored map, then explicitly import
again. This refusal happens before seed or registry writes.

Other errors, including some `422` responses, may happen after seed files were
written. Network or server failures can also leave the result uncertain. Inspect
the destinations and `room_libraries` before retrying. The UI does not claim
rollback, repeat an operation automatically, or provide idempotent replay.
An explicit retry is a new import with the current paths. The existing importer
reconciles source identities and preserves measurements according to its rules;
this screen does not change those rules or add deletion/approval operations.

Form drafts and reports remain local to the mounted screen except for an active
request, retained in memory across SPA route remounts in the same tab until it
settles. This lock does not coordinate separate browser tabs. They are
not saved to browser storage. Completed reports and idle drafts do not survive
route unmounting or page reload. Reloading the page also loses the in-flight lock;
check the destination before submitting again. The import
report is returned by the backend; this redesign adds no persistent report history.
