# -*- coding: utf-8 -*-
"""业务主库 schema：版本化迁移列表。

规则（规划 §业务库逻辑表）：本列表是唯一 DDL 正本；改表只能追加新版本，
不得改写已发布版本的 SQL（store 校验 checksum，漂移即拒绝启动）。
不使用 SQLite upsert/RETURNING 等 3.8 板端未探测的特性。
"""

MIGRATION_1 = """
CREATE TABLE store_meta(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE persons(
  person_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
);
CREATE TABLE external_identity_map(
  namespace TEXT NOT NULL,
  source_store_id TEXT NOT NULL,
  legacy_key TEXT NOT NULL,
  person_id TEXT NOT NULL REFERENCES persons(person_id),
  state TEXT NOT NULL DEFAULT 'linked',
  created_at TEXT NOT NULL,
  PRIMARY KEY(namespace, source_store_id, legacy_key)
);
CREATE TABLE models(
  model_id TEXT PRIMARY KEY,
  modality TEXT NOT NULL,
  dim INTEGER NOT NULL,
  dtype TEXT NOT NULL DEFAULT 'float32-le',
  metric TEXT NOT NULL DEFAULT 'cosine',
  model_sha256 TEXT,
  runtime TEXT,
  precision TEXT,
  preprocess_version TEXT,
  manifest_sha256 TEXT,
  legacy_pipeline_id TEXT,
  notes TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE face_samples(
  sample_id TEXT PRIMARY KEY,
  person_id TEXT NOT NULL REFERENCES persons(person_id),
  model_id TEXT NOT NULL REFERENCES models(model_id),
  dim INTEGER NOT NULL,
  dtype TEXT NOT NULL,
  blob BLOB NOT NULL,
  blob_sha256 TEXT NOT NULL,
  source_media_id TEXT,
  source_ref TEXT,
  quality TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  revoked_at TEXT
);
CREATE TABLE voice_samples(
  sample_id TEXT PRIMARY KEY,
  person_id TEXT NOT NULL REFERENCES persons(person_id),
  model_id TEXT NOT NULL REFERENCES models(model_id),
  dim INTEGER NOT NULL,
  dtype TEXT NOT NULL,
  blob BLOB NOT NULL,
  blob_sha256 TEXT NOT NULL,
  source_media_id TEXT,
  source_ref TEXT,
  quality TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  revoked_at TEXT
);
CREATE INDEX idx_face_person ON face_samples(person_id, model_id, active);
CREATE INDEX idx_voice_person ON voice_samples(person_id, model_id, active);
CREATE TABLE areas(
  area_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE devices(
  device_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  area_id TEXT REFERENCES areas(area_id),
  created_at TEXT NOT NULL
);
CREATE TABLE entities(
  entity_id TEXT PRIMARY KEY,
  device_id TEXT REFERENCES devices(device_id),
  area_id TEXT REFERENCES areas(area_id),
  name TEXT NOT NULL,
  capabilities TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);
CREATE TABLE settings(
  scope TEXT NOT NULL,
  key TEXT NOT NULL,
  value_type TEXT NOT NULL,
  value TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL,
  updated_by TEXT,
  PRIMARY KEY(scope, key)
);
CREATE TABLE settings_changes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope TEXT NOT NULL,
  key TEXT NOT NULL,
  old_revision INTEGER,
  new_revision INTEGER NOT NULL,
  changed_at TEXT NOT NULL,
  changed_by TEXT,
  note TEXT
);
CREATE TABLE settings_applied(
  scope TEXT NOT NULL,
  key TEXT NOT NULL,
  epoch INTEGER NOT NULL,
  revision INTEGER NOT NULL,
  applied_at TEXT NOT NULL,
  PRIMARY KEY(scope, key)
);
CREATE TABLE principals(
  principal_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL DEFAULT 'operator',
  display_name TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE sessions(
  token_hash TEXT PRIMARY KEY,
  principal_id TEXT NOT NULL REFERENCES principals(principal_id),
  created_at TEXT NOT NULL,
  expires_at TEXT,
  revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE pending_confirmations(
  confirmation_id TEXT PRIMARY KEY,
  principal_id TEXT,
  action TEXT NOT NULL,
  params_digest TEXT NOT NULL,
  target TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  consumed_at TEXT,
  command_id TEXT
);
CREATE TABLE commands(
  command_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  principal_id TEXT,
  session_id TEXT,
  client_id TEXT,
  action TEXT NOT NULL,
  target TEXT,
  params_json TEXT NOT NULL,
  params_digest TEXT NOT NULL,
  status TEXT NOT NULL,
  code TEXT,
  message TEXT,
  result_json TEXT,
  desired_revision INTEGER,
  applied_revision INTEGER,
  event_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_commands_status ON commands(status, created_at);
CREATE TABLE command_attempts(
  attempt_id TEXT PRIMARY KEY,
  command_id TEXT NOT NULL REFERENCES commands(command_id),
  operation_id TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  outcome TEXT,
  detail TEXT
);
CREATE TABLE events(
  event_id TEXT PRIMARY KEY,
  producer_id TEXT NOT NULL,
  component_id TEXT,
  producer_seq INTEGER NOT NULL,
  type TEXT NOT NULL,
  request_id TEXT,
  session_id TEXT,
  person_id TEXT REFERENCES persons(person_id),
  device_id TEXT,
  recording_id TEXT,
  media_id TEXT,
  boot_id TEXT,
  wall_time TEXT NOT NULL,
  monotonic_ns INTEGER,
  time_quality TEXT,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(producer_id, producer_seq)
);
CREATE INDEX idx_events_time ON events(wall_time);
CREATE TABLE verification_sessions(
  session_id TEXT PRIMARY KEY,
  purpose TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL,
  expires_at TEXT
);
CREATE TABLE verification_evidence(
  evidence_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES verification_sessions(session_id),
  modality TEXT NOT NULL,
  person_id TEXT REFERENCES persons(person_id),
  verdict TEXT NOT NULL,
  model_id TEXT,
  template_revision INTEGER,
  frame_id TEXT,
  frame_token TEXT,
  captured_monotonic_ns INTEGER,
  wall_time TEXT,
  fresh INTEGER NOT NULL DEFAULT 0,
  raw_json TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE media_assets(
  media_id TEXT PRIMARY KEY,
  storage_root TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  kind TEXT NOT NULL,
  size_bytes INTEGER,
  sha256 TEXT,
  status TEXT NOT NULL DEFAULT 'registered',
  created_at TEXT NOT NULL,
  UNIQUE(storage_root, relative_path)
);
CREATE TABLE recordings(
  recording_id TEXT PRIMARY KEY,
  device_id TEXT,
  format TEXT,
  index_format TEXT,
  first_wall_time TEXT,
  last_wall_time TEXT,
  duration_s REAL,
  size_bytes INTEGER,
  trash_state TEXT NOT NULL DEFAULT 'none',
  deleted_at TEXT,
  purge_started_at TEXT,
  purge_finished_at TEXT,
  legacy_ref TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE media_operations(
  operation_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  recording_id TEXT,
  media_id TEXT,
  state TEXT NOT NULL,
  detail TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE tasks(
  task_id TEXT PRIMARY KEY,
  action TEXT NOT NULL,
  params_json TEXT NOT NULL,
  run_at TEXT,
  interval_s INTEGER,
  iana_tz TEXT NOT NULL DEFAULT 'Asia/Shanghai',
  missed_policy TEXT NOT NULL DEFAULT 'skip',
  status TEXT NOT NULL DEFAULT 'active',
  next_due_at TEXT,
  last_result_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_tasks_due ON tasks(status, next_due_at);
CREATE TABLE task_attempts(
  attempt_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(task_id),
  occurrence_key TEXT NOT NULL,
  command_id TEXT,
  state TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  UNIQUE(task_id, occurrence_key)
);
CREATE TABLE outbox(
  outbox_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  aggregate TEXT NOT NULL,
  revision INTEGER NOT NULL,
  payload_ref TEXT,
  state TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  delivered_at TEXT
);
CREATE TABLE consumer_offsets(
  consumer TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  last_revision INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);
"""

MIGRATIONS = [(1, MIGRATION_1)]
