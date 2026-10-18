# Proposal

## Why

Saving a reviewed look currently creates only the appearance/outfit version. Full Auto then requires a separately authored automation-profile revision, making users repeat setup after they have already reviewed the same look and clothing-removal order.

## What Changes

- Prepare editable structured automation facts alongside the principal look and review them in the same Looks editor. Photo extraction remains an explicit proposal action; manually authored looks retain an assistant-free path.
- Save the reviewed look version and its first immutable automation-profile revision together, returning exact identities and authoritative readiness. Reuse the existing publication and readiness rules rather than creating another kind of look.
- Reuse the confirmed garment/accessory order and the canonical progressive removal stages. Full Auto must include intermediate removal stages and end with every listed item absent; removable items must not remain in permanent appearance.
- Make the generated profile discoverable immediately. For a newly selected exact look version with one ready profile, show and select that exact revision automatically; multiple ready revisions retain explicit choice. Generation still requires the existing consent action.
- Keep manual-only saving usable when facts are incomplete or assistance is unavailable, with an explicit readiness explanation. Existing looks, historical sessions, portable imports/exports, and legacy callers are not silently migrated or certified.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `resource-store`: Structured profile proposals and reviewed atomic look/profile saving with exact identity, replay, and evidence preservation.
- `resource-workflow-ux`: One integrated look/profile review and save, visible missing facts, and exact unambiguous profile selection during new Full Auto creation.
- `resource-full-auto`: Explicit acceptance of canonical stepwise removal through the final inventory-empty stage for profiles created with a look.

## Impact

Backend look request models, saved-look services, photo extraction/staging integration, additive save/evidence persistence, and their tests; the Looks editor, reusable profile controls, Resources selector, API transport, and frontend tests. Update README and the look/session guides when implementing. Reuse the existing stage builder, coverage compiler, authoring coordinator, and serial runner. No new dependency, live generation, historical backfill, portable schema change, commit, or push is included.
