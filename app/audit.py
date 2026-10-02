"""Append-only, hash-chained audit log.

Each event stores content_hash = sha256(note, before, after) and
hash = sha256(prev_hash | metadata | content_hash). Changing, reordering or
removing any event breaks the chain, which verify_chain() detects. The
database also refuses UPDATE/DELETE on audit_events (see the migration).
Erasure redacts content but keeps content_hash, so the chain still verifies.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .db import allow_erasure
from .models import AuditEvent, Organization

GENESIS = "0" * 64


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def content_hash(note, before, after) -> str:
    return _sha(_canon({"note": note, "before": before, "after": after}))


def event_hash(prev_hash: str, *, org_id, client_id, actor_user_id, action, entity_type, entity_id, at: datetime, c_hash: str) -> str:
    meta = {
        "org_id": str(org_id),
        "client_id": str(client_id) if client_id else None,
        "actor_user_id": str(actor_user_id) if actor_user_id else None,
        "action": action,
        "entity_type": entity_type or "",
        "entity_id": entity_id or "",
        "at": at.astimezone(timezone.utc).isoformat(timespec="microseconds"),
    }
    return _sha(prev_hash + "|" + _canon(meta) + "|" + c_hash)


def append(
    db: Session,
    *,
    org_id: uuid.UUID,
    action: str,
    client_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    entity_type: str = "",
    entity_id: str = "",
    note: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> AuditEvent:
    """Append an event inside the caller's transaction (commit is the caller's job)."""
    # Serialize appends per organization so the chain stays linear.
    db.execute(select(Organization.id).where(Organization.id == org_id).with_for_update())
    prev = db.execute(
        select(AuditEvent.hash).where(AuditEvent.org_id == org_id).order_by(AuditEvent.id.desc()).limit(1)
    ).scalar_one_or_none() or GENESIS
    at = datetime.now(timezone.utc)
    c_hash = content_hash(note, before, after)
    h = event_hash(prev, org_id=org_id, client_id=client_id, actor_user_id=actor_user_id, action=action,
                   entity_type=entity_type, entity_id=str(entity_id or ""), at=at, c_hash=c_hash)
    ev = AuditEvent(org_id=org_id, client_id=client_id, actor_user_id=actor_user_id, action=action,
                    entity_type=entity_type, entity_id=str(entity_id or ""), note=note, before=before, after=after,
                    redacted=False, at=at, content_hash=c_hash, prev_hash=prev, hash=h)
    db.add(ev)
    db.flush()
    return ev


def verify_chain(db: Session, org_id: uuid.UUID) -> dict:
    """Walk the org's chain. Returns {"ok": bool, "events": n, "problem": str | None}."""
    prev = GENESIS
    n = 0
    rows = db.execute(select(AuditEvent).where(AuditEvent.org_id == org_id).order_by(AuditEvent.id)).scalars()
    for ev in rows:
        n += 1
        if ev.prev_hash != prev:
            return {"ok": False, "events": n, "problem": f"event {ev.id}: link to previous event is broken"}
        if not ev.redacted and content_hash(ev.note, ev.before, ev.after) != ev.content_hash:
            return {"ok": False, "events": n, "problem": f"event {ev.id}: content was changed"}
        expected = event_hash(prev, org_id=ev.org_id, client_id=ev.client_id, actor_user_id=ev.actor_user_id,
                              action=ev.action, entity_type=ev.entity_type, entity_id=ev.entity_id, at=ev.at,
                              c_hash=ev.content_hash)
        if expected != ev.hash:
            return {"ok": False, "events": n, "problem": f"event {ev.id}: details were changed"}
        prev = ev.hash
    return {"ok": True, "events": n, "problem": None}


def redact_client(db: Session, org_id: uuid.UUID, client_id: uuid.UUID) -> int:
    """Erase the content of a client's audit events (for deletion requests). Chain stays valid."""
    allow_erasure(db)
    res = db.execute(
        update(AuditEvent)
        .where(AuditEvent.org_id == org_id, AuditEvent.client_id == client_id)
        .values(note=None, before=None, after=None, redacted=True)
    )
    return res.rowcount or 0
