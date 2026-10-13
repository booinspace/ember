"""Canonical SQLite schema 0.4.0: immutable baseline plus backed-up additive migration."""

SCHEMA_VERSION = 6
SCHEMA_SQL = """
CREATE TABLE store_metadata (
    store_id TEXT PRIMARY KEY, kind TEXT NOT NULL, namespace TEXT NOT NULL,
    recorded_clock_us INTEGER NOT NULL DEFAULT 0, policy_epoch INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE schema_versions (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE registry_stores (
    store_id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('library','character')),
    namespace TEXT NOT NULL, UNIQUE(kind, namespace)
);
CREATE TABLE source_records (
    record_kind TEXT NOT NULL, record_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0),
    recorded_at_us INTEGER NOT NULL, source_at_us INTEGER, role TEXT,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), payload_sha256 TEXT NOT NULL,
    PRIMARY KEY(record_kind, record_id, revision)
);
CREATE INDEX source_time ON source_records(record_kind, source_at_us, record_id);
CREATE INDEX source_role_time ON source_records(role, source_at_us);
CREATE TABLE message_parts (
    record_kind TEXT NOT NULL, record_id TEXT NOT NULL, revision INTEGER NOT NULL,
    part_id TEXT NOT NULL, ordinal INTEGER NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
    content_sha256 TEXT, PRIMARY KEY(record_kind, record_id, revision, part_id),
    FOREIGN KEY(record_kind,record_id,revision)
        REFERENCES source_records(record_kind,record_id,revision)
);
CREATE TABLE graph_edges (
    node_id TEXT NOT NULL, revision INTEGER NOT NULL, direction TEXT NOT NULL,
    ordinal INTEGER NOT NULL, status TEXT NOT NULL, target_id TEXT, target_revision INTEGER,
    raw_reference_sha256 TEXT, PRIMARY KEY(node_id,revision,direction,ordinal)
);
CREATE INDEX graph_traversal ON graph_edges(target_id,target_revision);
CREATE TABLE source_keys (
    adapter TEXT NOT NULL, record_kind TEXT NOT NULL, external_id TEXT NOT NULL,
    record_id TEXT NOT NULL, PRIMARY KEY(adapter,record_kind,external_id),
    UNIQUE(adapter,record_kind,record_id)
);
CREATE TABLE imports (
    adapter TEXT NOT NULL, adapter_version TEXT NOT NULL, inventory_sha256 TEXT NOT NULL,
    component_sha256 TEXT NOT NULL, cursor_json TEXT NOT NULL CHECK(json_valid(cursor_json)),
    PRIMARY KEY(adapter,adapter_version,inventory_sha256,component_sha256)
);
CREATE TABLE archive_receipts (
    idempotency_key TEXT PRIMARY KEY, request_sha256 TEXT NOT NULL,
    receipt_json TEXT NOT NULL CHECK(json_valid(receipt_json))
);
CREATE TABLE entities (
    entity_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), payload_sha256 TEXT NOT NULL,
    PRIMARY KEY(entity_id,revision)
);
CREATE TABLE entity_aliases (
    entity_id TEXT NOT NULL, alias TEXT NOT NULL, PRIMARY KEY(entity_id,alias)
);
CREATE INDEX entity_alias_lookup ON entity_aliases(alias);
CREATE TABLE predicate_definitions (
    name TEXT NOT NULL, version TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    PRIMARY KEY(name,version)
);
CREATE TABLE memories (
    memory_id TEXT PRIMARY KEY, current_revision INTEGER NOT NULL CHECK(current_revision > 0)
);
CREATE TABLE memory_revisions (
    memory_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
    kind TEXT NOT NULL, lifecycle TEXT NOT NULL, modality TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    importance REAL NOT NULL CHECK(importance BETWEEN 0 AND 1),
    recorded_at_us INTEGER NOT NULL, valid_from_us INTEGER, valid_until_us INTEGER,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), payload_sha256 TEXT NOT NULL,
    PRIMARY KEY(memory_id,revision), FOREIGN KEY(memory_id) REFERENCES memories(memory_id)
);
CREATE INDEX memory_recall ON memory_revisions(lifecycle,kind,importance,recorded_at_us);
CREATE INDEX memory_time ON memory_revisions(valid_from_us,valid_until_us,recorded_at_us);
CREATE TABLE evidence_spans (
    evidence_id TEXT PRIMARY KEY, record_kind TEXT NOT NULL, record_id TEXT NOT NULL,
    record_revision INTEGER NOT NULL, part_id TEXT NOT NULL,
    start_codepoint INTEGER NOT NULL CHECK(start_codepoint >= 0),
    end_codepoint INTEGER NOT NULL CHECK(end_codepoint > start_codepoint),
    start_byte INTEGER NOT NULL CHECK(start_byte >= 0),
    end_byte INTEGER NOT NULL CHECK(end_byte > start_byte),
    source_content_sha256 TEXT NOT NULL, quoted_span_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), payload_sha256 TEXT NOT NULL
);
CREATE TABLE memory_evidence (
    memory_id TEXT NOT NULL, revision INTEGER NOT NULL, evidence_id TEXT NOT NULL,
    source_store_id TEXT NOT NULL, record_kind TEXT NOT NULL, record_id TEXT NOT NULL,
    record_revision INTEGER NOT NULL, part_id TEXT NOT NULL,
    PRIMARY KEY(memory_id,revision,evidence_id),
    FOREIGN KEY(memory_id,revision) REFERENCES memory_revisions(memory_id,revision)
);
CREATE INDEX evidence_dependencies
    ON memory_evidence(source_store_id,record_kind,record_id,part_id);
CREATE TABLE source_grants (
    source_store_id TEXT NOT NULL, record_kind TEXT NOT NULL, record_id TEXT NOT NULL,
    record_revision INTEGER NOT NULL, part_id TEXT NOT NULL, capability TEXT NOT NULL,
    PRIMARY KEY(source_store_id,record_kind,record_id,record_revision,part_id,capability)
);
CREATE TABLE suppression_targets (
    target_kind TEXT NOT NULL, target_key TEXT NOT NULL, PRIMARY KEY(target_kind,target_key)
);
CREATE TABLE memory_dependencies (
    memory_id TEXT NOT NULL, revision INTEGER NOT NULL,
    target_kind TEXT NOT NULL, target_key TEXT NOT NULL,
    PRIMARY KEY(memory_id,revision,target_kind,target_key),
    FOREIGN KEY(memory_id,revision) REFERENCES memory_revisions(memory_id,revision)
);
CREATE INDEX suppression_dependencies ON memory_dependencies(target_kind,target_key);
CREATE TABLE memory_supersessions (
    old_memory_id TEXT NOT NULL, old_revision INTEGER NOT NULL, new_memory_id TEXT NOT NULL,
    new_revision INTEGER NOT NULL, transition_json TEXT NOT NULL CHECK(json_valid(transition_json)),
    recorded_at_us INTEGER NOT NULL, PRIMARY KEY(old_memory_id,old_revision,new_memory_id)
);
CREATE TABLE pins (
    memory_id TEXT PRIMARY KEY, memory_revision INTEGER NOT NULL,
    priority INTEGER NOT NULL, pinned_at TEXT NOT NULL
);
CREATE TABLE access_stats (
    memory_id TEXT NOT NULL, revision INTEGER NOT NULL, access_count INTEGER NOT NULL,
    last_accessed_at TEXT NOT NULL, PRIMARY KEY(memory_id,revision)
);
CREATE TABLE mutation_keys (
    idempotency_key TEXT PRIMARY KEY, operation TEXT NOT NULL, request_sha256 TEXT NOT NULL,
    receipt_json TEXT NOT NULL CHECK(json_valid(receipt_json))
);
CREATE TABLE index_outbox (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, record_id TEXT,
    revision INTEGER, policy_epoch INTEGER NOT NULL, recorded_at_us INTEGER NOT NULL
);
CREATE TABLE audit_events (
    event_id TEXT PRIMARY KEY, operation TEXT NOT NULL, record_id TEXT,
    recorded_at_us INTEGER NOT NULL, decision TEXT NOT NULL
);
"""
IMMUTABLE_TABLES = (
    "source_records",
    "message_parts",
    "graph_edges",
    "source_keys",
    "entities",
    "memory_revisions",
    "evidence_spans",
    "memory_evidence",
    "memory_dependencies",
    "memory_supersessions",
    "audit_events",
)

# Additive 0.2 schema; existing canonical revisions and public ABI remain unchanged.
PHASE_FOUR_SQL = """
ALTER TABLE source_grants ADD COLUMN expires_at TEXT;
CREATE TABLE candidates (
    candidate_id TEXT NOT NULL, revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
    PRIMARY KEY(candidate_id,revision)
);
CREATE TABLE approved_knowledge (
    knowledge_id TEXT NOT NULL, revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
    PRIMARY KEY(knowledge_id,revision)
);
CREATE TABLE knowledge_entities (
    knowledge_id TEXT NOT NULL, knowledge_revision INTEGER NOT NULL,
    entity_id TEXT NOT NULL, entity_revision INTEGER NOT NULL,
    PRIMARY KEY(knowledge_id,knowledge_revision,entity_id)
);
CREATE TABLE maintenance_receipts (
    idempotency_key TEXT PRIMARY KEY, operation TEXT NOT NULL,
    request_sha256 TEXT NOT NULL, receipt_json TEXT NOT NULL
);
CREATE TABLE entity_origins (
    origin_store_id TEXT NOT NULL, origin_entity_id TEXT NOT NULL, entity_id TEXT NOT NULL,
    PRIMARY KEY(origin_store_id,origin_entity_id), UNIQUE(entity_id)
);
CREATE TABLE materialized_evidence (
    evidence_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL
);
CREATE TABLE store_coverage (
    coverage TEXT NOT NULL CHECK(coverage IN ('full','evidence_only','selected_character'))
);
INSERT INTO store_coverage VALUES('full');
"""
PHASE_FOUR_IMMUTABLE = (
    "candidates",
    "approved_knowledge",
    "knowledge_entities",
    "entity_origins",
    "materialized_evidence",
)

# Cover only immutable headers, not large JSON/evidence. These B-trees accelerate
# canonical eligibility; FTS/embedding/chunk data remain disposable and separate.
PHASE_FIVE_SQL = """
CREATE INDEX IF NOT EXISTS memory_retrieval_identity ON memory_revisions(
    memory_id,revision,lifecycle,kind,modality,valid_from_us,valid_until_us,importance,recorded_at_us
);
CREATE INDEX IF NOT EXISTS memory_retrieval_order ON memory_revisions(
    lifecycle,importance DESC,recorded_at_us DESC,memory_id,revision,kind,modality,
    valid_from_us,valid_until_us
);
CREATE INDEX IF NOT EXISTS memory_pointer ON memories(memory_id,current_revision);
CREATE INDEX IF NOT EXISTS source_conversation ON source_records(
    json_extract(payload_json,'$.conversation.record_id'),record_kind,record_id,revision
);
"""


# Processing metadata is durable; prompts and unfinished model output are not.
PHASE_SIX_SQL = """
CREATE TABLE IF NOT EXISTS processing_specs (
    specification_sha256 TEXT PRIMARY KEY, payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processing_jobs (
    job_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, request_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL, status TEXT NOT NULL,
    completed_windows INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS processing_windows (
    job_id TEXT NOT NULL, ordinal INTEGER NOT NULL, window_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL, status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, error_code TEXT,
    PRIMARY KEY(job_id,ordinal), UNIQUE(job_id,window_sha256)
);
CREATE INDEX IF NOT EXISTS processing_pending ON processing_windows(job_id,status,ordinal);
CREATE TRIGGER IF NOT EXISTS immutable_processing_jobs_headers
BEFORE UPDATE OF job_id,idempotency_key,request_sha256,payload_json,payload_sha256
ON processing_jobs
BEGIN SELECT RAISE(ABORT, 'immutable processing definition'); END;
CREATE TRIGGER IF NOT EXISTS immutable_processing_windows_headers
BEFORE UPDATE OF job_id,ordinal,window_sha256,payload_json,payload_sha256 ON processing_windows
BEGIN SELECT RAISE(ABORT, 'immutable processing window'); END;

CREATE TABLE IF NOT EXISTS processing_candidates (
    job_id TEXT NOT NULL, ordinal INTEGER NOT NULL, candidate_id TEXT NOT NULL,
    revision INTEGER NOT NULL, fingerprint TEXT NOT NULL, conflict_key TEXT, value_key TEXT,
    PRIMARY KEY(candidate_id,revision)
);
CREATE INDEX IF NOT EXISTS processing_job_candidates ON processing_candidates(
    job_id,ordinal,candidate_id
);
CREATE INDEX IF NOT EXISTS processing_claim ON processing_candidates(conflict_key,value_key);
CREATE INDEX IF NOT EXISTS processing_fingerprint ON processing_candidates(fingerprint);
CREATE TABLE IF NOT EXISTS processing_failures (
    failure_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
    attempt INTEGER NOT NULL, code TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activation_decisions (
    decision_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS activation_candidate ON activation_decisions(candidate_id,revision);
CREATE TABLE IF NOT EXISTS review_decisions (
    decision_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, revision INTEGER NOT NULL,
    fingerprint TEXT NOT NULL, decision TEXT NOT NULL,
    recorded_at TEXT NOT NULL, payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS review_fingerprint ON review_decisions(fingerprint,recorded_at);
"""
PHASE_SIX_IMMUTABLE = (
    "processing_specs",
    "processing_candidates",
    "processing_failures",
    "activation_decisions",
    "review_decisions",
)


# Canonical supports/choices are append-only. Claim headers are disposable projections.
PHASE_SEVEN_SQL = """
CREATE INDEX IF NOT EXISTS pins_priority ON pins(priority DESC,pinned_at DESC,memory_id);
CREATE INDEX IF NOT EXISTS memory_recent_order ON memory_revisions(
    lifecycle,recorded_at_us DESC,memory_id,revision,kind,modality,importance,
    valid_from_us,valid_until_us
);
CREATE TABLE IF NOT EXISTS claim_projection (
    memory_id TEXT NOT NULL, revision INTEGER NOT NULL, subject_id TEXT NOT NULL,
    predicate TEXT NOT NULL, value_key TEXT NOT NULL, origin_key TEXT NOT NULL,
    claim_key TEXT NOT NULL, observed_at TEXT, temporal_basis TEXT NOT NULL,
    foreign_evidence INTEGER NOT NULL,
    PRIMARY KEY(memory_id,revision)
);
CREATE INDEX IF NOT EXISTS claim_equivalence ON claim_projection(claim_key,memory_id,revision);
CREATE INDEX IF NOT EXISTS claim_profile ON
    claim_projection(subject_id,predicate,memory_id,revision);
CREATE INDEX IF NOT EXISTS claim_conflict ON claim_projection(subject_id,predicate,value_key);
CREATE INDEX IF NOT EXISTS claim_foreign ON claim_projection(foreign_evidence);
CREATE INDEX IF NOT EXISTS claim_observed ON claim_projection(observed_at,memory_id,revision);
CREATE INDEX IF NOT EXISTS materialized_source_header ON materialized_evidence(
    json_extract(payload_json,'$.evidence.source.record_kind'),
    json_extract(payload_json,'$.evidence.source.record_id'),
    json_extract(payload_json,'$.evidence.source.record_revision'),
    json_extract(payload_json,'$.evidence.source.part_id'),
    json_extract(payload_json,'$.availability')
);
CREATE TABLE IF NOT EXISTS profile_policy (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), subject_id TEXT,
    enabled INTEGER NOT NULL DEFAULT 1, selection_epoch INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO profile_policy(singleton) VALUES(1);
CREATE TABLE IF NOT EXISTS profile_exclusions (predicate TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS native_acceptance (
    idempotency_key TEXT PRIMARY KEY, request_sha256 TEXT NOT NULL,
    candidate_id TEXT NOT NULL, revision INTEGER NOT NULL, claim_key TEXT NOT NULL,
    outcome TEXT NOT NULL, payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS native_acceptance_claim ON native_acceptance(claim_key,outcome);
CREATE TABLE IF NOT EXISTS memory_supports (
    support_id TEXT PRIMARY KEY, memory_id TEXT NOT NULL, memory_revision INTEGER NOT NULL,
    support_key TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS support_memory ON memory_supports(memory_id,memory_revision,support_id);
CREATE TABLE IF NOT EXISTS memory_relationships (
    relationship_id TEXT PRIMARY KEY, kind TEXT NOT NULL,
    source_id TEXT NOT NULL, source_revision INTEGER NOT NULL,
    target_id TEXT, target_revision INTEGER,
    payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS relationship_source ON memory_relationships(source_id,relationship_id);
CREATE INDEX IF NOT EXISTS relationship_target ON memory_relationships(target_id,relationship_id);
"""
PHASE_SEVEN_IMMUTABLE = ("native_acceptance", "memory_supports", "memory_relationships")

PHASE_EIGHT_SQL = """
CREATE INDEX IF NOT EXISTS entity_alias_fold ON entity_aliases(lower(alias),entity_id);
CREATE TABLE IF NOT EXISTS recall_policy (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), local_enabled INTEGER NOT NULL DEFAULT 1,
    import_mode TEXT NOT NULL DEFAULT 'all' CHECK(import_mode IN ('all','selected','none')),
    revision INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO recall_policy(singleton) VALUES(1);
CREATE TABLE IF NOT EXISTS recall_libraries (
    library_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL CHECK(enabled IN (0,1))
);
CREATE TABLE IF NOT EXISTS capture_policy (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    revision INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO capture_policy(singleton) VALUES(1);
CREATE TABLE IF NOT EXISTS native_capture (
    delivery_key TEXT PRIMARY KEY, source_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','paused','processing','complete','failed')),
    job_id TEXT, error_code TEXT
);
CREATE INDEX IF NOT EXISTS native_capture_pending ON native_capture(status,delivery_key);
CREATE TABLE IF NOT EXISTS temporal_enrichments (
    proposal_id TEXT PRIMARY KEY, memory_id TEXT NOT NULL, revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS temporal_enrichment_reviews (
    decision_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('approve','reject')),
    operation_id TEXT, recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS temporal_review_latest ON temporal_enrichment_reviews(proposal_id);
CREATE TRIGGER IF NOT EXISTS temporal_enrichments_no_update BEFORE UPDATE ON temporal_enrichments
BEGIN SELECT RAISE(ABORT,'immutable temporal proposal'); END;
CREATE TRIGGER IF NOT EXISTS temporal_enrichments_no_delete BEFORE DELETE ON temporal_enrichments
BEGIN SELECT RAISE(ABORT,'immutable temporal proposal'); END;
"""
