# Design: guided canvas and wardrobe coverage

## Scope and compatibility

This records the implemented resource-v1 behavior and completed live acceptance for the observed UI and generation paths, including their limits. It does not change legacy session composition or the completed `simplify-resource-session-workflow` artifacts.

The closed guided request in [`simplify-resource-session-workflow/design.md`](../simplify-resource-session-workflow/design.md#7-atomic-creation-and-existing-transport), line 96, enumerates the accepted body fields. This change explicitly supersedes that exact-field list by allowing the optional `width` and `height` pair. Those are the only additional keys. The existing request-ID replay rule remains: the validated normalized body excluding `request_id` defines identity; reusing the ID with different content returns `409 idempotency_conflict`. Supplied dimensions participate in that identity. When both dimensions are omitted, normalization omits them from the digest input to preserve the established body identity for retries from clients that predate the override.

## Per-take coverage

`wardrobe_coverage` is optional string data on a stable resource-v1 take. It is user-authored, capped at 2,000 characters, and preserved through plan normalization and take reordering. The application does not infer body areas or derive this field from wardrobe text. Automatic preparation treats it as locked user input rather than a writer-requested field.

For text-to-image and guided graphs, non-blank coverage is composed once after the resolved effective wardrobe and before take clauses. Reference-edit graphs continue to receive the bare instruction and omit coverage with the other session context. Missing and blank coverage do not add an effective-state key or change the prompt.

Plan saves use the existing expected-revision CAS. Coverage changes affect preparation for that take; automatic downstream work is invalidated when its dependencies require it. Unaffected verified work may copy forward. Generated snapshots and linked shots remain immutable history. The text describes intent only; it does not guarantee rendered pixels or exact visible anatomy.

## Guided canvas override

Guided creation accepts `width` and `height` together or omits both. Each supplied value must be a strict JSON integer, at least 8, and divisible by 8. Missing or half-specified values fail before writes. Omission inherits the character's current effective session dimensions. Supplied values override those dimensions only in the new session settings; the character record is unchanged.

The UI keeps the override off by default. Enabling it starts the custom size inputs at 768×1360. This is a UI starting value, not a server default or a promise that every workflow can render it. ComfyUI graph requirements and available GPU memory determine practical limits; the application does not advertise an independent upper bound.

## Acceptance boundary

Automated test and build gates are reported passed by the main agent. UI session 425 inherited 832×1216 with the override off (draft, ungenerated); session 426 used 768×1360 and completed generation 5/5. Clothing progression was visible, but feet were not reliably complete. Real ComfyUI guide session 427 rendered once at its workflow's 928×1664 resolution with coverage once; its anchor/reference were set through the API because the UI lacks a cross-session selector. No GPU ceiling was established. These observations do not make prompt text a guarantee of visual coverage.
