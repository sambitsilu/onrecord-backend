"""ORM models. The schema itself lives in migrations/versions (raw SQL)."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _uuid()
    auth_uid: Mapped[str] = mapped_column(Text, unique=True)
    email: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = _uuid()
    name: Mapped[str] = mapped_column(Text)
    monthly_token_budget: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(Text, default="INR")
    default_tax_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("18.00"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Membership(Base):
    __tablename__ = "memberships"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    invited_email: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Client(Base):
    __tablename__ = "clients"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    people: Mapped[str] = mapped_column(Text, default="")
    sow: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Call(Base):
    __tablename__ = "calls"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(Text)
    call_date: Mapped[date] = mapped_column(Date)
    consent: Mapped[bool] = mapped_column(Boolean)
    sha256: Mapped[str] = mapped_column(Text)
    line_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, default="new")
    summary: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(Text, default="transcript")
    speaker_map: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TranscriptLine(Base):
    __tablename__ = "transcript_lines"
    call_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    t: Mapped[str | None] = mapped_column(Text)
    speaker: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text)


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    call_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_count: Mapped[int] = mapped_column(Integer, default=0)
    flagged_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Record(Base):
    __tablename__ = "records"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"))
    call_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("extraction_runs.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)
    topic: Mapped[str] = mapped_column(Text, default="")
    value: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text)
    who: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str] = mapped_column(Text, default="")
    due_text: Mapped[str] = mapped_column(Text, default="")
    due_date: Mapped[date | None] = mapped_column(Date)
    line_idx: Mapped[int | None] = mapped_column(Integer)
    t: Mapped[str | None] = mapped_column(Text)
    quote: Mapped[str] = mapped_column(Text, default="")
    verified: Mapped[bool | None] = mapped_column(Boolean)
    numbers_ok: Mapped[bool | None] = mapped_column(Boolean)
    role_ok: Mapped[bool | None] = mapped_column(Boolean)
    confidence: Mapped[str] = mapped_column(Text, default="high")
    outside_scope: Mapped[bool] = mapped_column(Boolean, default=False)
    scope_note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="current")
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("records.id", ondelete="SET NULL"))
    replaces: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    original: Mapped[dict | None] = mapped_column(JSONB)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    client_confirmation: Mapped[str | None] = mapped_column(Text)
    client_comment: Mapped[str] = mapped_column(Text, default="")
    client_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LLMUsage(Base):
    __tablename__ = "llm_usage"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text, default="")
    entity_id: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str | None] = mapped_column(Text)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    redacted: Mapped[bool] = mapped_column(Boolean, default=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(Text)
    prev_hash: Mapped[str] = mapped_column(Text)
    hash: Mapped[str] = mapped_column(Text)


class ConfirmationRequest(Base):
    __tablename__ = "confirmation_requests"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"))
    call_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(Text, default="open")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    responder_name: Mapped[str] = mapped_column(Text, default="")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConfirmationItem(Base):
    __tablename__ = "confirmation_items"
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("confirmation_requests.id", ondelete="CASCADE"), primary_key=True)
    record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("records.id", ondelete="CASCADE"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    response: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str] = mapped_column(Text, default="")
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RateItem(Base):
    __tablename__ = "rate_items"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(Text, default="item")
    unit_price: Mapped[int] = mapped_column(BigInteger)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChangeOrder(Base):
    __tablename__ = "change_orders"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="draft")
    currency: Mapped[str] = mapped_column(Text)
    tax_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    subtotal: Mapped[int] = mapped_column(BigInteger, default=0)
    tax: Mapped[int] = mapped_column(BigInteger, default=0)
    total: Mapped[int] = mapped_column(BigInteger, default=0)
    token_hash: Mapped[str | None] = mapped_column(Text, unique=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_name: Mapped[str] = mapped_column(Text, default="")
    decision_comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChangeOrderLine(Base):
    __tablename__ = "change_order_lines"
    id: Mapped[uuid.UUID] = _uuid()
    change_order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("change_orders.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("records.id", ondelete="SET NULL"))
    description: Mapped[str] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    unit: Mapped[str] = mapped_column(Text, default="item")
    unit_price: Mapped[int] = mapped_column(BigInteger)
    amount: Mapped[int] = mapped_column(BigInteger)


class TranscriptionJob(Base):
    __tablename__ = "transcription_jobs"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(Text)
    call_date: Mapped[date] = mapped_column(Date)
    consent: Mapped[bool] = mapped_column(Boolean)
    language: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    audio_sha256: Mapped[str] = mapped_column(Text)
    audio_bytes: Mapped[int] = mapped_column(BigInteger)
    content_type: Mapped[str] = mapped_column(Text)
    duration_sec: Mapped[int | None] = mapped_column(Integer)
    extract_after: Mapped[bool] = mapped_column(Boolean, default=False)
    call_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("calls.id", ondelete="SET NULL"))
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
