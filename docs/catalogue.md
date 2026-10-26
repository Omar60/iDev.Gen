# Catalogue

Catalogue (`#/catalogue`) manages three existing stores: prompt components, base
readings for judging, and the wardrobe seed inventory. Section buttons move focus
within the page; they do not save, import, retire or delete anything.

## Prompt components

Slot, manner and Show retired components retain their existing meaning. Every
manner in `ALL_MANNERS`, including `pov`, remains selectable. Search is local,
case-insensitive and limited to the current selection; it matches concept keys,
IDs, wording, judge labels, families, faces and ordered camera families. Records
retain the order returned by the API. An empty filtered result does not mean the
stored catalogue is empty.

Cards keep identity, family/faces/cameras, prompt wording and judge label visible.
Evidence reports the server's state, arrived/judged counts and contradictions;
unmeasured records are not presented as verified. Retire and Restore preserve the
record and evidence. Delete requires explicit confirmation and remains subject to
the backend's refusal when measured evidence exists.

The editor keeps its existing defaults, editable fields and explicit Save.
Camera families remain ordered comma-separated text converted into a list on the
wire; hiding the act-only field does not clear its value. The server still
validates identity and the difference between prompt wording and judge label.
Errors keep the draft, receive focus and do not retry automatically. Escape and
Cancel dismiss the editor without saving; while Save is pending, edits and
cancellation are disabled. Closing restores focus to the opener when available.

## Base readings and wardrobe

Base readings belong to the selected slot and manner, without a session scope.
Adding trims the key and label as before. Deleting requires confirmation; the
server refuses readings referenced by stored answers. Changing filters cannot
replace the current readings with an older scope's response. Failed additions
keep their input values.

Each seed import is explicit and separate. Components and readings retain their
added/skipped reports. Existing keys and labels/wording are not overwritten.
Wardrobe remains a count-and-import panel, not a garment/outfit editor. Counts
stay unknown until loaded; a failed refresh retains the last loaded inventory.
Refresh controls only read data and never import as a fallback.

## Contracts and shared consumers

| Operation | Existing endpoint |
| --- | --- |
| Load all component rows | `GET /api/components?all=1` |
| Create / edit | `POST /api/components`, `PATCH /api/components/{id}` |
| Retire / restore / delete | `POST /api/components/{id}/retire`, `POST /api/components/{id}/restore`, `DELETE /api/components/{id}` |
| Import components | `POST /api/components/import` |
| Load base readings | `GET /api/readings?slot=...&manner=...` |
| Add / delete / import readings | `POST /api/readings`, `DELETE /api/readings/{id}`, `POST /api/readings/import` |
| Load / import wardrobe | `GET /api/wardrobe`, `POST /api/wardrobe/import` |

The view now uses the existing `api.del` helper for both DELETE routes; the former
`api.delete` calls did not exist on the client. No backend or API schema changed.
In-flight duplicate actions are blocked locally; this is not an exactly-once
server guarantee, and uncertain writes are never replayed automatically.

Successful reads continue to publish through `setCatalogue` and `setWardrobe`,
including refreshes after an explicit import completes while leaving the page.
Their implementations and consumers in composition, session pickers and judging
remain unchanged. Older concurrent responses from this Catalogue instance cannot
replace a newer refresh. App's separate startup reads are unchanged.

Existing limitation: concept key, slot and manner remain editable, and evidence
is grouped by those identities. The redesign does not migrate historical
references when those fields are changed. Catalogue has no dedicated phase-3
prototype; its layout follows the approved studio system and existing operations.
