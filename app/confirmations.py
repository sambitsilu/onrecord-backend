"""Client confirmation: the client confirms or disputes each agreement from a link.

The link carries a random token; only its SHA-256 hash is stored. The client sees a
snapshot of each record taken when the link was created, so later edits can't change
what they confirmed. One submission completes the request.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .auth import OrgAccess
from .config import get_settings
from .models import Call, Client, ConfirmationItem, ConfirmationRequest, Organization, Record

CONFIRMABLE = ("decision", "approval", "action")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> tuple[str, str]:
    t = secrets.token_urlsafe(32)
    return t, hash_token(t)


def link(kind: str, token: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/{kind}/{token}"


def create_request(db: Session, access: OrgAccess, call: Call) -> tuple[ConfirmationRequest, str, int]:
    recs = db.execute(select(Record).where(Record.call_id == call.id, Record.reviewed.is_(True),
                                           Record.status == "current", Record.type.in_(CONFIRMABLE))
                      .order_by(Record.line_idx)).scalars().all()
    if not recs:
        raise HTTPException(409, "There are no accepted agreements from this call to confirm yet.")
    # A new link replaces any open one for the same call.
    for old in db.execute(select(ConfirmationRequest).where(ConfirmationRequest.call_id == call.id,
                                                            ConfirmationRequest.status == "open")).scalars():
        old.status = "revoked"
    token, th = new_token()
    req = ConfirmationRequest(org_id=access.org_id, client_id=call.client_id, call_id=call.id, token_hash=th,
                              expires_at=datetime.now(timezone.utc) + timedelta(days=get_settings().link_valid_days),
                              created_by=access.user.id)
    db.add(req)
    db.flush()
    for i, r in enumerate(recs):
        db.add(ConfirmationItem(request_id=req.id, record_id=r.id, position=i, snapshot={
            "type": r.type, "topic": r.topic, "value": r.value, "text": r.text, "owner": r.owner,
            "due": r.due_date.isoformat() if r.due_date else r.due_text}))
    audit.append(db, org_id=access.org_id, client_id=call.client_id, actor_user_id=access.user.id,
                 action="confirmation_link_created", entity_type="confirmation", entity_id=str(req.id),
                 note=f"Created a confirmation link for “{call.title}” ({len(recs)} agreements)")
    db.commit()
    return req, token, len(recs)


def load_by_token(db: Session, token: str, lock: bool = False) -> ConfirmationRequest:
    q = select(ConfirmationRequest).where(ConfirmationRequest.token_hash == hash_token(token))
    if lock:
        q = q.with_for_update()
    req = db.execute(q).scalar_one_or_none()
    if req is None or req.status == "revoked":
        raise HTTPException(404, "This link isn't valid. Ask the agency for a new one.")
    return req


def is_expired(req: ConfirmationRequest) -> bool:
    return req.status == "open" and req.expires_at < datetime.now(timezone.utc)


def public_view(db: Session, req: ConfirmationRequest) -> dict:
    client = db.get(Client, req.client_id)
    org = db.get(Organization, req.org_id)
    call = db.get(Call, req.call_id) if req.call_id else None
    items = db.execute(select(ConfirmationItem).where(ConfirmationItem.request_id == req.id)
                       .order_by(ConfirmationItem.position)).scalars().all()
    return {"agency": org.name, "client": client.name, "call_title": call.title if call else "",
            "call_date": call.call_date.isoformat() if call else "", "status": "expired" if is_expired(req) else req.status,
            "responder_name": req.responder_name, "expires_at": req.expires_at.isoformat(),
            "items": [{"record_id": str(it.record_id), **it.snapshot, "response": it.response, "comment": it.comment}
                      for it in items]}


def respond(db: Session, token: str, name: str, answers: dict[str, tuple[str, str]]) -> dict:
    """answers: record_id -> (response, comment). Every item must be answered."""
    req = load_by_token(db, token, lock=True)
    if req.status == "completed":
        raise HTTPException(409, "This confirmation has already been submitted.")
    if is_expired(req):
        raise HTTPException(410, "This link has expired. Ask the agency for a new one.")
    name = (name or "").strip()[:120]
    if not name:
        raise HTTPException(422, "Please enter your name.")
    items = db.execute(select(ConfirmationItem).where(ConfirmationItem.request_id == req.id)).scalars().all()
    missing = [str(it.record_id) for it in items if str(it.record_id) not in answers]
    if missing:
        raise HTTPException(422, "Please confirm or flag every item.")
    now = datetime.now(timezone.utc)
    disputed = 0
    for it in items:
        resp, comment = answers[str(it.record_id)]
        if resp not in ("confirmed", "disputed"):
            raise HTTPException(422, "Each item must be confirmed or flagged.")
        comment = (comment or "").strip()[:1000]
        if resp == "disputed" and not comment:
            raise HTTPException(422, "Please say what's not right for each flagged item.")
        it.response, it.comment, it.responded_at = resp, comment, now
        rec = db.get(Record, it.record_id)
        if rec is not None:
            rec.client_confirmation, rec.client_comment, rec.client_responded_at = resp, comment, now
        disputed += resp == "disputed"
    req.status, req.responder_name, req.completed_at = "completed", name, now
    audit.append(db, org_id=req.org_id, client_id=req.client_id, action="client_confirmed",
                 entity_type="confirmation", entity_id=str(req.id),
                 note=f"{name} (client) confirmed {len(items) - disputed} and flagged {disputed} of {len(items)} agreements",
                 after={"responses": {str(it.record_id): {"response": it.response, "comment": it.comment,
                                                          "shown": it.snapshot} for it in items}})
    db.commit()
    return {"confirmed": len(items) - disputed, "disputed": disputed}


def list_for_client(db: Session, client_id: uuid.UUID) -> list[ConfirmationRequest]:
    return db.execute(select(ConfirmationRequest).where(ConfirmationRequest.client_id == client_id)
                      .order_by(ConfirmationRequest.created_at.desc())).scalars().all()
