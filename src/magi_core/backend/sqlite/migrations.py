"""SQLite schema migrations for the local memory record store."""

SCHEMA_VERSION = 10

MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_registry (
    entity_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    entity_type TEXT,
    revision INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    expired_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_entity_registry_workspace_name
    ON entity_registry(workspace_id, canonical_name);

CREATE TABLE IF NOT EXISTS relation_registry (
    relation_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    entity_a_id TEXT NOT NULL,
    entity_b_id TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    expired_at TEXT,
    UNIQUE(workspace_id, entity_a_id, entity_b_id)
);

CREATE TABLE IF NOT EXISTS episodes (
    episode_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    reference_at TEXT NOT NULL,
    source_uri TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    track_id TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_episodes_workspace_created
    ON episodes(workspace_id, created_at);

CREATE TABLE IF NOT EXISTS atoms (
    atom_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    owner_kind TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    content TEXT NOT NULL,
    normalized_hash TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    subject_entity_id TEXT,
    predicate TEXT,
    object_entity_id TEXT,
    relation_keywords_json TEXT NOT NULL DEFAULT '[]',
    valid_at TEXT,
    invalid_at TEXT,
    temporal_text TEXT,
    temporal_precision TEXT,
    created_at TEXT NOT NULL,
    expired_at TEXT,
    confidence REAL,
    importance REAL,
    support_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(workspace_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_atoms_owner
    ON atoms(workspace_id, owner_kind, owner_id);
CREATE INDEX IF NOT EXISTS idx_atoms_valid_time
    ON atoms(workspace_id, valid_at, invalid_at);

CREATE TABLE IF NOT EXISTS atom_evidence (
    atom_id TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    quote TEXT,
    span_start INTEGER NOT NULL DEFAULT -1,
    span_end INTEGER NOT NULL DEFAULT -1,
    extraction_revision TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(atom_id, episode_id, span_start, span_end),
    FOREIGN KEY(atom_id) REFERENCES atoms(atom_id),
    FOREIGN KEY(episode_id) REFERENCES episodes(episode_id)
);

CREATE TABLE IF NOT EXISTS atom_evolution (
    source_atom_id TEXT NOT NULL,
    target_atom_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(source_atom_id, target_atom_id, relation_type),
    FOREIGN KEY(source_atom_id) REFERENCES atoms(atom_id),
    FOREIGN KEY(target_atom_id) REFERENCES atoms(atom_id)
);

CREATE TABLE IF NOT EXISTS projection_outbox (
    operation_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    episode_id TEXT,
    owner_ids_json TEXT NOT NULL,
    target_revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    applied_at TEXT,
    FOREIGN KEY(episode_id) REFERENCES episodes(episode_id)
);
CREATE INDEX IF NOT EXISTS idx_projection_outbox_pending
    ON projection_outbox(workspace_id, status, created_at);
"""

MIGRATION_2 = """
CREATE TABLE IF NOT EXISTS entity_aliases (
    workspace_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, normalized_alias, entity_id),
    FOREIGN KEY(entity_id) REFERENCES entity_registry(entity_id)
);
CREATE INDEX IF NOT EXISTS idx_entity_aliases_entity
    ON entity_aliases(workspace_id, entity_id);

CREATE VIRTUAL TABLE IF NOT EXISTS entity_name_fts USING fts5(
    workspace_id UNINDEXED,
    entity_id UNINDEXED,
    name,
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS memory_embeddings (
    workspace_id TEXT NOT NULL,
    object_kind TEXT NOT NULL,
    object_id TEXT NOT NULL,
    owner_id TEXT,
    model_name TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector BLOB NOT NULL,
    source_text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, object_kind, object_id, model_name)
);
CREATE INDEX IF NOT EXISTS idx_memory_embeddings_owner
    ON memory_embeddings(workspace_id, object_kind, owner_id, model_name);

CREATE TABLE IF NOT EXISTS workspace_settings (
    workspace_id TEXT NOT NULL,
    setting_key TEXT NOT NULL,
    setting_value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, setting_key)
);
"""

MIGRATION_3 = """
CREATE TABLE IF NOT EXISTS memory_deletion_backups (
    backup_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    restored_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_deletion_backups_episode
    ON memory_deletion_backups(workspace_id, episode_id, created_at);
"""

MIGRATION_4 = """
CREATE INDEX IF NOT EXISTS idx_projection_outbox_owner
    ON projection_outbox(workspace_id, owner_id);
CREATE INDEX IF NOT EXISTS idx_projection_outbox_retry
    ON projection_outbox(workspace_id, status, next_attempt_at, updated_at);
"""

MIGRATION_5 = """
ALTER TABLE atom_evidence RENAME TO atom_evidence_v4;

CREATE TABLE atom_evidence (
    evidence_id TEXT PRIMARY KEY,
    atom_id TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    quote TEXT,
    span_start INTEGER NOT NULL DEFAULT -1,
    span_end INTEGER NOT NULL DEFAULT -1,
    extraction_revision TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(atom_id) REFERENCES atoms(atom_id),
    FOREIGN KEY(episode_id) REFERENCES episodes(episode_id)
);
CREATE INDEX idx_atom_evidence_atom ON atom_evidence(atom_id);
CREATE INDEX idx_atom_evidence_episode ON atom_evidence(episode_id);

INSERT INTO atom_evidence(
    evidence_id, atom_id, episode_id, quote, span_start, span_end,
    extraction_revision, created_at
)
SELECT
    'legacy-' || lower(hex(randomblob(16))), atom_id, episode_id, quote,
    span_start, span_end, extraction_revision, created_at
FROM atom_evidence_v4;

DROP TABLE atom_evidence_v4;
"""

MIGRATION_6 = """
CREATE TABLE IF NOT EXISTS owner_summary_checkpoints (
    workspace_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    checkpoint_summary TEXT NOT NULL DEFAULT '',
    covered_atom_ids_json TEXT NOT NULL DEFAULT '[]',
    pending_atom_ids_json TEXT NOT NULL DEFAULT '[]',
    summary_revision INTEGER NOT NULL DEFAULT 0,
    prompt_version TEXT NOT NULL,
    model_identity TEXT NOT NULL,
    incremental_compaction_count INTEGER NOT NULL DEFAULT 0,
    last_compacted_at TEXT,
    last_reason TEXT,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, owner_id)
);
"""

MIGRATION_7 = """
CREATE TABLE IF NOT EXISTS exploration_traces (
    workspace_id TEXT NOT NULL,
    exploration_id TEXT NOT NULL,
    query TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, exploration_id)
);
CREATE INDEX IF NOT EXISTS idx_exploration_traces_updated
    ON exploration_traces(workspace_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS exploration_events (
    workspace_id TEXT NOT NULL,
    exploration_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    call_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, exploration_id, seq),
    FOREIGN KEY(workspace_id, exploration_id)
        REFERENCES exploration_traces(workspace_id, exploration_id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_exploration_events_call
    ON exploration_events(workspace_id, exploration_id, call_id);
"""

MIGRATION_8 = """
CREATE TABLE IF NOT EXISTS dreaming_runs (
    run_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    status TEXT NOT NULL,
    phase TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    snapshot_id TEXT,
    node_count INTEGER NOT NULL DEFAULT 0,
    relationship_count INTEGER NOT NULL DEFAULT 0,
    community_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_dreaming_runs_workspace_created
    ON dreaming_runs(workspace_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dreaming_runs_one_active
    ON dreaming_runs(workspace_id) WHERE status = 'running';

CREATE TABLE IF NOT EXISTS dreaming_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    algorithm_version TEXT,
    config_json TEXT NOT NULL DEFAULT '{}',
    node_count INTEGER NOT NULL,
    relationship_count INTEGER NOT NULL,
    community_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    published_at TEXT,
    FOREIGN KEY(run_id) REFERENCES dreaming_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_dreaming_snapshots_workspace_published
    ON dreaming_snapshots(workspace_id, published_at DESC);

CREATE TABLE IF NOT EXISTS dreaming_memberships (
    snapshot_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    community_id TEXT NOT NULL,
    membership_status TEXT NOT NULL DEFAULT 'stable',
    PRIMARY KEY(snapshot_id, entity_id),
    FOREIGN KEY(snapshot_id) REFERENCES dreaming_snapshots(snapshot_id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_dreaming_memberships_community
    ON dreaming_memberships(snapshot_id, community_id);
"""

MIGRATION_9 = """
ALTER TABLE dreaming_runs ADD COLUMN report_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE dreaming_runs ADD COLUMN prompt_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE dreaming_runs ADD COLUMN completion_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE dreaming_runs ADD COLUMN total_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE dreaming_runs ADD COLUMN llm_call_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE dreaming_runs ADD COLUMN token_usage_source TEXT;

ALTER TABLE dreaming_snapshots ADD COLUMN report_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE dreaming_snapshots ADD COLUMN prompt_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE dreaming_snapshots ADD COLUMN completion_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE dreaming_snapshots ADD COLUMN total_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE dreaming_snapshots ADD COLUMN llm_call_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE dreaming_snapshots ADD COLUMN token_usage_source TEXT;
"""

MIGRATION_10 = """
CREATE TABLE IF NOT EXISTS dreaming_community_reports (
    snapshot_id TEXT NOT NULL,
    community_id TEXT NOT NULL,
    community_name TEXT NOT NULL,
    report TEXT NOT NULL,
    member_count INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    llm_call_count INTEGER NOT NULL DEFAULT 0,
    token_usage_source TEXT,
    PRIMARY KEY(snapshot_id, community_id),
    FOREIGN KEY(snapshot_id) REFERENCES dreaming_snapshots(snapshot_id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_dreaming_reports_snapshot_members
    ON dreaming_community_reports(snapshot_id, member_count DESC);
"""

MIGRATIONS = (
    (1, MIGRATION_1),
    (2, MIGRATION_2),
    (3, MIGRATION_3),
    (4, MIGRATION_4),
    (5, MIGRATION_5),
    (6, MIGRATION_6),
    (7, MIGRATION_7),
    (8, MIGRATION_8),
    (9, MIGRATION_9),
    (10, MIGRATION_10),
)
