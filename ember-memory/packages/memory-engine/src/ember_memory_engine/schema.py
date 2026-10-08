"""Canonical SQLite schema 0.2.0: immutable baseline plus backed-up additive migration."""

SCHEMA_VERSION = 2
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
