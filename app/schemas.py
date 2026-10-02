"""API request and response shapes."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

RecordType = Literal["decision", "approval", "action", "scope_change", "open_question"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- organizations -------------------------------------------------------------

class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class OrgOut(ORM):
    id: uuid.UUID
    name: str
    role: str | None = None


class MemberInvite(BaseModel):
    email: EmailStr
    role: Literal["owner", "member"] = "member"


class MemberOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    pending: bool


# --- clients -----------------------------------------------------------------------

class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    people: str = Field(default="", max_length=500)
    sow: str = Field(default="", max_length=20_000)


class ClientPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    people: str | None = Field(default=None, max_length=500)
    sow: str | None = Field(default=None, max_length=20_000)


class ClientOut(ORM):
    id: uuid.UUID
    name: str
    people: str
    sow: str
    created_at: datetime


# --- calls -------------------------------------------------------------------------

class CallIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    call_date: date
    transcript: str = Field(min_length=1)
    consent: bool = Field(description="Everyone on the call knew it was recorded or transcribed. Must be true.")
    extract: bool = True


class CallOut(ORM):
    source: str = "transcript"
    speaker_map: dict = {}
    id: uuid.UUID
    client_id: uuid.UUID
    title: str
    call_date: date
    consent: bool
    sha256: str
    line_count: int
    status: str
    summary: str
    created_at: datetime


class LineOut(BaseModel):
    idx: int
    t: str | None
    speaker: str
    text: str


class RunOut(ORM):
    id: uuid.UUID
    call_id: uuid.UUID
    status: str
    model: str
    prompt_version: str
    created_count: int
    flagged_count: int
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class CallCreated(BaseModel):
    call: CallOut
    run: RunOut | None


# --- records -----------------------------------------------------------------------

class RecordOut(ORM):
    id: uuid.UUID
    client_id: uuid.UUID
    call_id: uuid.UUID
    source: str
    type: str
    topic: str
    value: str
    text: str
    who: str
    owner: str
    due_text: str
    due_date: date | None
    line_idx: int | None
    t: str | None
    quote: str
    verified: bool | None
    numbers_ok: bool | None
    role_ok: bool | None
    confidence: str
    outside_scope: bool
    scope_note: str
    status: str
    replaced_by: uuid.UUID | None
    replaces: list[uuid.UUID]
    reviewed: bool
    reviewed_at: datetime | None
    done: bool
    original: dict | None
    edited_at: datetime | None
    created_at: datetime
    client_confirmation: str | None = None
    client_comment: str = ""
    client_responded_at: datetime | None = None
    needs_check: bool = False
    check_reasons: list[str] = []


class AcceptIn(BaseModel):
    confirm_flagged: bool = Field(default=False, description="Required to accept a record that failed a check.")


class AcceptBatchIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class AcceptBatchOut(BaseModel):
    accepted: list[uuid.UUID]
    skipped_need_check: list[uuid.UUID]


class RecordPatch(BaseModel):
    topic: str | None = Field(default=None, max_length=60)
    value: str | None = Field(default=None, max_length=120)
    text: str | None = Field(default=None, min_length=1, max_length=500)
    done: bool | None = None
    due_date: date | None = None


class ManualRecordIn(BaseModel):
    call_id: uuid.UUID
    type: RecordType
    topic: str = Field(default="", max_length=60)
    value: str = Field(default="", max_length=120)
    text: str = Field(min_length=1, max_length=500)
    who: str = Field(default="", max_length=80)
    owner: str = Field(default="", max_length=80)
    due_date: date | None = None
    line_idx: int | None = None
    quote: str = Field(default="", max_length=300)
    outside_scope: bool = False


class MergeTopicsIn(BaseModel):
    from_topic: str = Field(min_length=1, max_length=60)
    to_topic: str = Field(min_length=1, max_length=60)


# --- brief, ask, recap, quality ------------------------------------------------------

class BriefEntry(BaseModel):
    id: uuid.UUID
    value: str
    text: str
    type: str
    who: str
    call_id: uuid.UUID
    call_date: date
    t: str | None
    quote: str
    verified: bool | None
    client_confirmation: str | None = None
    client_comment: str = ""


class BriefTerm(BaseModel):
    topic: str
    current: list[BriefEntry]
    history: list[BriefEntry]
    conflict: bool


class BriefOut(BaseModel):
    terms: list[BriefTerm]
    pending_review: int
    open_actions: int
    overdue_actions: int
    outside_scope: int
    disputed_by_client: int = 0


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=500)


class Source(BaseModel):
    n: int
    call_id: uuid.UUID
    call_title: str
    call_date: date
    line_idx: int | None
    t: str | None
    text: str | None


class AskOut(BaseModel):
    answer: str
    sources: list[Source]
    searched_excerpts_only: bool


class RecapIn(BaseModel):
    signer: str = Field(default="", max_length=80)
    agency: str = Field(default="", max_length=120)
    include_confirmation_link: bool = Field(default=True, description="Create a link the client uses to confirm each item")


class RecapOut(BaseModel):
    subject: str
    body: str
    left_out_pending: int
    confirm_link: str | None = None


class QualityOut(BaseModel):
    ai_records: int
    accepted_unchanged: int
    accepted_after_edit: int
    discarded: int
    pending: int
    manual_additions: int
    flagged: int
    flagged_then_discarded: int
    unflagged_then_discarded: int
    discard_rate: float | None
    edit_rate: float | None


class AuditOut(ORM):
    id: int
    action: str
    entity_type: str
    entity_id: str
    note: str | None
    before: dict | None
    after: dict | None
    redacted: bool
    at: datetime
    actor_user_id: uuid.UUID | None
    hash: str


class ChainOut(BaseModel):
    ok: bool
    events: int
    problem: str | None


# --- client confirmation ------------------------------------------------------------

class ConfirmationOut(ORM):
    id: uuid.UUID
    call_id: uuid.UUID | None
    status: str
    expires_at: datetime
    responder_name: str
    completed_at: datetime | None
    created_at: datetime


class ConfirmationCreated(BaseModel):
    confirmation: ConfirmationOut
    link: str
    items: int


class PublicAnswer(BaseModel):
    record_id: uuid.UUID
    response: Literal["confirmed", "disputed"]
    comment: str = Field(default="", max_length=1000)


class PublicConfirmIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    answers: list[PublicAnswer] = Field(min_length=1, max_length=200)


# --- change orders ---------------------------------------------------------------------

class BillingSettings(BaseModel):
    currency: str = Field(min_length=3, max_length=3, pattern="^[A-Z]{3}$")
    default_tax_percent: Decimal = Field(ge=0, le=100)


class RateItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    unit: str = Field(default="item", max_length=30)
    unit_price: int = Field(ge=0, description="In the smallest currency unit, e.g. paise: ₹4,500 = 450000")
    active: bool = True


class RateItemOut(ORM):
    id: uuid.UUID
    name: str
    unit: str
    unit_price: int
    active: bool


class CoLineIn(BaseModel):
    description: str = Field(default="", max_length=300)
    quantity: Decimal = Field(default=Decimal("1"), gt=0, le=Decimal("99999"))
    unit: str | None = Field(default=None, max_length=30)
    unit_price: int | None = Field(default=None, ge=0, description="Smallest currency unit. Taken from the rate item if omitted.")
    rate_item_id: uuid.UUID | None = None
    record_id: uuid.UUID | None = Field(default=None, description="The out-of-scope request this line prices")


class ChangeOrderIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=4000)
    tax_percent: Decimal | None = Field(default=None, ge=0, le=100)
    lines: list[CoLineIn] = Field(min_length=1, max_length=100)


class ChangeOrderPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)
    tax_percent: Decimal | None = Field(default=None, ge=0, le=100)
    lines: list[CoLineIn] | None = Field(default=None, min_length=1, max_length=100)


class CoLineOut(ORM):
    position: int
    record_id: uuid.UUID | None
    description: str
    quantity: Decimal
    unit: str
    unit_price: int
    amount: int


class ChangeOrderOut(ORM):
    id: uuid.UUID
    client_id: uuid.UUID
    number: int
    title: str
    notes: str
    status: str
    currency: str
    tax_percent: Decimal
    subtotal: int
    tax: int
    total: int
    total_text: str = ""
    sent_at: datetime | None
    expires_at: datetime | None
    decided_at: datetime | None
    decided_by_name: str
    decision_comment: str
    created_at: datetime
    lines: list[CoLineOut] = []
    link: str | None = None


class ScopeValueOut(BaseModel):
    currency: str
    approved_total: int
    pending_total: int
    declined_total: int
    approved_count: int
    pending_count: int
    approved_text: str
    pending_text: str


class PublicDecisionIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    decision: Literal["approve", "decline"]
    comment: str = Field(default="", max_length=1000)


# --- audio ---------------------------------------------------------------------------------

class TranscriptionJobOut(ORM):
    id: uuid.UUID
    client_id: uuid.UUID
    title: str
    call_date: date
    status: str
    provider: str
    audio_sha256: str
    audio_bytes: int
    duration_sec: int | None
    call_id: uuid.UUID | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class SpeakersIn(BaseModel):
    speakers: dict[str, str] = Field(description='e.g. {"Speaker A": "Meera", "Speaker B": "Arjun"}')
