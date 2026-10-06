# Task 10.6 Compatibility Verification

This matrix records the existing regression coverage for the Task 10.6 contracts and the added raw-expert, multi-resource API regression. No production behavior changed.

| Contract | Regression evidence |
| --- | --- |
| Legacy and guided session creation | `tests/test_legacy_session_baseline.py::test_legacy_session_creation_does_not_mutate_cell_evidence`; `tests/test_guided_session_creation.py::test_guided_creation_returns_closed_plan_and_persists_one_of_each`; rollback coverage also verifies that legacy creation remains available while resource-v1 writes are disabled. |
| Measured-catalogue gates | `tests/test_catalogue_api.py::test_empty_catalogue_refusals` and `tests/test_catalogue_api.py::test_the_imported_acts_carry_the_families_they_were_measured_on`; `tests/test_room_gates.py` exercises refusal, accepted composition, and zero-queue behavior; `tests/test_resource_session_acceptance.py::TestTwelvePortraitAcceptance::test_twelve_portrait_acceptance_flow` checks the longer acceptance flow. |
| Expert raw completion with multiple selected resources | `tests/test_preparation_authority.py::TestTask24AuthoringEvidenceContract::test_task_106_pre_authoring_expert_raw_complete_preserves_multiresource_snapshot_and_queue_flow` completes a pre-authoring plan through the API with two selected revisions. It checks caller-owned prompt, effective state, versions, and provenance; the server-derived sorted resource projection and digest; assistant-free completion; review and approval; one pending queued shot; idempotent retry; and unchanged linked snapshot/shot after a future-take edit and stale-revision retry. Existing projection and single-resource raw-evidence tests are `tests/test_preparation_authority.py::TestTask24AuthoringEvidenceContract::test_task_74_projection_sorts_resources_and_preserves_fields_and_lists` and `tests/test_preparation_authority.py::TestTask24AuthoringEvidenceContract::test_task_74_pre_authoring_expert_ready_requires_persisted_resource_evidence`. |
| Path-backed import compatibility and API/CLI parity | `tests/test_task10_1_independent.py::test_path_backed_resource_import_keeps_compatibility_behavior_when_disabled`; `tests/test_resource_service.py::test_app_and_cli_reports_are_equivalent`, `tests/test_resource_service.py::test_browser_scoped_attestation_rejected_by_path_commit`, and `tests/test_resource_service.py::test_legacy_preview_serialization_matches_baseline_keys_without_browser_fields`; `tests/test_importer.py::test_app_and_cli_reports_carry_the_same_counts_per_destination`. |
| Translation contracts | `tests/test_resource_translation.py::TestStrictPreparationContract::test_refuses_preparation_when_required_field_lacks_translation`, `tests/test_resource_translation.py::TestTranslationApiRoutes::test_preview_and_apply_endpoints_round_trip`, and `tests/test_resource_translation.py::TestSelectedTranslationMapWorkflow::test_selected_map_preview_and_apply_round_trip`; `tests/test_task10_4_manual_journey.py::test_offline_manual_journey_and_automatic_draft_stop_at_review` exercises the offline translation and preparation journey without an assistant. |
| Session listing and search | `tests/test_api.py::test_text_query_searches_across_models`, `tests/test_api.py::test_resource_session_projects_plan_constants_and_searches_current_values`, and `tests/test_api.py::test_legacy_session_list_search_and_wardrobe_patch_remain_compatible`. |
| Queued and generated snapshots | The new multi-resource test covers queue creation, a pending shot, immutable linked evidence, duplicate-submit idempotency, and stale-revision no-write behavior. `tests/test_runner.py::test_resource_take_runs_with_exact_final_prompt_and_no_double_prefix` and `tests/test_runner.py::test_resource_take_submission_never_re_executes_writer_and_ignores_subsequent_changes` cover execution from the persisted snapshot. |
| Operational rollback | `tests/test_backup_upgrade_rollback.py::test_rollback_preserves_modern_database_and_history`, `tests/test_backup_upgrade_rollback.py::test_rollback_disables_resource_v1_writes_with_http_503`, and `tests/test_backup_upgrade_rollback.py::test_rollback_keeps_reads_and_queue_actions_functional`. |
| Runner and reference behavior | `tests/test_runner.py::test_a_reference_take_edits_the_anchor_through_the_other_workflow` and `tests/test_runner.py::test_a_guide_take_keeps_the_character_model`, alongside the resource-runner snapshot tests above. |

The final focused compatibility run was:

```text
python -m pytest tests/test_preparation_authority.py tests/test_task10_1_independent.py tests/test_task10_4_manual_journey.py tests/test_task10_5_compatibility_journey.py tests/test_legacy_session_baseline.py tests/test_guided_session_creation.py tests/test_resource_translation.py tests/test_resource_import.py tests/test_resource_service.py tests/test_importer.py tests/test_api.py tests/test_runner.py tests/test_backup_upgrade_rollback.py tests/test_catalogue_api.py tests/test_room_gates.py tests/test_resource_coverage.py tests/test_catalogue_seed.py tests/test_resource_session_acceptance.py -q
```

Result: passed at 100% with exit code 0. Pytest emitted the `EnhanceIn.register` Pydantic shadowing warning from `backend/enhance.py`; it did not fail the run.

## Final verification

- `.venv/Scripts/python.exe -m pytest` — 2,762 passed, 1 skipped, 5 warnings.
- The skipped case is `tests/test_look_photo_staging_independent.py::test_preview_refuses_a_staged_path_replaced_with_external_symlink`; this environment could not create a filesystem symlink.
- The new regression, `tests/test_preparation_authority.py::TestTask24AuthoringEvidenceContract::test_task_106_pre_authoring_expert_raw_complete_preserves_multiresource_snapshot_and_queue_flow`, passed independently (1 passed).
- `tests/historical_rollback.py` — 2 passed. `tests/test_no_personal_data.py` and `tests/test_shoot_checks.py` — 46 passed.
- `npm --prefix frontend test` — 587 tests across 30 files passed.
- `npm --prefix frontend run build` — passed; the build reported a chunk-size advisory.
- OpenSpec strict validation and `git diff --check` passed.

Runner and reference behavior was verified with the repository's `FakeComfy` test harness. No live ComfyUI or GPU execution is claimed.
