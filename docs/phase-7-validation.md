# Phase 7 acceptance evidence

Date: 2026-10-09. Implementation is experimental in the isolated
`C:/Users/operator/repos/ember-phase-seven` worktree and is awaiting architecture review.
The primary checkout and production memory data were not migrated. Phase 8 has not
started. [Root ADR 0013](adr/0013-phase-seven-native-consolidation-and-profile.md)
records the architecture; the [runbook](phase-7-runbook.md) describes review and
eventual deployment.

## Acceptance matrix

| Criterion | Evidence | Result and limit |
|---|---|---|
| P7-AC-01 | `memory-engine/tests/test_phase_seven.py::test_native_acceptance_support_replay_and_restart`; `test_unsupported_native_proposals_never_become_profile`; `test_cli_policy_acceptance_profile_inspection_and_owner_diagnostics`; `ember-agent/tests/test_phase_seven_real_model.py::test_real_qwen_native_acceptance_and_query_independent_profile` | Policy-owned, idempotent native decisions and original-speaker/evidence checks pass. Imported assertions remain operator-reviewed. Four synthetic Qwen candidates were accepted by the native policy. |
| P7-AC-02 | `test_native_acceptance_support_replay_and_restart`; `test_unsupported_native_proposals_never_become_profile`; `test_manual_rejection_resolves_conflict_and_survives_new_job` | Decisions preserve version/reasons/evidence/lineage and remain inspectable; unsupported cases do not enter the profile. |
| P7-AC-03 | `test_literal_owned_object_repeated_support_and_no_index_rebuild`; `test_consolidation_redirect_and_interrupted_transaction_preserve_identities`; `test_reviewed_import_profile_independent_characters_restart_and_support` | Exact same-scope repeats append support without duplicate active beliefs or an unnecessary vector rebuild. Confirmed merge preserves IDs/redirects and rolls back atomically on interruption. |
| P7-AC-04 | `test_explicit_correction_and_conflict_history`; `test_temporal_observed_filters_and_unknown_validity` | Explicit correction and unresolved conflict preserve history; observed, valid and recorded axes remain distinct, and unknown validity is not guessed. |
| P7-AC-05 | `test_inactive_beliefs_cannot_resurrect_under_new_job`; `test_manual_rejection_resolves_conflict_and_survives_new_job`; `test_forgetting_blocks_reseed_and_does_not_mutate_other_character`; `test_import_grant_expiry_invalidates_profile_before_limits` | Suppression, rejection, forgetting, expiry and character isolation survive new jobs/seeds and profile refresh. |
| P7-AC-06 | `test_native_support_of_seeded_fact_retains_independent_origins`; `test_manual_imported_supersession_retains_origin_and_forget_dependencies`; `test_reviewed_import_profile_independent_characters_restart_and_support` | Native and imported origins remain independently revocable through support/supersession; two characters and restart are covered. |
| P7-AC-07 | `test_consolidation_redirect_and_interrupted_transaction_preserve_identities`; `test_schema_four_supersessions_are_backfilled_as_typed_relationships`; `test_related_foreign_scope_deadline_and_row_limit` | Typed evidence-backed transitions/redirects and bounded related reads are covered; topic/date similarity does not create an incident edge. |
| P7-AC-08 | `test_temporal_observed_filters_and_unknown_validity`; `test_explicit_correction_and_conflict_history` | Indexed temporal filters distinguish observed/valid/recorded time and preserve unknown precision. |
| P7-AC-09 | ABI `test_interfaces.py`; `test_related_foreign_scope_deadline_and_row_limit`; `test_profile_exclusions_manual_pin_ownership_and_batch_validation`; portability `test_projected_grant_expiry_and_revoke_are_checked_before_top_k` | Optional related/temporal capabilities are typed and negotiated; bounds, expiry, suppression and source scope are exercised with in-process clients. No backend dependency enters ember-agent. |
| P7-AC-10 | `ember-agent/tests/test_phase_seven.py::test_unrelated_prompt_includes_profile_and_six_distinct_recall_slots`; `ember-agent/tests/test_livekit_runtime.py::test_actual_room_includes_query_independent_profile_after_restart`; `test_reviewed_import_profile_independent_characters_restart_and_support`; paired Qwen report | Preferred name, pronouns, timezone and style are inserted for unrelated NAS/network/audio questions after restart. The paired Qwen run found four pins for all three queries; this verifies context delivery, not a statistically established answer-quality gain. |
| P7-AC-11 | `test_unsupported_native_proposals_never_become_profile`; `test_profile_exclusions_manual_pin_ownership_and_batch_validation`; `test_import_grant_expiry_invalidates_profile_before_limits`; `test_manual_rejection_resolves_conflict_and_survives_new_job` | Pending/unsupported, unbound, excluded, expired and conflicting facts are omitted with reasons; import recency or confidence cannot override policy. |
| P7-AC-12 | `test_unrelated_prompt_includes_profile_and_six_distinct_recall_slots`; `test_profile_capability_without_delivery_validation_degrades_honestly` | Profile uses the existing retrieve-context call and PinnedMemory contract. The measured turn had one context call, no expansion and no per-memory `get`; unsupported validation capability omits profile pins honestly. |
| P7-AC-13 | `test_profile_injection_stays_data_and_manual_pins_keep_priority`; `test_unrelated_prompt_includes_profile_and_six_distinct_recall_slots` | Manual pins win within the shared serialized 384-byte guard, profile and six ordinary slots stay distinct, and the 7000-token ceiling is enforced. Untrusted values remain user-role data. |
| P7-AC-14 | `test_source_revision_edit_omits_old_profile_and_invalidates_context`; `test_profile_policy_change_stops_visible_delivery_not_metadata_progress`; `test_import_grant_expiry_invalidates_profile_before_limits`; `test_projected_grant_expiry_and_revoke_are_checked_before_top_k` | Source edits, policy changes, expiry and revocation invalidate before visible delivery; metadata-only progress does not cause repeated validation. |
| P7-AC-15 | portability `test_profile_choices_and_native_policy_survive_bundle`; `test_profile_exclusions_manual_pin_ownership_and_batch_validation`; `test_inactive_beliefs_cannot_resurrect_under_new_job` | Per-character bind/enable/field controls, manual pins and suppression survive restart and supported full/selective bundle restore. Refresh cannot approve or seed candidates. |
| P7-AC-16 | `ember-agent/tests/test_livekit_runtime.py::test_actual_room_includes_query_independent_profile_after_restart`; `ember-agent/tests/test_phase_seven.py::test_profile_injection_stays_data_and_manual_pins_keep_priority`; CLI `test_cli_policy_acceptance_profile_inspection_and_owner_diagnostics` | Terminal and actual LiveKit SDK room use the shared core. CLI evidence is opt-in; values remain untrusted data. Profile selection is canonical and independent of search-index availability. |
| P7-AC-17 | Sanitized reports [synthetic benchmark](reports/phase-7-memory-benchmark.json) and [paired Qwen run](reports/phase-7-real-qwen.json); `scripts/benchmark_phase_seven.py` | Profile selection/validation/format p95 was 1.03 ms. Warm recall p95 was 109.23 ms and grant-scoped recall p95 137.08 ms: both meet the 150 ms soft recall deadline, though both exceed the 50–100 ms warm-recall aim. Complete prepared/streamed-turn p95 was 150.33 ms (20 samples), reported separately as delivery cost; concurrent index-copy foreground samples peaked at 154.58 ms. 128-reference-equivalent batch validation p95 was 5.52 ms versus 18.64 ms serial, with the same checks. Qwen added 57 prompt tokens per paired query; TTFT/prefill are recorded separately. One Windows CPU/SSD synthetic run is not a million-memory, multi-platform or answer-quality certification. |

## Quality and compatibility checks

The locked environment sync completed in the isolated worktree. The root quality gate
passes Ruff format check, Ruff lint and strict Pyright, followed by **492 passed and 7
skipped** in the full 499-test suite. Contract schema export check passes with
`scripts/export_contract_schemas.py --check`. The suite includes existing Phase 0–6
coverage; seven integration/model tests remain skipped by their explicit runtime
requirements. Passing this Phase 7 run does not close the older Phase 5 million-record,
multi-platform, or broader capacity gates.

The benchmark corpus contains synthetic statements only. The paired Qwen report stores
no generated answer text or private archive content. Real LiveKit transport used fake
generation; real Qwen extraction/prompt measurement ran separately with no realtime
session reservation. These two checks must not be described as one combined live room
using the real Qwen model.

## Review regression evidence

- `ember-agent/tests/test_character.py::test_session_archives_multiple_turns_without_rewriting_conversation`
  exercises two completed core turns in one context, then a completed and an interrupted
  delivery after reopening the store. Submitted timestamps differ; stored conversation
  bytes/hash/time remain unchanged. All seven messages retain the session reference and
  native speaker crosswalk. The interrupted delivery reproduced a cursor RevisionConflict
  before the fix; per-delivery cursor components now preserve it atomically.
- `memory-engine/tests/test_phase_seven_portability.py::test_certificate_uses_effective_grant_window`
  covers permanent part grants with temporary whole-record alternatives and the inverse,
  overlapping temporary grants, already-expired alternatives and the earliest effective
  boundary across independent sources. The pre-existing certificate stays valid when
  only an obsolete alternative expires and becomes invalid at the effective boundary.
- PLAN's benchmark and validation links are relative to the root PLAN file and now
  resolve to docs/reports and docs, respectively.
