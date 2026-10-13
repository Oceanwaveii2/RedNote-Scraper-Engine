-- Proposed authored-state schema. Apply to a NEW Scatter database, never source DB.
PRAGMA foreign_keys=ON;
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS scatter_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scatter_projects(
 id TEXT PRIMARY KEY, title TEXT NOT NULL, regime TEXT NOT NULL CHECK(regime IN ('SPARSE_SCATTER','DEEP_EVIDENCE_RENDER')),
 head_revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scatter_revisions(
 project_id TEXT NOT NULL REFERENCES scatter_projects(id), revision INTEGER NOT NULL,
 parent_revision INTEGER, graph_json TEXT NOT NULL CHECK(json_valid(graph_json)), graph_hash TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(project_id,revision)
);
CREATE TABLE IF NOT EXISTS scatter_commands(
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES scatter_projects(id), idempotency_key TEXT NOT NULL,
 expected_revision INTEGER NOT NULL, result_revision INTEGER NOT NULL, operation TEXT NOT NULL,
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), inverse_json TEXT CHECK(inverse_json IS NULL OR json_valid(inverse_json)),
 created_at TEXT NOT NULL, UNIQUE(project_id,idempotency_key),
 FOREIGN KEY(project_id,result_revision) REFERENCES scatter_revisions(project_id,revision)
);
CREATE TABLE IF NOT EXISTS scatter_archive_instances(
 id TEXT PRIMARY KEY, adapter TEXT NOT NULL CHECK(adapter IN ('legacy_sqlite','v2_http','local_asset','deep_bundle')),
 locator TEXT NOT NULL, schema_fingerprint TEXT NOT NULL, verified_at TEXT
);
CREATE TABLE IF NOT EXISTS scatter_source_snapshots(
 id TEXT PRIMARY KEY, archive_instance_id TEXT NOT NULL REFERENCES scatter_archive_instances(id),
 entity_kind TEXT NOT NULL, entity_id TEXT NOT NULL, platform_source_id TEXT, revision TEXT NOT NULL,
 availability TEXT NOT NULL CHECK(availability IN ('available','partial','missing','unbound')),
 canonical_locator TEXT, evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)), evidence_hash TEXT NOT NULL,
 coverage_json TEXT NOT NULL CHECK(json_valid(coverage_json)), captured_at TEXT NOT NULL,
 UNIQUE(archive_instance_id,entity_kind,entity_id,revision)
);
CREATE TABLE IF NOT EXISTS scatter_observations(
 id TEXT PRIMARY KEY, source_snapshot_id TEXT NOT NULL REFERENCES scatter_source_snapshots(id),
 processor TEXT NOT NULL, processor_revision TEXT NOT NULL, input_hash TEXT NOT NULL,
 observation_json TEXT NOT NULL CHECK(json_valid(observation_json)), created_at TEXT NOT NULL,
 UNIQUE(source_snapshot_id,processor,processor_revision,input_hash)
);
CREATE TABLE IF NOT EXISTS scatter_actors(
 id TEXT NOT NULL, release INTEGER NOT NULL, project_id TEXT NOT NULL REFERENCES scatter_projects(id),
 derived_from_id TEXT, identity_policy TEXT NOT NULL CHECK(identity_policy IN ('same_identity_new_form','derived_actor')),
 definition_json TEXT NOT NULL CHECK(json_valid(definition_json)), definition_hash TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(id,release)
);
CREATE TABLE IF NOT EXISTS scatter_runs(
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, graph_revision INTEGER NOT NULL,
 parent_run_id TEXT REFERENCES scatter_runs(id), idempotency_key TEXT NOT NULL,
 snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)), snapshot_hash TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','resolving','interpreting','waiting_for_selection','waiting_for_resolution','planning','writing','validating','completed','failed','cancelling','cancelled','interrupted')),
 freshness TEXT NOT NULL CHECK(freshness IN ('current','stale')), cancellation_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancellation_requested IN (0,1)),
 remote_cancellation_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(remote_cancellation_confirmed IN (0,1)),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(project_id,idempotency_key),
 FOREIGN KEY(project_id,graph_revision) REFERENCES scatter_revisions(project_id,revision)
);
CREATE TABLE IF NOT EXISTS scatter_run_sources(
 run_id TEXT NOT NULL REFERENCES scatter_runs(id), source_snapshot_id TEXT NOT NULL REFERENCES scatter_source_snapshots(id),
 PRIMARY KEY(run_id,source_snapshot_id)
);
CREATE TABLE IF NOT EXISTS scatter_run_events(
 run_id TEXT NOT NULL REFERENCES scatter_runs(id), sequence INTEGER NOT NULL,
 event_type TEXT NOT NULL, payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), created_at TEXT NOT NULL,
 PRIMARY KEY(run_id,sequence)
);
CREATE TABLE IF NOT EXISTS scatter_provider_attempts(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES scatter_runs(id), stage TEXT NOT NULL, attempt INTEGER NOT NULL,
 provider TEXT NOT NULL CHECK(provider IN ('codex','gemini','fixture')), model TEXT NOT NULL,
 input_hash TEXT NOT NULL, schema_hash TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('prepared','submitted','responded','validated','failed','cancel_requested','ambiguous')),
 request_locator TEXT, response_text TEXT, receipt_json TEXT CHECK(receipt_json IS NULL OR json_valid(receipt_json)),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(run_id,stage,attempt)
);
CREATE TABLE IF NOT EXISTS scatter_candidates(
 id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES scatter_runs(id), rank INTEGER NOT NULL,
 candidate_json TEXT NOT NULL CHECK(json_valid(candidate_json)), candidate_hash TEXT NOT NULL,
 PRIMARY KEY(run_id,id), UNIQUE(run_id,rank)
);
CREATE TABLE IF NOT EXISTS scatter_plans(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES scatter_runs(id),
 selected_candidate_id TEXT NOT NULL, selected_candidate_run_id TEXT NOT NULL,
 plan_json TEXT NOT NULL CHECK(json_valid(plan_json)), plan_hash TEXT NOT NULL,
 FOREIGN KEY(selected_candidate_run_id,selected_candidate_id) REFERENCES scatter_candidates(run_id,id)
);
CREATE TABLE IF NOT EXISTS scatter_documents(
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES scatter_projects(id), title TEXT NOT NULL, head_revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS scatter_document_revisions(
 document_id TEXT NOT NULL REFERENCES scatter_documents(id), revision INTEGER NOT NULL,
 parent_revision INTEGER, run_id TEXT REFERENCES scatter_runs(id), content_json TEXT NOT NULL CHECK(json_valid(content_json)),
 content_hash TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(document_id,revision)
);
CREATE TABLE IF NOT EXISTS scatter_block_locks(
 document_id TEXT NOT NULL REFERENCES scatter_documents(id), block_id TEXT NOT NULL,
 lock_kind TEXT NOT NULL CHECK(lock_kind IN ('text','fact','identity','endpoint')), value_json TEXT NOT NULL CHECK(json_valid(value_json)),
 author_revision INTEGER NOT NULL, PRIMARY KEY(document_id,block_id,lock_kind)
);
CREATE TABLE IF NOT EXISTS scatter_issues(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES scatter_runs(id), code TEXT NOT NULL,
 requires_human_resolution INTEGER NOT NULL CHECK(requires_human_resolution IN (0,1)),
 severity TEXT NOT NULL CHECK(severity IN ('error','warning','information')),
 issue_json TEXT NOT NULL CHECK(json_valid(issue_json)), resolved_by_run_id TEXT REFERENCES scatter_runs(id)
);
CREATE TABLE IF NOT EXISTS scatter_dependencies(
 project_id TEXT NOT NULL REFERENCES scatter_projects(id), product_id TEXT NOT NULL, input_kind TEXT NOT NULL,
 input_id TEXT NOT NULL, input_hash TEXT NOT NULL, PRIMARY KEY(project_id,product_id,input_kind,input_id)
);
CREATE INDEX IF NOT EXISTS scatter_events_replay ON scatter_run_events(run_id,sequence);
CREATE INDEX IF NOT EXISTS scatter_attempt_recovery ON scatter_provider_attempts(status,updated_at);
CREATE TRIGGER IF NOT EXISTS scatter_snapshot_immutable BEFORE UPDATE OF snapshot_json,snapshot_hash,graph_revision,project_id,parent_run_id ON scatter_runs BEGIN SELECT RAISE(ABORT,'Run snapshot immutable'); END;
CREATE TRIGGER IF NOT EXISTS scatter_revision_immutable BEFORE UPDATE ON scatter_revisions BEGIN SELECT RAISE(ABORT,'Graph revision immutable'); END;
CREATE TRIGGER IF NOT EXISTS scatter_source_immutable BEFORE UPDATE ON scatter_source_snapshots BEGIN SELECT RAISE(ABORT,'Source snapshot immutable'); END;
INSERT OR IGNORE INTO scatter_migrations VALUES(1,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
COMMIT;
