# -*- coding: utf-8 -*-
"""业务主库 schema：版本化迁移列表。

规则（规划 §业务库逻辑表）：本列表是唯一 DDL 正本；改表只能追加新版本，
不得改写已发布版本的 SQL（store 校验 checksum，漂移即拒绝启动）。
不使用 SQLite upsert/RETURNING 等板端 3.31 未证实的新语法；JSONB/STRICT 也不用。
MIGRATION_2 追加结构约束（CHECK/json_valid），按"建新表→校验复制（坏行入
persons_rejects 隔离表）→换表→重建索引"的 3.31 兼容方式改表。
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

# B01/B02/B03/B04：结构约束迁移（SQLite 3.31 兼容：不用 STRICT，不用跨表 CHECK）。
# 方法 = 建新表（带 CHECK）→ 校验并复制旧数据（坏行进 *_rejects，不静默丢）→ 换表 → 重建索引。
# 已发布迁移与 checksum 不改；本段是纯追加。
MIGRATION_2 = """
PRAGMA legacy_alter_table=ON;
CREATE TABLE IF NOT EXISTS persons_rejects(
  reject_id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity TEXT NOT NULL,
  key TEXT,
  reason TEXT NOT NULL,
  payload TEXT,
  rejected_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- 顺序＝先子表后父表：旧子表先换（DROP 后其引用消失），父表才能在 FK 开启下安全换表。
-- v2 子表故意不带 REFERENCES 子句（跨表一致性由业务写入口同事务校验，samples.enroll）；
-- 这是有记录的取舍：CHECK 管单表结构，FK 网络保留在其余未动表。
CREATE TABLE voice_samples_v2(
  sample_id TEXT PRIMARY KEY NOT NULL CHECK(length(sample_id) >= 8),
  person_id TEXT NOT NULL,
  model_id TEXT NOT NULL,
  dim INTEGER NOT NULL CHECK(dim > 0),
  dtype TEXT NOT NULL CHECK(dtype IN ('float32-le','float64-le')),
  blob BLOB NOT NULL CHECK(typeof(blob) = 'blob' AND length(blob) > 0 AND length(blob) = dim * CASE dtype WHEN 'float32-le' THEN 4 WHEN 'float64-le' THEN 8 END),
  blob_sha256 TEXT NOT NULL CHECK(length(blob_sha256) = 64),
  source_media_id TEXT,
  source_ref TEXT,
  quality TEXT,
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  created_at TEXT NOT NULL,
  revoked_at TEXT
);
INSERT INTO voice_samples_v2 SELECT sample_id, person_id, model_id, dim, dtype, blob,
  blob_sha256, source_media_id, source_ref, quality, active, created_at, revoked_at
  FROM voice_samples
WHERE (sample_id IS NOT NULL AND length(sample_id) >= 8
       AND person_id IS NOT NULL AND model_id IS NOT NULL
       AND dim IS NOT NULL AND dim > 0
       AND dtype IN ('float32-le','float64-le')
       AND typeof(blob) = 'blob' AND length(blob) > 0
       AND blob_sha256 IS NOT NULL AND length(blob_sha256) = 64
       AND active IN (0,1));
INSERT INTO persons_rejects(entity, key, reason, payload)
  SELECT 'voice_samples', sample_id, 'constraint_violation',
         sample_id || '|' || COALESCE(dtype,'') || '|' || CAST(length(blob) AS TEXT)
  FROM voice_samples WHERE (sample_id IS NULL OR length(sample_id) < 8
       OR person_id IS NULL OR model_id IS NULL
       OR dim IS NULL OR dim <= 0
       OR dtype NOT IN ('float32-le','float64-le')
       OR typeof(blob) != 'blob' OR length(blob) IS NULL OR length(blob) <= 0
       OR blob_sha256 IS NULL OR length(blob_sha256) != 64
       OR active NOT IN (0,1));
DROP TABLE voice_samples;
ALTER TABLE voice_samples_v2 RENAME TO voice_samples;
CREATE INDEX idx_voice_person ON voice_samples(person_id, model_id, active);

CREATE TABLE face_samples_v2(
  sample_id TEXT PRIMARY KEY NOT NULL CHECK(length(sample_id) >= 8),
  person_id TEXT NOT NULL,
  model_id TEXT NOT NULL,
  dim INTEGER NOT NULL CHECK(dim > 0),
  dtype TEXT NOT NULL CHECK(dtype IN ('float32-le','float64-le')),
  blob BLOB NOT NULL CHECK(typeof(blob) = 'blob' AND length(blob) > 0 AND length(blob) = dim * CASE dtype WHEN 'float32-le' THEN 4 WHEN 'float64-le' THEN 8 END),
  blob_sha256 TEXT NOT NULL CHECK(length(blob_sha256) = 64),
  source_media_id TEXT,
  source_ref TEXT,
  quality TEXT,
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  created_at TEXT NOT NULL,
  revoked_at TEXT
);
INSERT INTO face_samples_v2 SELECT sample_id, person_id, model_id, dim, dtype, blob,
  blob_sha256, source_media_id, source_ref, quality, active, created_at, revoked_at
  FROM face_samples
WHERE (sample_id IS NOT NULL AND length(sample_id) >= 8
       AND person_id IS NOT NULL AND model_id IS NOT NULL
       AND dim IS NOT NULL AND dim > 0
       AND dtype IN ('float32-le','float64-le')
       AND typeof(blob) = 'blob' AND length(blob) > 0
       AND blob_sha256 IS NOT NULL AND length(blob_sha256) = 64
       AND active IN (0,1));
INSERT INTO persons_rejects(entity, key, reason, payload)
  SELECT 'face_samples', sample_id, 'constraint_violation',
         sample_id || '|' || COALESCE(dtype,'') || '|' || CAST(length(blob) AS TEXT)
  FROM face_samples WHERE (sample_id IS NULL OR length(sample_id) < 8
       OR person_id IS NULL OR model_id IS NULL
       OR dim IS NULL OR dim <= 0
       OR dtype NOT IN ('float32-le','float64-le')
       OR typeof(blob) != 'blob' OR length(blob) IS NULL OR length(blob) <= 0
       OR blob_sha256 IS NULL OR length(blob_sha256) != 64
       OR active NOT IN (0,1));
DROP TABLE face_samples;
ALTER TABLE face_samples_v2 RENAME TO face_samples;
CREATE INDEX idx_face_person ON face_samples(person_id, model_id, active);

CREATE TABLE models_v2(
  model_id TEXT PRIMARY KEY NOT NULL CHECK(length(model_id) >= 1),
  modality TEXT NOT NULL CHECK(modality IN ('face','voice')),
  dim INTEGER NOT NULL CHECK(dim > 0),
  dtype TEXT NOT NULL DEFAULT 'float32-le' CHECK(dtype IN ('float32-le','float64-le')),
  metric TEXT NOT NULL DEFAULT 'cosine' CHECK(metric IN ('cosine','euclidean')),
  model_sha256 TEXT CHECK(model_sha256 IS NULL OR length(model_sha256) = 64),
  runtime TEXT,
  precision TEXT,
  preprocess_version TEXT,
  manifest_sha256 TEXT,
  legacy_pipeline_id TEXT,
  notes TEXT,
  created_at TEXT NOT NULL
);
INSERT INTO models_v2 SELECT model_id, modality, dim, dtype, metric, model_sha256,
  runtime, precision, preprocess_version, manifest_sha256, legacy_pipeline_id,
  notes, created_at FROM models
WHERE (model_id IS NOT NULL AND length(model_id) >= 1
       AND modality IN ('face','voice') AND dim > 0
       AND dtype IN ('float32-le','float64-le')
       AND metric IN ('cosine','euclidean')
       AND (model_sha256 IS NULL OR length(model_sha256) = 64));
INSERT INTO persons_rejects(entity, key, reason, payload)
  SELECT 'models', model_id, 'constraint_violation', model_id || '|' || COALESCE(dtype,'')
  FROM models WHERE (model_id IS NULL OR length(model_id) < 1
       OR modality NOT IN ('face','voice') OR dim IS NULL OR dim <= 0
       OR dtype NOT IN ('float32-le','float64-le')
       OR metric NOT IN ('cosine','euclidean')
       OR (model_sha256 IS NOT NULL AND length(model_sha256) != 64));
DROP TABLE models;
ALTER TABLE models_v2 RENAME TO models;

CREATE TABLE persons_v2(
  person_id TEXT PRIMARY KEY NOT NULL CHECK(length(person_id) >= 8),
  display_name TEXT NOT NULL CHECK(length(trim(display_name)) BETWEEN 1 AND 40),
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive','merged','deleted')),
  revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
);
INSERT INTO persons_v2 SELECT person_id, display_name, status, revision,
  created_at, updated_at, deleted_at FROM persons
WHERE (person_id IS NOT NULL AND length(person_id) >= 8
       AND display_name IS NOT NULL AND length(trim(display_name)) BETWEEN 1 AND 40
       AND status IN ('active','inactive','merged','deleted') AND revision >= 1);
INSERT INTO persons_rejects(entity, key, reason, payload)
  SELECT 'persons', person_id, 'constraint_violation', person_id || '|' || status
  FROM persons WHERE (person_id IS NULL OR length(person_id) < 8
       OR display_name IS NULL OR length(trim(display_name)) NOT BETWEEN 1 AND 40
       OR status NOT IN ('active','inactive','merged','deleted') OR revision IS NULL OR revision < 1);
DROP TABLE persons;
ALTER TABLE persons_v2 RENAME TO persons;

CREATE TABLE commands_v2(
  command_id TEXT PRIMARY KEY NOT NULL CHECK(length(command_id) >= 8),
  request_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  principal_id TEXT,
  session_id TEXT,
  client_id TEXT,
  action TEXT NOT NULL CHECK(length(action) >= 1),
  target TEXT,
  params_json TEXT NOT NULL CHECK(json_valid(params_json)),
  params_digest TEXT NOT NULL CHECK(length(params_digest) = 64),
  status TEXT NOT NULL CHECK(status IN
    ('accepted','running','succeeded','failed','unknown','cancelled')),
  code TEXT,
  message TEXT,
  result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
  desired_revision INTEGER CHECK(desired_revision IS NULL OR desired_revision >= 0),
  applied_revision INTEGER CHECK(applied_revision IS NULL OR applied_revision >= 0),
  event_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
INSERT INTO commands_v2 SELECT command_id, request_id, idempotency_key, principal_id,
  session_id, client_id, action, target, params_json, params_digest, status, code,
  message, result_json, desired_revision, applied_revision, event_id,
  created_at, updated_at FROM commands
WHERE (command_id IS NOT NULL AND length(command_id) >= 8
       AND idempotency_key IS NOT NULL
       AND action IS NOT NULL AND length(action) >= 1
       AND params_json IS NOT NULL AND json_valid(params_json)
       AND params_digest IS NOT NULL AND length(params_digest) = 64
       AND status IN ('accepted','running','succeeded','failed','unknown','cancelled')
       AND (result_json IS NULL OR json_valid(result_json)));
INSERT INTO persons_rejects(entity, key, reason, payload)
  SELECT 'commands', command_id, 'constraint_violation', command_id || '|' || status
  FROM commands WHERE (command_id IS NULL OR length(command_id) < 8
       OR idempotency_key IS NULL
       OR action IS NULL OR length(action) < 1
       OR params_json IS NULL OR NOT json_valid(params_json)
       OR params_digest IS NULL OR length(params_digest) != 64
       OR status NOT IN ('accepted','running','succeeded','failed','unknown','cancelled')
       OR (result_json IS NOT NULL AND NOT json_valid(result_json)));
DROP TABLE commands;
ALTER TABLE commands_v2 RENAME TO commands;
CREATE INDEX idx_commands_status ON commands(status, created_at);

CREATE TABLE settings_v2(
  scope TEXT NOT NULL,
  key TEXT NOT NULL,
  value_type TEXT NOT NULL CHECK(value_type IN ('float','enum','int','bool','string')),
  value TEXT NOT NULL CHECK(json_valid(value)),
  revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
  updated_at TEXT NOT NULL,
  updated_by TEXT,
  PRIMARY KEY(scope, key)
);
INSERT INTO settings_v2 SELECT scope, key, value_type, value, revision, updated_at,
  updated_by FROM settings
WHERE (scope IS NOT NULL AND key IS NOT NULL
       AND value_type IN ('float','enum','int','bool','string')
       AND value IS NOT NULL AND json_valid(value)
       AND revision IS NOT NULL AND revision >= 1);
INSERT INTO persons_rejects(entity, key, reason, payload)
  SELECT 'settings', scope || '/' || key, 'constraint_violation',
         COALESCE(scope,'') || '/' || COALESCE(key,'') || '|' || COALESCE(value_type,'')
  FROM settings WHERE (scope IS NULL OR key IS NULL
       OR value_type NOT IN ('float','enum','int','bool','string')
       OR value IS NULL OR NOT json_valid(value)
       OR revision IS NULL OR revision < 1);
DROP TABLE settings;
ALTER TABLE settings_v2 RENAME TO settings;
"""

MIGRATIONS = [(1, MIGRATION_1), (2, MIGRATION_2)]
