"""Client confirmation links, change orders, rate card, audio upload and speaker names."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, change_orders as co_svc, confirmations, jobs, services, transcription
from ..auth import OrgAccess, get_client, org_access, require_owner
from ..db import get_db
from ..models import ChangeOrder, Organization, RateItem, TranscriptionJob
from ..schemas import (BillingSettings, CallOut, ChangeOrderIn, ChangeOrderOut, ChangeOrderPatch, ConfirmationCreated,
                       ConfirmationOut, RateItemIn, RateItemOut, ScopeValueOut, SpeakersIn, TranscriptionJobOut)

router = APIRouter(prefix="/v1/orgs/{org_id}", tags=["confirmations, change orders, audio"])


# --- client confirmation -------------------------------------------------------------

@router.post("/calls/{call_id}/confirmation", response_model=ConfirmationCreated, status_code=201)
def create_confirmation(call_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    """Create a link the client opens to confirm or flag each accepted agreement from this call.
    The link is shown only once; a new link replaces the previous open one."""
    call = services.get_call(db, access, call_id)
    req, token, n = confirmations.create_request(db, access, call)
    return {"confirmation": req, "link": confirmations.link("confirm", token), "items": n}


@router.get("/clients/{client_id}/confirmations", response_model=list[ConfirmationOut])
def list_confirmations(client_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    get_client(db, access, client_id)
    return confirmations.list_for_client(db, client_id)


# --- billing settings and rate card -----------------------------------------------------

@router.put("/billing", response_model=BillingSettings)
def set_billing(body: BillingSettings, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    require_owner(access)
    org = db.get(Organization, access.org_id)
    org.currency, org.default_tax_percent = body.currency, body.default_tax_percent
    audit.append(db, org_id=access.org_id, actor_user_id=access.user.id, action="billing_updated",
                 note=f"Billing set to {body.currency}, default tax {body.default_tax_percent}%")
    db.commit()
    return body


@router.get("/rate-items", response_model=list[RateItemOut])
def list_rate_items(access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    return db.execute(select(RateItem).where(RateItem.org_id == access.org_id).order_by(RateItem.name)).scalars().all()


@router.post("/rate-items", response_model=RateItemOut, status_code=201)
def add_rate_item(body: RateItemIn, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    require_owner(access)
    ri = RateItem(org_id=access.org_id, **body.model_dump())
    db.add(ri)
    db.flush()
    audit.append(db, org_id=access.org_id, actor_user_id=access.user.id, action="rate_item_added", entity_type="rate_item",
                 entity_id=str(ri.id), note=f"Rate card: {ri.name} at {ri.unit_price} per {ri.unit}")
    db.commit()
    return ri


@router.put("/rate-items/{item_id}", response_model=RateItemOut)
def update_rate_item(item_id: uuid.UUID, body: RateItemIn, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    require_owner(access)
    ri = db.execute(select(RateItem).where(RateItem.id == item_id, RateItem.org_id == access.org_id)).scalar_one_or_none()
    if ri is None:
        raise HTTPException(404, "Rate item not found.")
    for k, v in body.model_dump().items():
        setattr(ri, k, v)
    audit.append(db, org_id=access.org_id, actor_user_id=access.user.id, action="rate_item_updated", entity_type="rate_item",
                 entity_id=str(ri.id), note=f"Rate card updated: {ri.name} at {ri.unit_price} per {ri.unit}")
    db.commit()
    return ri


# --- change orders -------------------------------------------------------------------------

def co_out(db: Session, co: ChangeOrder, link: str | None = None) -> dict:
    d = {c: getattr(co, c) for c in ("id", "client_id", "number", "title", "notes", "status", "currency", "tax_percent",
                                     "subtotal", "tax", "total", "sent_at", "expires_at", "decided_at", "decided_by_name",
                                     "decision_comment", "created_at")}
    d["total_text"] = co_svc.money(co.total, co.currency)
    d["lines"] = co_svc.lines_of(db, co.id)
    d["link"] = link
    return d


@router.get("/clients/{client_id}/change-orders", response_model=list[ChangeOrderOut])
def list_change_orders(client_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    get_client(db, access, client_id)
    cos = db.execute(select(ChangeOrder).where(ChangeOrder.client_id == client_id).order_by(ChangeOrder.number.desc())).scalars().all()
    return [co_out(db, c) for c in cos]


@router.post("/clients/{client_id}/change-orders", response_model=ChangeOrderOut, status_code=201)
def create_change_order(client_id: uuid.UUID, body: ChangeOrderIn, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    """Draft a priced change order, usually from out-of-scope requests (link them with record_id)."""
    return co_out(db, co_svc.create(db, access, get_client(db, access, client_id), body))


@router.get("/change-orders/{co_id}", response_model=ChangeOrderOut)
def read_change_order(co_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    return co_out(db, co_svc.get_change_order(db, access, co_id))


@router.patch("/change-orders/{co_id}", response_model=ChangeOrderOut)
def edit_change_order(co_id: uuid.UUID, body: ChangeOrderPatch, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    return co_out(db, co_svc.update_draft(db, access, co_svc.get_change_order(db, access, co_id, lock=True), body))


@router.post("/change-orders/{co_id}/send", response_model=ChangeOrderOut)
def send_change_order(co_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    """Freeze the terms and create the approval link for the client (shown only once)."""
    co, token = co_svc.send(db, access, co_svc.get_change_order(db, access, co_id, lock=True))
    return co_out(db, co, confirmations.link("change-order", token))


@router.post("/change-orders/{co_id}/withdraw", response_model=ChangeOrderOut)
def withdraw_change_order(co_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    return co_out(db, co_svc.withdraw(db, access, co_svc.get_change_order(db, access, co_id, lock=True)))


@router.get("/clients/{client_id}/scope-value", response_model=ScopeValueOut)
def scope_value(client_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    """Extra work requested and billed through change orders."""
    get_client(db, access, client_id)
    cur = db.get(Organization, access.org_id).currency
    v = co_svc.scope_value(db, client_id)
    return {"currency": cur, **v, "approved_text": co_svc.money(v["approved_total"], cur),
            "pending_text": co_svc.money(v["pending_total"], cur)}


# --- audio -----------------------------------------------------------------------------------

@router.post("/clients/{client_id}/audio", response_model=TranscriptionJobOut, status_code=202)
async def upload_audio(client_id: uuid.UUID, request: Request, background: BackgroundTasks,
                       file: UploadFile = File(...), title: str = Form(..., min_length=1, max_length=200),
                       call_date: date = Form(...), consent: bool = Form(...), language: str = Form(""),
                       extract: bool = Form(False), access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    """Upload a call recording or voice note. It's transcribed in the background and deleted afterwards.
    Leave extract off to name the speakers first, then start reading the call."""
    client = get_client(db, access, client_id)
    if not consent:
        raise HTTPException(422, "Confirm that everyone on the call knew it was recorded.")
    if request.app.state.transcriber_factory() is None:
        raise HTTPException(503, {"code": "misconfigured", "message": "Audio transcription isn't set up on this server."})
    path, sha, size = await transcription.save_upload(file)
    job = transcription.create_job(db, org_id=access.org_id, client_id=client.id, user_id=access.user.id, title=title,
                                   call_date=call_date, consent=consent, language=language, sha=sha, size=size,
                                   content_type=(file.content_type or "").split(";")[0], extract_after=extract)
    llm_factory, model, user_id = request.app.state.llm_factory, request.app.state.llm_model, access.user.id

    def after_call(call_id: uuid.UUID) -> None:
        from ..db import SessionLocal
        from ..models import Call
        s = SessionLocal()
        try:
            run = jobs.start_run(s, s.get(Call, call_id), user_id, model)
            run_id = run.id
        finally:
            s.close()
        jobs.process_run(run_id, llm_factory)

    background.add_task(transcription.process_job, job.id, path, request.app.state.transcriber_factory, after_call)
    return job


@router.get("/transcriptions/{job_id}", response_model=TranscriptionJobOut)
def read_transcription(job_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    job = db.execute(select(TranscriptionJob).where(TranscriptionJob.id == job_id,
                                                    TranscriptionJob.org_id == access.org_id)).scalar_one_or_none()
    if job is None:
        raise HTTPException(404, "Not found.")
    return job


@router.put("/calls/{call_id}/speakers", response_model=CallOut)
def name_speakers(call_id: uuid.UUID, body: SpeakersIn, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    """Map speaker labels to names, e.g. {"Speaker A": "Meera"}. The stored transcript isn't changed."""
    return services.set_speakers(db, access, services.get_call(db, access, call_id), body.speakers)
