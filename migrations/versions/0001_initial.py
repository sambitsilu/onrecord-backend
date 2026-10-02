"""Initial schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-02
"""
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = r"""
CREATE TABLE users (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  auth_uid    text NOT NULL UNIQUE,
  email       text NOT NULL,
  name        text NOT NULL DEFAULT '',
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX users_email_idx ON users (lower(email));

CREATE TABLE organizations (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name                  text NOT NULL,
  monthly_token_budget  bigint NOT NULL,
  created_at            timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE memberships (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id        uuid REFERENCES users(id) ON DELETE CASCADE,
  invited_email  text,
  role           text NOT NULL CHECK (role IN ('owner', 'member')),
  created_at     timestamptz NOT NULL DEFAULT now(),
  CHECK (user_id IS NOT NULL OR invited_email IS NOT NULL)
);
CREATE UNIQUE INDEX memberships_org_user ON memberships (org_id, user_id) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX memberships_org_invite ON memberships (org_id, lower(invited_email)) WHERE user_id IS NULL;

CREATE TABLE clients (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name        text NOT NULL,
  people      text NOT NULL DEFAULT '',
  sow         text NOT NULL DEFAULT '',
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX clients_org_idx ON clients (org_id);

CREATE TABLE calls (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  client_id   uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  title       text NOT NULL,
  call_date   date NOT NULL,
  consent     boolean NOT NULL,
  sha256      text NOT NULL,
  line_count  integer NOT NULL,
  status      text NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'processing', 'processed', 'failed')),
  summary     text NOT NULL DEFAULT '',
  created_by  uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX calls_client_idx ON calls (org_id, client_id, call_date);

CREATE TABLE transcript_lines (
  call_id    uuid NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
  idx        integer NOT NULL,
  org_id     uuid NOT NULL,
  client_id  uuid NOT NULL,
  t          text,
  speaker    text NOT NULL DEFAULT '',
  text       text NOT NULL,
  tsv        tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(speaker, '') || ' ' || text)) STORED,
  PRIMARY KEY (call_id, idx)
);
CREATE INDEX transcript_lines_tsv_idx ON transcript_lines USING gin (tsv);
CREATE INDEX transcript_lines_client_idx ON transcript_lines (client_id);

CREATE TABLE extraction_runs (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  call_id        uuid NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
  status         text NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
  model          text NOT NULL,
  prompt_version text NOT NULL,
  input_tokens   integer NOT NULL DEFAULT 0,
  output_tokens  integer NOT NULL DEFAULT 0,
  created_count  integer NOT NULL DEFAULT 0,
  flagged_count  integer NOT NULL DEFAULT 0,
  error          text,
  created_by     uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  started_at     timestamptz,
  finished_at    timestamptz
);
CREATE INDEX extraction_runs_call_idx ON extraction_runs (call_id, created_at);

CREATE TABLE records (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  client_id     uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  call_id       uuid NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
  run_id        uuid REFERENCES extraction_runs(id) ON DELETE SET NULL,
  source        text NOT NULL CHECK (source IN ('ai', 'manual')),
  type          text NOT NULL CHECK (type IN ('decision', 'approval', 'action', 'scope_change', 'open_question')),
  topic         text NOT NULL DEFAULT '',
  value         text NOT NULL DEFAULT '',
  text          text NOT NULL,
  who           text NOT NULL DEFAULT '',
  owner         text NOT NULL DEFAULT '',
  due_text      text NOT NULL DEFAULT '',
  due_date      date,
  line_idx      integer,
  t             text,
  quote         text NOT NULL DEFAULT '',
  verified      boolean,
  numbers_ok    boolean,
  role_ok       boolean,
  confidence    text NOT NULL DEFAULT 'high' CHECK (confidence IN ('high', 'low')),
  outside_scope boolean NOT NULL DEFAULT false,
  scope_note    text NOT NULL DEFAULT '',
  status        text NOT NULL DEFAULT 'current' CHECK (status IN ('current', 'replaced')),
  replaced_by   uuid REFERENCES records(id) ON DELETE SET NULL,
  replaces      uuid[] NOT NULL DEFAULT '{}',
  reviewed      boolean NOT NULL DEFAULT false,
  reviewed_by   uuid REFERENCES users(id) ON DELETE SET NULL,
  reviewed_at   timestamptz,
  done          boolean NOT NULL DEFAULT false,
  original      jsonb,
  edited_at     timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX records_client_idx ON records (org_id, client_id, reviewed);
CREATE INDEX records_call_idx ON records (call_id);

CREATE TABLE llm_usage (
  id             bigserial PRIMARY KEY,
  org_id         uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  kind           text NOT NULL,
  model          text NOT NULL,
  input_tokens   integer NOT NULL,
  output_tokens  integer NOT NULL,
  at             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX llm_usage_org_idx ON llm_usage (org_id, at);

-- Append-only, hash-chained audit log. client_id has no foreign key so the
-- log outlives a deleted client (its content is redacted instead).
CREATE TABLE audit_events (
  id             bigserial PRIMARY KEY,
  org_id         uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  client_id      uuid,
  actor_user_id  uuid,
  action         text NOT NULL,
  entity_type    text NOT NULL DEFAULT '',
  entity_id      text NOT NULL DEFAULT '',
  note           text,
  before         jsonb,
  after          jsonb,
  redacted       boolean NOT NULL DEFAULT false,
  at             timestamptz NOT NULL,
  content_hash   text NOT NULL,
  prev_hash      text NOT NULL,
  hash           text NOT NULL
);
CREATE INDEX audit_events_org_idx ON audit_events (org_id, id);
CREATE INDEX audit_events_client_idx ON audit_events (client_id, id);

-- Guards ------------------------------------------------------------------
-- Audit events can never be changed or deleted, except inside an erasure
-- transaction (SET LOCAL onrecord.allow_erasure = 'on'), which may only
-- redact content fields. The hash chain still verifies after redaction
-- because it covers content_hash, which is kept.
CREATE FUNCTION audit_guard() RETURNS trigger AS $$
BEGIN
  IF coalesce(current_setting('onrecord.allow_erasure', true), '') = 'on' THEN
    IF TG_OP = 'DELETE' THEN
      RETURN OLD;
    END IF;
    IF NEW.id <> OLD.id OR NEW.org_id <> OLD.org_id OR NEW.action <> OLD.action OR NEW.at <> OLD.at
       OR NEW.content_hash <> OLD.content_hash OR NEW.prev_hash <> OLD.prev_hash OR NEW.hash <> OLD.hash THEN
      RAISE EXCEPTION 'audit_events: erasure may only redact content';
    END IF;
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'audit_events is append-only';
END
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_events_guard
  BEFORE UPDATE OR DELETE ON audit_events
  FOR EACH ROW EXECUTE FUNCTION audit_guard();

-- Transcripts are evidence: lines can be deleted with their call, never edited.
CREATE FUNCTION transcript_guard() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'transcript lines cannot be edited';
END
$$ LANGUAGE plpgsql;

CREATE TRIGGER transcript_lines_guard
  BEFORE UPDATE ON transcript_lines
  FOR EACH ROW EXECUTE FUNCTION transcript_guard();

CREATE FUNCTION call_fingerprint_guard() RETURNS trigger AS $$
BEGIN
  IF NEW.sha256 <> OLD.sha256 OR NEW.line_count <> OLD.line_count OR NEW.call_date <> OLD.call_date
     OR NEW.consent <> OLD.consent OR NEW.client_id <> OLD.client_id OR NEW.org_id <> OLD.org_id THEN
    RAISE EXCEPTION 'a call''s transcript fingerprint, date, consent and owner cannot be changed';
  END IF;
  RETURN NEW;
END
$$ LANGUAGE plpgsql;

CREATE TRIGGER calls_fingerprint_guard
  BEFORE UPDATE ON calls
  FOR EACH ROW EXECUTE FUNCTION call_fingerprint_guard();
"""

DROP = r"""
DROP TABLE IF EXISTS audit_events, llm_usage, records, extraction_runs, transcript_lines, calls,
  clients, memberships, organizations, users CASCADE;
DROP FUNCTION IF EXISTS audit_guard(), transcript_guard(), call_fingerprint_guard();
"""


def upgrade() -> None:
    op.execute(SCHEMA)


def downgrade() -> None:
    op.execute(DROP)
