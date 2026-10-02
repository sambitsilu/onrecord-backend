"""Change orders: turn out-of-scope requests into priced estimates the client approves.

Money is stored as integers in the smallest unit (paise for INR). Tax is rounded half-up
to the nearest paisa. Once sent, the terms are frozen by a database trigger.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import audit
from .auth import OrgAccess
from .config import get_settings
from .confirmations import hash_token, new_token
from .models import ChangeOrder, ChangeOrderLine, Client, Organization, RateItem, Record


def money(minor: int, currency: str) -> str:
    major = Decimal(minor) / 100
    if currency == "INR":
        # Indian grouping: 1,23,45,678.00
        whole, frac = f"{major:.2f}".split(".")
        neg = whole.startswith("-")
        whole = whole.lstrip("-")
        if len(whole) > 3:
            head, tail = whole[:-3], whole[-3:]
            groups = []
            while len(head) > 2:
                groups.insert(0, head[-2:])
                head = head[:-2]
            if head:
                groups.insert(0, head)
            whole = ",".join(groups + [tail])
        return ("-" if neg else "") + "₹" + whole + "." + frac
    return f"{currency} {major:,.2f}"


def line_amount(quantity: Decimal, unit_price: int) -> int:
    return int((Decimal(quantity) * unit_price).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def totals(lines: list[ChangeOrderLine], tax_percent: Decimal) -> tuple[int, int, int]:
    subtotal = sum(ln.amount for ln in lines)
    tax = int((Decimal(subtotal) * Decimal(tax_percent) / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return subtotal, tax, subtotal + tax


def get_change_order(db: Session, access: OrgAccess, co_id: uuid.UUID, lock: bool = False) -> ChangeOrder:
    q = select(ChangeOrder).where(ChangeOrder.id == co_id, ChangeOrder.org_id == access.org_id)
    if lock:
        q = q.with_for_update()
    co = db.execute(q).scalar_one_or_none()
    if co is None:
        raise HTTPException(404, "Change order not found.")
    return co


def lines_of(db: Session, co_id: uuid.UUID) -> list[ChangeOrderLine]:
    return db.execute(select(ChangeOrderLine).where(ChangeOrderLine.change_order_id == co_id)
                      .order_by(ChangeOrderLine.position)).scalars().all()


def _resolve_lines(db: Session, access: OrgAccess, client: Client, specs: list) -> list[dict]:
    out = []
    for s in specs:
        unit, price, desc = s.unit, s.unit_price, (s.description or "").strip()
        if s.rate_item_id:
            ri = db.execute(select(RateItem).where(RateItem.id == s.rate_item_id, RateItem.org_id == access.org_id)).scalar_one_or_none()
            if ri is None:
                raise HTTPException(422, "Unknown rate card item.")
            unit = unit or ri.unit
            price = ri.unit_price if price is None else price
            desc = desc or ri.name
        if s.record_id:
            rec = db.execute(select(Record).where(Record.id == s.record_id, Record.client_id == client.id)).scalar_one_or_none()
            if rec is None:
                raise HTTPException(422, "A linked record doesn't belong to this client.")
            desc = desc or rec.text
        if not desc:
            raise HTTPException(422, "Every line needs a description.")
        if price is None:
            raise HTTPException(422, f"Set a price for “{desc}”.")
        out.append({"record_id": s.record_id, "description": desc[:300], "quantity": Decimal(s.quantity),
                    "unit": (unit or "item")[:30], "unit_price": int(price)})
    return out


def _write_lines(db: Session, co: ChangeOrder, resolved: list[dict]) -> None:
    db.execute(delete(ChangeOrderLine).where(ChangeOrderLine.change_order_id == co.id))
    lines = []
    for i, r in enumerate(resolved):
        ln = ChangeOrderLine(change_order_id=co.id, position=i, amount=line_amount(r["quantity"], r["unit_price"]), **r)
        db.add(ln)
        lines.append(ln)
    db.flush()
    co.subtotal, co.tax, co.total = totals(lines, co.tax_percent)


def create(db: Session, access: OrgAccess, client: Client, body) -> ChangeOrder:
    org = db.execute(select(Organization).where(Organization.id == access.org_id).with_for_update()).scalar_one()
    number = (db.execute(select(func.max(ChangeOrder.number)).where(ChangeOrder.org_id == access.org_id)).scalar_one() or 0) + 1
    co = ChangeOrder(org_id=access.org_id, client_id=client.id, number=number, title=body.title.strip(), notes=body.notes.strip(),
                     currency=org.currency, created_by=access.user.id,
                     tax_percent=Decimal(body.tax_percent) if body.tax_percent is not None else org.default_tax_percent)
    db.add(co)
    db.flush()
    _write_lines(db, co, _resolve_lines(db, access, client, body.lines))
    audit.append(db, org_id=access.org_id, client_id=client.id, actor_user_id=access.user.id, action="change_order_drafted",
                 entity_type="change_order", entity_id=str(co.id),
                 note=f"Drafted change order #{co.number} “{co.title}”: {money(co.total, co.currency)}")
    db.commit()
    return co


def update_draft(db: Session, access: OrgAccess, co: ChangeOrder, body) -> ChangeOrder:
    if co.status != "draft":
        raise HTTPException(409, "Only drafts can be edited. Withdraw it and create a new one.")
    client = db.get(Client, co.client_id)
    if body.title is not None:
        co.title = body.title.strip()
    if body.notes is not None:
        co.notes = body.notes.strip()
    if body.tax_percent is not None:
        co.tax_percent = Decimal(body.tax_percent)
    if body.lines is not None:
        _write_lines(db, co, _resolve_lines(db, access, client, body.lines))
    else:
        co.subtotal, co.tax, co.total = totals(lines_of(db, co.id), co.tax_percent)
    audit.append(db, org_id=access.org_id, client_id=co.client_id, actor_user_id=access.user.id, action="change_order_edited",
                 entity_type="change_order", entity_id=str(co.id),
                 note=f"Edited draft change order #{co.number}: {money(co.total, co.currency)}")
    db.commit()
    return co


def send(db: Session, access: OrgAccess, co: ChangeOrder) -> tuple[ChangeOrder, str]:
    if co.status != "draft":
        raise HTTPException(409, "This change order has already been sent.")
    if not lines_of(db, co.id) or co.total <= 0:
        raise HTTPException(422, "Add at least one priced line before sending.")
    token, th = new_token()
    now = datetime.now(timezone.utc)
    co.status, co.token_hash, co.sent_at = "sent", th, now
    co.expires_at = now + timedelta(days=get_settings().link_valid_days)
    audit.append(db, org_id=access.org_id, client_id=co.client_id, actor_user_id=access.user.id, action="change_order_sent",
                 entity_type="change_order", entity_id=str(co.id),
                 note=f"Sent change order #{co.number} for approval: {money(co.total, co.currency)}",
                 after={"subtotal": co.subtotal, "tax": co.tax, "total": co.total, "tax_percent": str(co.tax_percent),
                        "lines": [{"description": ln.description, "quantity": str(ln.quantity), "unit_price": ln.unit_price,
                                   "amount": ln.amount} for ln in lines_of(db, co.id)]})
    db.commit()
    return co, token


def withdraw(db: Session, access: OrgAccess, co: ChangeOrder) -> ChangeOrder:
    if co.status not in ("draft", "sent"):
        raise HTTPException(409, "A decided change order can't be withdrawn.")
    co.status = "withdrawn"
    audit.append(db, org_id=access.org_id, client_id=co.client_id, actor_user_id=access.user.id, action="change_order_withdrawn",
                 entity_type="change_order", entity_id=str(co.id), note=f"Withdrew change order #{co.number}")
    db.commit()
    return co


def load_by_token(db: Session, token: str, lock: bool = False) -> ChangeOrder:
    q = select(ChangeOrder).where(ChangeOrder.token_hash == hash_token(token))
    if lock:
        q = q.with_for_update()
    co = db.execute(q).scalar_one_or_none()
    if co is None or co.status in ("draft", "withdrawn"):
        raise HTTPException(404, "This link isn't valid. Ask the agency for a new one.")
    return co


def public_view(db: Session, co: ChangeOrder) -> dict:
    expired = co.status == "sent" and co.expires_at and co.expires_at < datetime.now(timezone.utc)
    return {"agency": db.get(Organization, co.org_id).name, "client": db.get(Client, co.client_id).name,
            "number": co.number, "title": co.title, "notes": co.notes, "currency": co.currency,
            "status": "expired" if expired else co.status, "tax_percent": str(co.tax_percent),
            "subtotal": co.subtotal, "tax": co.tax, "total": co.total,
            "subtotal_text": money(co.subtotal, co.currency), "tax_text": money(co.tax, co.currency),
            "total_text": money(co.total, co.currency), "decided_by_name": co.decided_by_name,
            "lines": [{"description": ln.description, "quantity": str(ln.quantity.normalize()), "unit": ln.unit,
                       "unit_price_text": money(ln.unit_price, co.currency), "amount_text": money(ln.amount, co.currency)}
                      for ln in lines_of(db, co.id)]}


def decide(db: Session, token: str, approve: bool, name: str, comment: str) -> ChangeOrder:
    co = load_by_token(db, token, lock=True)
    if co.status != "sent":
        raise HTTPException(409, "This change order has already been answered.")
    if co.expires_at and co.expires_at < datetime.now(timezone.utc):
        raise HTTPException(410, "This link has expired. Ask the agency for a new one.")
    name = (name or "").strip()[:120]
    if not name:
        raise HTTPException(422, "Please enter your name.")
    co.status = "approved" if approve else "declined"
    co.decided_at, co.decided_by_name, co.decision_comment = datetime.now(timezone.utc), name, (comment or "").strip()[:1000]
    audit.append(db, org_id=co.org_id, client_id=co.client_id, action=f"change_order_{co.status}",
                 entity_type="change_order", entity_id=str(co.id),
                 note=f"{name} (client) {co.status} change order #{co.number}: {money(co.total, co.currency)}"
                      + (f". Comment: {co.decision_comment}" if co.decision_comment else ""))
    db.commit()
    return co


def scope_value(db: Session, client_id: uuid.UUID) -> dict:
    rows = db.execute(select(ChangeOrder.status, func.coalesce(func.sum(ChangeOrder.total), 0), func.count())
                      .where(ChangeOrder.client_id == client_id).group_by(ChangeOrder.status)).all()
    by = {s: (int(t), int(n)) for s, t, n in rows}
    return {"approved_total": by.get("approved", (0, 0))[0], "pending_total": by.get("sent", (0, 0))[0],
            "declined_total": by.get("declined", (0, 0))[0], "approved_count": by.get("approved", (0, 0))[1],
            "pending_count": by.get("sent", (0, 0))[1]}
