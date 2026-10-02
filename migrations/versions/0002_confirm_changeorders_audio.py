"""Client confirmations, change orders with pricing, audio transcription.

Revision ID: 0002_confirm_co_audio
Revises: 0001_initial
Create Date: 2026-10-03
"""
from alembic import op

revision = "0002_confirm_co_audio"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

UP = r"""
-- Client confirmation -----------------------------------------------------------
ALTER TABLE records ADD COLUMN client_confirmation text CHECK (client_confirmation IN ('confirmed', 'disputed'));
ALTER TABLE records ADD COLUMN client_comment text NOT NULL DEFAULT '';
ALTER TABLE records ADD COLUMN client_responded_at timestamptz;

CREATE TABLE confirmation_requests (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  client_id       uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  call_id         uuid REFERENCES calls(id) ON DELETE CASCADE,
  token_hash      text NOT NULL UNIQUE,
  status          text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'completed', 'revoked')),
  expires_at      timestamptz NOT NULL,
  responder_name  text NOT NULL DEFAULT '',
  completed_at    timestamptz,
  created_by      uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX confirmation_requests_client_idx ON confirmation_requests (client_id, created_at);

CREATE TABLE confirmation_items (
  request_id    uuid NOT NULL REFERENCES confirmation_requests(id) ON DELETE CASCADE,
  record_id     uuid NOT NULL REFERENCES records(id) ON DELETE CASCADE,
  position      integer NOT NULL,
  snapshot      jsonb NOT NULL,   -- exactly what the client was shown
  response      text CHECK (response IN ('confirmed', 'disputed')),
  comment       text NOT NULL DEFAULT '',
  responded_at  timestamptz,
  PRIMARY KEY (request_id, record_id)
);

-- Change orders -------------------------------------------------------------------
-- Money is stored in the smallest currency unit (paise for INR) as integers.
ALTER TABLE organizations ADD COLUMN currency text NOT NULL DEFAULT 'INR';
ALTER TABLE organizations ADD COLUMN default_tax_percent numeric(5,2) NOT NULL DEFAULT 18.00;

CREATE TABLE rate_items (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name        text NOT NULL,
  unit        text NOT NULL DEFAULT 'item',
  unit_price  bigint NOT NULL CHECK (unit_price >= 0),
  active      boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX rate_items_org_idx ON rate_items (org_id);

CREATE TABLE change_orders (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  client_id         uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  number            integer NOT NULL,
  title             text NOT NULL,
  notes             text NOT NULL DEFAULT '',
  status            text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'sent', 'approved', 'declined', 'withdrawn')),
  currency          text NOT NULL,
  tax_percent       numeric(5,2) NOT NULL,
  subtotal          bigint NOT NULL DEFAULT 0,
  tax               bigint NOT NULL DEFAULT 0,
  total             bigint NOT NULL DEFAULT 0,
  token_hash        text UNIQUE,
  sent_at           timestamptz,
  expires_at        timestamptz,
  decided_at        timestamptz,
  decided_by_name   text NOT NULL DEFAULT '',
  decision_comment  text NOT NULL DEFAULT '',
  created_by        uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, number)
);
CREATE INDEX change_orders_client_idx ON change_orders (client_id, created_at);

CREATE TABLE change_order_lines (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  change_order_id  uuid NOT NULL REFERENCES change_orders(id) ON DELETE CASCADE,
  position         integer NOT NULL,
  record_id        uuid REFERENCES records(id) ON DELETE SET NULL,
  description      text NOT NULL,
  quantity         numeric(10,2) NOT NULL CHECK (quantity > 0),
  unit             text NOT NULL DEFAULT 'item',
  unit_price       bigint NOT NULL CHECK (unit_price >= 0),
  amount           bigint NOT NULL
);
CREATE INDEX change_order_lines_co_idx ON change_order_lines (change_order_id, position);

-- Once sent, a change order's terms are frozen: only the decision fields may change.
CREATE FUNCTION change_order_guard() RETURNS trigger AS $$
BEGIN
  IF OLD.status <> 'draft' AND (NEW.title <> OLD.title OR NEW.notes <> OLD.notes OR NEW.currency <> OLD.currency
     OR NEW.tax_percent <> OLD.tax_percent OR NEW.subtotal <> OLD.subtotal OR NEW.tax <> OLD.tax
     OR NEW.total <> OLD.total OR NEW.client_id <> OLD.client_id OR NEW.number <> OLD.number) THEN
    RAISE EXCEPTION 'a sent change order cannot be edited';
  END IF;
  IF OLD.status IN ('approved', 'declined') AND NEW.status <> OLD.status THEN
    RAISE EXCEPTION 'a decided change order cannot change status';
  END IF;
  RETURN NEW;
END
$$ LANGUAGE plpgsql;
CREATE TRIGGER change_orders_guard BEFORE UPDATE ON change_orders FOR EACH ROW EXECUTE FUNCTION change_order_guard();

CREATE FUNCTION change_order_lines_guard() RETURNS trigger AS $$
DECLARE st text;
BEGIN
  IF coalesce(current_setting('onrecord.allow_erasure', true), '') = 'on' THEN
    RETURN coalesce(NEW, OLD);
  END IF;
  SELECT status INTO st FROM change_orders WHERE id = coalesce(NEW.change_order_id, OLD.change_order_id);
  IF st IS NOT NULL AND st <> 'draft' THEN
    RAISE EXCEPTION 'lines of a sent change order cannot be changed';
  END IF;
  RETURN coalesce(NEW, OLD);
END
$$ LANGUAGE plpgsql;
CREATE TRIGGER change_order_lines_guard BEFORE INSERT OR UPDATE OR DELETE ON change_order_lines
  FOR EACH ROW EXECUTE FUNCTION change_order_lines_guard();

-- Audio transcription ---------------------------------------------------------------
ALTER TABLE calls ADD COLUMN source text NOT NULL DEFAULT 'transcript' CHECK (source IN ('transcript', 'audio'));
ALTER TABLE calls ADD COLUMN speaker_map jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE transcription_jobs (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  client_id      uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  title          text NOT NULL,
  call_date      date NOT NULL,
  consent        boolean NOT NULL,
  language       text NOT NULL DEFAULT '',
  provider       text NOT NULL,
  status         text NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
  audio_sha256   text NOT NULL,
  audio_bytes    bigint NOT NULL,
  content_type   text NOT NULL,
  duration_sec   integer,
  extract_after  boolean NOT NULL DEFAULT false,
  call_id        uuid REFERENCES calls(id) ON DELETE SET NULL,
  error          text,
  created_by     uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  finished_at    timestamptz
);
CREATE INDEX transcription_jobs_client_idx ON transcription_jobs (client_id, created_at);
"""

DOWN = r"""
DROP TABLE IF EXISTS transcription_jobs, change_order_lines, change_orders, rate_items, confirmation_items, confirmation_requests CASCADE;
DROP FUNCTION IF EXISTS change_order_guard(), change_order_lines_guard();
ALTER TABLE calls DROP COLUMN IF EXISTS source, DROP COLUMN IF EXISTS speaker_map;
ALTER TABLE organizations DROP COLUMN IF EXISTS currency, DROP COLUMN IF EXISTS default_tax_percent;
ALTER TABLE records DROP COLUMN IF EXISTS client_confirmation, DROP COLUMN IF EXISTS client_comment, DROP COLUMN IF EXISTS client_responded_at;
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
