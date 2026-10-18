# Automation profiles Group 5 acceptance

This records the independent acceptance evidence for tasks 5.1–5.3 of
[create-automation-profiles-with-looks](../openspec/changes/create-automation-profiles-with-looks/proposal.md).
The three delta specifications remain the acceptance source:
[resource-store](../openspec/changes/create-automation-profiles-with-looks/specs/resource-store/spec.md),
[resource-workflow-ux](../openspec/changes/create-automation-profiles-with-looks/specs/resource-workflow-ux/spec.md),
and [resource-full-auto](../openspec/changes/create-automation-profiles-with-looks/specs/resource-full-auto/spec.md).

The connected path passed for manual and reviewed-photo saves. Each saved
version received its own ready profile revision 1 with the exact look key,
version, and content digest. Full Auto selected that exact revision, retained
explicit authorization, materialized the complete removal timeline, prepared
every take, and submitted the same positive prompt text that was stored with
the prepared take and shot.

The independent connected test uses five invented removable items, including
layered clothing and a progressive accessory. The canonical timeline contains
seven stages: all five items worn, one ordered removal at a time, the final
accessory's explicit aside state, then an empty inventory. Seven takes cover
the exact stage count; thirteen takes span the longer interval. The final
permanent appearance contains no removable accessory clause. Unknown coverage
and a baseline that conflicts with the empty inventory both refuse the
combined save without partial writes.

## Scenario evidence

Each delta scenario is listed separately. Test names below were checked in the
source tree; the complete Python and frontend suites were run for the final
group state.

### resource-store

| # | Scenario | Evidence |
|---:|---|---|
| 1 | Photo extraction supplies an automation candidate | tests/test_photo_extraction.py — test_opt_in_photo_automation_is_source_bound_transient_and_preserves_unknowns |
| 2 | A manually authored look requests assistance | tests/test_look_automation_proposals.py — test_manual_proposal_is_exact_transient_and_leaves_unknowns_unknown |
| 3 | Provider cannot establish a fact | tests/test_look_automation_proposals.py — test_malformed_or_self_accepting_manual_candidate_is_rejected; tests/test_photo_extraction.py — test_photo_companion_provider_failure_falls_back_to_ordinary_proposal |
| 4 | Complete look and profile are reviewed together | tests/test_combined_look_save.py — test_combined_create_maps_final_identity_and_builds_server_user_provenance; tests/test_g5_connected_look_full_auto_acceptance.py — test_integrated_look_profile_reaches_fake_comfy_with_exact_removal_arc |
| 5 | Inventory contains a removable accessory | tests/test_g5_connected_look_full_auto_acceptance.py — test_integrated_look_profile_reaches_fake_comfy_with_exact_removal_arc |
| 6 | Complete removal is confirmed | tests/test_g5_connected_look_full_auto_acceptance.py — test_integrated_look_profile_reaches_fake_comfy_with_exact_removal_arc; test_incomplete_integrated_review_is_a_zero_write_refusal |
| 7 | Facts remain incomplete | tests/test_g5_connected_look_full_auto_acceptance.py — test_incomplete_integrated_review_is_a_zero_write_refusal; frontend/src/views/Looks.combinedSave.test.jsx — shows unknown blockers and allows look-only saving only through its explicit action |
| 8 | New garments receive persistent keys | tests/test_combined_look_save.py — test_combined_create_maps_final_identity_and_builds_server_user_provenance |
| 9 | Profile publication fails after look allocation | tests/test_combined_look_save.py — test_every_principal_write_failure_rolls_back_all_persisted_rows |
| 10 | A later look version is saved | tests/test_combined_look_save.py — test_new_version_gets_revision_one_and_does_not_mutate_prior_profile_or_session_snapshot |
| 11 | The browser loses the successful response | tests/test_combined_look_save.py — test_identical_retry_after_discarded_response_and_real_concurrent_requests_converge; frontend/src/views/Looks.combinedSave.test.jsx — blocks double-click writes and replays the exact frozen body and request ID after response loss |
| 12 | Two tabs submit one save identity | tests/test_combined_look_save.py — test_identical_retry_after_discarded_response_and_real_concurrent_requests_converge |
| 13 | A request identity is reused after an edit | tests/test_combined_look_save.py — test_changed_content_or_garment_order_with_same_request_id_conflicts |
| 14 | A saved photo has already been cleaned | tests/test_combined_look_save.py — test_photo_combined_save_cleans_only_after_outer_commit_and_replays_without_bytes |
| 15 | Clothing order changes after review | tests/test_combined_look_save.py — test_stale_suggestion_digest_and_stale_final_draft_are_zero_write_refusals; frontend/src/views/Looks.combinedSave.test.jsx — keeps photo uncertainty and order confirmation separate while mapping reordered suggestion rows by identity |
| 16 | An appearance edit shifts Unicode spans | tests/test_saved_look_automation_profiles.py — test_appearance_spans_use_unicode_scalar_indices; frontend/src/components/AutomationProfileReview.test.jsx — keeps initial facts unknown and maps selected Unicode spans to stable garment row identities |
| 17 | A newer photo proposal supersedes the reviewed one | tests/test_photo_extraction.py — test_photo_automation_proposal_is_fenced_by_a_newer_extraction_attempt |
| 18 | A suggestion is corrected before saving | tests/test_combined_look_save.py — test_manual_suggestion_identity_survives_renewed_final_review |
| 19 | An unreviewed provider labels output accepted | tests/test_look_automation_proposals.py — test_malformed_or_self_accepting_manual_candidate_is_rejected; tests/test_combined_look_save.py — test_combined_create_maps_final_identity_and_builds_server_user_provenance |
| 20 | A photo-derived look is exported | tests/test_combined_look_save.py — test_archive_feature_gate_old_look_only_shape_and_portable_v1_are_preserved; tests/test_portable_looks_independent.py — test_export_route_round_trips_every_schema_legal_path_character |
| 21 | Assistance is not configured | Browser walkthrough: manual-no-assistant mode; tests/test_g5_connected_look_full_auto_acceptance.py — test_integrated_look_profile_reaches_fake_comfy_with_exact_removal_arc |
| 22 | An old look or portable import has no profile | tests/test_task31_acceptance.py — test_manual_saved_look_creation_uses_exact_snapshot_without_profile_or_run; tests/test_combined_look_save.py — test_archive_feature_gate_old_look_only_shape_and_portable_v1_are_preserved |
| 23 | Resource planning is disabled | tests/test_look_automation_proposals.py — test_disabled_feature_gate_refuses_before_provider_dispatch; tests/test_combined_look_save.py — test_feature_gate_refuses_even_a_combined_receipt_replay_without_new_writes |

### resource-workflow-ux

| # | Scenario | Evidence |
|---:|---|---|
| 24 | A reviewed photo look is saved for Full Auto | Browser walkthrough and API-wire audit; tests/test_g5_connected_look_full_auto_acceptance.py — test_integrated_look_profile_reaches_fake_comfy_with_exact_removal_arc |
| 25 | A manually entered look is saved with automation facts | tests/test_g5_connected_look_full_auto_acceptance.py — test_integrated_look_profile_reaches_fake_comfy_with_exact_removal_arc |
| 26 | An imported outfit becomes a named look | tests/test_looks_g3_contracts.py — test_frontend_profiles_execute_against_real_combined_save_routes; frontend/src/views/Looks.task9_11.test.jsx — converts a committed legacy outfit into a named look only after an explicit editor action |
| 27 | A profile proposal fails | Browser walkthrough's first photo-companion failure; tests/test_photo_extraction.py — test_photo_companion_provider_failure_falls_back_to_ordinary_proposal |
| 28 | Combined save is rejected | tests/test_combined_look_save.py — test_combined_companion_pair_rejection_never_falls_back_to_look_only; frontend/src/views/Looks.combinedSave.test.jsx — releases only a stable known pre-write refusal while preserving the editable form |
| 29 | A newly saved look has one ready profile | Browser walkthrough; frontend/src/components/AutomationProfileSelector.test.jsx — selects and verifies the sole ready revision when other revisions need review |
| 30 | Multiple ready revisions exist | frontend/src/components/AutomationProfileSelector.test.jsx — keeps zero-ready and multiple-ready revisions explicit with diagnostics |
| 31 | No ready revision exists | Browser walkthrough's incomplete manual save; frontend/src/components/AutomationProfileSelector.test.jsx — keeps zero-ready and multiple-ready revisions explicit with diagnostics |
| 32 | An earlier discovery response arrives | frontend/src/components/AutomationProfileSelector.test.jsx — fences stale discovery responses and never selects the newest revision implicitly; fences A-to-B-to-A discovery responses by the newest request identity |
| 33 | The selected profile changes before authorization | frontend/src/views/Resources.test.jsx — invalidates consent while a newly selected exact profile is still loading |
| 34 | Ordered clothing includes a final moved-aside state | Browser walkthrough; tests/test_g4_full_auto_progression_acceptance.py — test_exact_and_longer_full_auto_intervals_materialize_every_canonical_stage |
| 35 | A look and ready profile are saved | tests/test_g5_connected_look_full_auto_acceptance.py — test_integrated_look_profile_reaches_fake_comfy_with_exact_removal_arc; browser walkthrough shows preparation and generation begin only after navigating to Resources and authorizing |

### resource-full-auto

| # | Scenario | Evidence |
|---:|---|---|
| 36 | Look save produces a profile for an ordered outfit | tests/test_g5_connected_look_full_auto_acceptance.py — test_integrated_look_profile_reaches_fake_comfy_with_exact_removal_arc |
| 37 | The last remaining item has an aside state | tests/test_g5_connected_look_full_auto_acceptance.py — test_integrated_look_profile_reaches_fake_comfy_with_exact_removal_arc |
| 38 | An accessory was described in appearance | tests/test_g5_connected_look_full_auto_acceptance.py — test_integrated_look_profile_reaches_fake_comfy_with_exact_removal_arc |
| 39 | The baseline contradicts the empty final inventory | tests/test_g5_connected_look_full_auto_acceptance.py — test_incomplete_integrated_review_is_a_zero_write_refusal |
| 40 | Take count equals the stage count | tests/test_g5_connected_look_full_auto_acceptance.py — test_integrated_look_profile_reaches_fake_comfy_with_exact_removal_arc; tests/test_g4_full_auto_progression_acceptance.py — test_exact_and_longer_full_auto_intervals_materialize_every_canonical_stage |
| 41 | Too few takes are requested | tests/test_g5_connected_look_full_auto_acceptance.py — test_integrated_look_profile_reaches_fake_comfy_with_exact_removal_arc; tests/test_g4_full_auto_progression_acceptance.py — test_insufficient_full_auto_preflight_returns_minimum_without_allocating_work |
| 42 | A longer session spans the same inventory | tests/test_g5_connected_look_full_auto_acceptance.py — test_integrated_look_profile_reaches_fake_comfy_with_exact_removal_arc |
| 43 | A profile is saved for an appearance-only look | tests/test_g4_full_auto_progression_acceptance.py — test_appearance_only_full_auto_keeps_one_constant_wardrobe_stage |
| 44 | Combined save reaches an authorized generated session | Browser walkthrough and API-wire audit; tests/test_g5_connected_look_full_auto_acceptance.py — test_integrated_look_profile_reaches_fake_comfy_with_exact_removal_arc |
| 45 | A rendered output disagrees with its final wardrobe | Reporting contract only: no visual-compliance claim or automatic reroll was made; the conditional real-pixel mismatch was not exercised with synthetic outputs. |

## Verification and limits

- Python: project interpreter full suite, 3,698 passed, 2 skipped, 18 warnings.
- An earlier full-suite attempt hit a lease-snapshot failure that reproduced on
  unchanged HEAD; the final complete suite passed.
- Frontend: 724 tests passed across 44 files; build passed with the existing
  large-chunk advisory.
- Strict OpenSpec validation passed. Privacy and character/whitespace guards
  passed (46 checks), as did py_compile for the demo, inspection of new files,
  and git diff --check.
- The connected Group 5 module has four pytest cases: manual and photo sources,
  plus unknown-coverage and conflicting-baseline refusals.
- The corrected API-wire audit exited successfully and verified one persisted
  authorization receipt, 13 prepared takes, 13 submission receipts, 13
  completed shots, target indices 0–12, 13 captured Comfy request bodies, and
  exact prepared/shot/serialized-request positive-prompt byte equality for
  13/13 takes.

The UI and internal services ran against isolated temporary SQLite/config
state. Assistant and ComfyUI boundaries were synthetic; no GPU, remote
provider, production database, or real configuration was used. The wire audit
compares the Comfy positive field from captured HTTPX request content with
UTF-8 prompt bytes persisted by the app. It is not a packet capture or a test
of a real ComfyUI service. A deliberate mismatch injection into the audit was
not run because the host execution policy blocked that attempt. FakeComfy
produced synthetic images, so successful protocol completion and matching
prompt bytes do not establish visual garment fidelity.
