"""Clients, calls, review, brief and the AI features."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, jobs, services
from ..auth import OrgAccess, get_client, org_access, require_owner
from ..db import get_db
from ..llm import LLMError
from ..models import AuditEvent, Call, Client, ExtractionRun, Record, TranscriptLine
from ..schemas import (AcceptBatchIn, AcceptBatchOut, AcceptIn, AskIn, AskOut, AuditOut, BriefOut, CallCreated, CallIn,
                       CallOut, ClientIn, ClientOut, ClientPatch, LineOut, ManualRecordIn, MergeTopicsIn, QualityOut,
                       RecapIn, RecapOut, RecordOut, RecordPatch, RunOut)

router = APIRouter(prefix="/v1/orgs/{org_id}", tags=["clients"])

LLM_STATUS = {"budget_exceeded": 402, "rate_limited": 429, "too_large": 413, "invalid_json": 502,
              "refused": 422, "unavailable": 503, "misconfigured": 503}


def llm_http(e: LLMError) -> HTTPException:
    return HTTPException(LLM_STATUS.get(e.code, 502), {"code": e.code, "message": str(e)})


# --- clients -------------------------------------------------------------------------

@router.get("/clients", response_model=list[ClientOut])
def list_clients(access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    return db.execute(select(Client).where(Client.org_id == access.org_id).order_by(Client.name)).scalars().all()


@router.post("/clients", response_model=ClientOut, status_code=201)
def create_client(body: ClientIn, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    c = Client(org_id=access.org_id, name=body.name.strip(), people=body.people.strip(), sow=body.sow.strip())
    db.add(c)
    db.flush()
    audit.append(db, org_id=access.org_id, client_id=c.id, actor_user_id=access.user.id, action="client_added",
                 entity_type="client", entity_id=str(c.id), note=f"Client “{c.name}” added", after={"sow": c.sow, "people": c.people})
    db.commit()
    return c


@router.get("/clients/{client_id}", response_model=ClientOut)
def read_client(client_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    return get_client(db, access, client_id)


@router.patch("/clients/{client_id}", response_model=ClientOut)
def update_client(client_id: uuid.UUID, body: ClientPatch, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    c = get_client(db, access, client_id)
    changes = body.model_dump(exclude_none=True)
    before = {k: getattr(c, k) for k in changes}
    for k, v in changes.items():
        setattr(c, k, v.strip())
    after = {k: getattr(c, k) for k in changes}
    if before != after:
        audit.append(db, org_id=access.org_id, client_id=c.id, actor_user_id=access.user.id, action="client_updated",
                     entity_type="client", entity_id=str(c.id),
                     note="Scope of work updated" if "sow" in changes and before.get("sow") != after.get("sow") else "Client details updated",
                     before=before, after=after)
    db.commit()
    return c


@router.delete("/clients/{client_id}", status_code=204)
def erase_client(client_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    """Owner only. Deletes the client, its calls and records; redacts its audit entries."""
    require_owner(access)
    services.erase_client(db, access, get_client(db, access, client_id))
    return Response(status_code=204)


# --- calls ---------------------------------------------------------------------------

@router.get("/clients/{client_id}/calls", response_model=list[CallOut])
def list_calls(client_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    get_client(db, access, client_id)
    return db.execute(select(Call).where(Call.client_id == client_id).order_by(Call.call_date, Call.created_at)).scalars().all()


@router.post("/clients/{client_id}/calls", response_model=CallCreated, status_code=201)
def add_call(client_id: uuid.UUID, body: CallIn, request: Request, background: BackgroundTasks,
             access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    client = get_client(db, access, client_id)
    call = services.create_call(db, access, client, body.title, body.call_date, body.transcript, body.consent)
    run = None
    if body.extract:
        run = jobs.start_run(db, call, access.user.id, request.app.state.llm_model)
        background.add_task(jobs.process_run, run.id, request.app.state.llm_factory)
    return {"call": call, "run": run}


@router.get("/calls/{call_id}", response_model=CallOut)
def read_call(call_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    return services.get_call(db, access, call_id)


@router.get("/calls/{call_id}/lines", response_model=list[LineOut])
def read_lines(call_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    services.get_call(db, access, call_id)
    rows = db.execute(select(TranscriptLine).where(TranscriptLine.call_id == call_id).order_by(TranscriptLine.idx)).scalars()
    return [LineOut(idx=r.idx, t=r.t, speaker=r.speaker, text=r.text) for r in rows]


@router.post("/calls/{call_id}/extract", response_model=RunOut, status_code=202)
def extract(call_id: uuid.UUID, request: Request, background: BackgroundTasks,
            access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    """Read (or re-read) a call. Reviewed records are kept; unreviewed findings are replaced."""
    call = services.get_call(db, access, call_id)
    run = jobs.start_run(db, call, access.user.id, request.app.state.llm_model)
    background.add_task(jobs.process_run, run.id, request.app.state.llm_factory)
    return run


@router.get("/runs/{run_id}", response_model=RunOut)
def read_run(run_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    run = db.execute(select(ExtractionRun).where(ExtractionRun.id == run_id, ExtractionRun.org_id == access.org_id)).scalar_one_or_none()
    if run is None:
        raise HTTPException(404, "Run not found.")
    return run


@router.delete("/calls/{call_id}", status_code=204)
def delete_call(call_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    services.delete_call(db, access, services.get_call(db, access, call_id))
    return Response(status_code=204)


@router.post("/calls/{call_id}/recap", response_model=RecapOut)
def recap(call_id: uuid.UUID, body: RecapIn, request: Request, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    call = services.get_call(db, access, call_id)
    client = get_client(db, access, call.client_id)
    try:
        return services.recap(db, access, call, client, body.signer, body.agency, request.app.state.llm_factory())
    except LLMError as e:
        raise llm_http(e) from e


# --- records & review ------------------------------------------------------------------

@router.get("/clients/{client_id}/records", response_model=list[RecordOut])
def list_records(client_id: uuid.UUID, state: Literal["pending", "accepted", "all"] = Query("all"),
                 access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    get_client(db, access, client_id)
    q = select(Record).join(Call, Call.id == Record.call_id).where(Record.client_id == client_id)
    if state != "all":
        q = q.where(Record.reviewed.is_(state == "accepted"))
    rows = db.execute(q.order_by(Call.call_date, Record.line_idx)).scalars().all()
    return [services.record_out(r) for r in rows]


@router.post("/clients/{client_id}/records", response_model=RecordOut, status_code=201)
def add_missed_record(client_id: uuid.UUID, body: ManualRecordIn, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    """Add something the AI missed. Counted separately in quality metrics."""
    return services.record_out(services.add_manual(db, access, get_client(db, access, client_id), body))


@router.post("/records/{record_id}/accept", response_model=RecordOut)
def accept(record_id: uuid.UUID, body: AcceptIn, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    rec = services.get_record(db, access, record_id, lock=True)
    return services.record_out(services.accept(db, access, rec, body.confirm_flagged))


@router.post("/clients/{client_id}/records/accept", response_model=AcceptBatchOut)
def accept_batch(client_id: uuid.UUID, body: AcceptBatchIn, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    """Accept several records. Records that failed a check are skipped and must be accepted one by one."""
    done, skipped = services.accept_batch(db, access, get_client(db, access, client_id), body.ids)
    return {"accepted": done, "skipped_need_check": skipped}


@router.patch("/records/{record_id}", response_model=RecordOut)
def edit_record(record_id: uuid.UUID, body: RecordPatch, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    rec = services.get_record(db, access, record_id, lock=True)
    return services.record_out(services.edit(db, access, rec, body.model_dump(exclude_unset=True)))


@router.post("/records/{record_id}/discard", status_code=204)
def discard(record_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    services.discard(db, access, services.get_record(db, access, record_id, lock=True))
    return Response(status_code=204)


@router.post("/records/{record_id}/replaced", response_model=RecordOut)
def mark_replaced(record_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    return services.record_out(services.set_replaced(db, access, services.get_record(db, access, record_id, lock=True), True))


@router.post("/records/{record_id}/restore", response_model=RecordOut)
def restore(record_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    return services.record_out(services.set_replaced(db, access, services.get_record(db, access, record_id, lock=True), False))


@router.delete("/records/{record_id}", status_code=204)
def delete_record(record_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    services.delete_record(db, access, services.get_record(db, access, record_id, lock=True))
    return Response(status_code=204)


@router.post("/clients/{client_id}/topics/merge")
def merge_topics(client_id: uuid.UUID, body: MergeTopicsIn, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    return {"updated": services.merge_topics(db, access, get_client(db, access, client_id), body.from_topic, body.to_topic)}


# --- brief, ask, quality, audit, export --------------------------------------------------

@router.get("/clients/{client_id}/brief", response_model=BriefOut)
def brief(client_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    return services.brief(db, get_client(db, access, client_id))


@router.post("/clients/{client_id}/ask", response_model=AskOut)
def ask(client_id: uuid.UUID, body: AskIn, request: Request, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    try:
        return services.ask(db, access, get_client(db, access, client_id), body.question.strip(), request.app.state.llm_factory())
    except LLMError as e:
        raise llm_http(e) from e


@router.get("/clients/{client_id}/quality", response_model=QualityOut)
def quality(client_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    return services.quality(db, get_client(db, access, client_id))


@router.get("/clients/{client_id}/audit", response_model=list[AuditOut])
def client_audit(client_id: uuid.UUID, limit: int = Query(100, ge=1, le=500), before_id: int | None = None,
                 access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    get_client(db, access, client_id)
    q = select(AuditEvent).where(AuditEvent.org_id == access.org_id, AuditEvent.client_id == client_id)
    if before_id:
        q = q.where(AuditEvent.id < before_id)
    return db.execute(q.order_by(AuditEvent.id.desc()).limit(limit)).scalars().all()


@router.get("/clients/{client_id}/export", response_class=PlainTextResponse)
def export(client_id: uuid.UUID, access: OrgAccess = Depends(org_access), db: Session = Depends(get_db)):
    client = get_client(db, access, client_id)
    md = services.export_markdown(db, client)
    audit.append(db, org_id=access.org_id, client_id=client.id, actor_user_id=access.user.id, action="client_exported",
                 entity_type="client", entity_id=str(client.id), note="Exported the client record")
    db.commit()
    safe = "".join(ch for ch in client.name if ch.isalnum() or ch in " -").strip().replace(" ", "-").lower() or "client"
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{safe}-record.md"'})
