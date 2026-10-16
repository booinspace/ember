# Phase 8 ordinary word recall correction

The user requested ordinary searches over individual words in every eligible
accepted memory, with relevant high-importance extracted values packed into the
configured context. This supersedes the ordinary six-row/768-byte restriction;
explicit deep investigation retains its orchestration, resource and prompt bounds.
The shared word ranker also applies when an investigation searches accepted memories.

## Behavior and ownership

The memory engine owns Unicode word matching, scope-before-limit selection and
ranking. One existing hybrid lookup combines lexical candidates and the existing
single CPU query embedding. Conversational filler is removed from multiword
lexical queries; single words remain searchable. Matches in extracted values rank
ahead of matches supplied only by predicates or entity labels. Importance breaks
ties in lexical selection. No personal-name-specific rule is introduced.

Existing FTS generations remain usable. The FTS index finds matches; a bounded
query joins their canonical revisions to score extracted-value words before the
candidate limit. This avoids copying source quotes or rebuilding vectors. The
canonical fallback also filters words before its limit, rather than sampling
high-importance memories and searching that sample afterward. It remains partial
without an index. Retrieval honors the requested limit, up to the existing public
100-row envelope, with pins sharing that envelope.

The agent requests up to 100 candidates within a separate 65,536-byte working
selection budget and selects whole extracted values into
at most one quarter of its configured input allowance, capped at 6,144 conservative
UTF-8 guard units. Its existing total prompt guard and output reserve still apply.
An oversized value is skipped so smaller useful memories can fit. Ordinary rows
omit evidence envelopes, memory IDs, revisions and import/processing timestamps;
subject attribution, polarity, modality, goal/plan state and supported temporal
qualifiers remain where they affect meaning. Exact selected IDs and source
dependencies stay in the existing delivery certificate. No inference summarizes
or rewrites extracted values.

## Invariants and failures

Source selection, grants, suppression, current revisions and temporal eligibility
still precede candidate limits and are rechecked before delivery. This change does
not bind imported entities to the live user, convert episodes into profile facts,
approve proposals, or promise that a bounded result contains every matching memory.
Index deadlines and missing encoders remain explicit partial/degraded outcomes.
No startup download, index rebuild, model generation or new service is introduced.

The inspected deployment config currently declares 8,192 context tokens and a
7,000 input ceiling. A configured 32,768 context is supported by the same bounded
selection; changing a running gateway's model context requires its normal reviewed
restart. This correction does not restart or mutate the active store.

## Acceptance

- An accepted name episode remains findable among many noisy `entity.name` facts
  by a colloquial name question, independently of profile identity binding.
- A word such as `Milo` retrieves facts, episodes, goals and plans, including
  lower-importance rows outside the old first-six/first-twenty sample. Word
  boundaries, Unicode, case and punctuation are covered.
- Multiple independent words match without requiring the full question as a
  substring; internal JSON keys/IDs do not create value matches.
- Higher-importance relevant values are favored, while denied, suppressed,
  obsolete and temporally ineligible rows never consume permitted top-k slots.
- Prompt tests cover compact exact values, semantic qualifiers, oversized rows,
  configured 8k/32k ceilings and delivery invalidation, without real model calls
  or private fixtures.

Historical Phase 8 performance measurements remain historical; the changed
ordinary candidate and prompt bounds require fresh measurements before release.
