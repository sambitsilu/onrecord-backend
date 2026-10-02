"""Running extraction for a call and saving the results.

A run is created (queued), then processed in the background. Results replace
the call's previous *unreviewed* findings in one transaction; reviewed records
are never touched. Failures are recorded on the run and in the audit log.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from . import audit
from .config import get_settings
from .db import session_scope
from .extraction import PROMPT_VERSION, CallCtx, ClientCtx, PriorRecord, run_extraction
from .llm import LLM, LLMError, check_budget, record_usage
from .models import Call, Client, ExtractionRun, Record, TranscriptLine
from .transcripts import Line

log = logging.getLogger("onrecord.jobs")


def load_lines(db: Session, call_id: uuid.UUID, raw: bool = False) -> list[Line]:
    """Transcript lines, with speaker labels mapped to names (e.g. "Speaker A" -> "Meera") unless raw."""
    rows = db.execute(select(TranscriptLine).where(TranscriptLine.call_id == call_id).order_by(TranscriptLine.idx)).scalars().all()
    names = {} if raw else ((db.execute(select(Call.speaker_map).where(Call.id == call_id)).scalar_one_or_none()) or {})
    return [Line(t=r.t, speaker=names.get(r.speaker, r.speaker), text=r.text) for r in rows]


def fail_stale_runs(db: Session) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=get_settings().stale_run_minutes)
    stale = db.execute(select(ExtractionRun).where(ExtractionRun.status.in_(("queued", "running")),
                                                   ExtractionRun.created_at < cutoff)).scalars().all()
    for r in stale:
        r.status, r.error, r.finished_at = "failed", "Stopped before finishing (server restarted or timed out). Run it again.", datetime.now(timezone.utc)
        db.execute(update(Call).where(Call.id == r.call_id, Call.status == "processing").values(status="failed"))
    db.commit()
    return len(stale)


def start_run(db: Session, call: Call, user_id: uuid.UUID | None, model: str) -> ExtractionRun:
    fail_stale_runs(db)
    busy = db.execute(select(ExtractionRun.id).where(ExtractionRun.call_id == call.id,
                                                     ExtractionRun.status.in_(("queued", "running")))).first()
    if busy:
        raise HTTPException(409, "This call is already being read.")
    run = ExtractionRun(org_id=call.org_id, call_id=call.id, status="queued", model=model,
                        prompt_version=PROMPT_VERSION, created_by=user_id)
    db.add(run)
    call.status = "processing"
    db.commit()
    return run


def _prior_records(db: Session, call: Call) -> tuple[list[PriorRecord], list[tuple[str, int | None]]]:
    rows = db.execute(
        select(Record, Call.call_date).join(Call, Call.id == Record.call_id)
        .where(Record.client_id == call.client_id, Record.status == "current")
        .order_by(Call.call_date, Record.line_idx)
    ).all()
    prior, kept_here = [], []
    for rec, cdate in rows:
        if rec.call_id == call.id:
            if rec.reviewed:
                kept_here.append((rec.type, rec.line_idx))
                prior.append(PriorRecord(str(rec.id), rec.type, rec.topic, rec.value, rec.text, cdate.isoformat(), True))
        elif cdate <= call.call_date:
            prior.append(PriorRecord(str(rec.id), rec.type, rec.topic, rec.value, rec.text, cdate.isoformat(), False))
    return prior[-120:], kept_here


def process_run(run_id: uuid.UUID, llm_factory: Callable[[], LLM]) -> None:
    """Background entry point. Opens its own sessions."""
    with session_scope() as db:
        run = db.get(ExtractionRun, run_id)
        if run is None or run.status != "queued":
            return
        run.status, run.started_at = "running", datetime.now(timezone.utc)
    try:
        with session_scope() as db:
            run = db.get(ExtractionRun, run_id)
            call = db.get(Call, run.call_id)
            client = db.get(Client, call.client_id)
            check_budget(db, run.org_id)
            prior, kept_here = _prior_records(db, call)
            llm = llm_factory()
            result = run_extraction(
                llm, ClientCtx(client.name, client.people, client.sow),
                CallCtx(call.title, call.call_date, load_lines(db, call.id)), prior,
                id_gen=lambda: str(uuid.uuid4()), kept_here=kept_here, chunk_chars=get_settings().chunk_chars)
            # Replace previous unreviewed AI findings for this call, atomically with the new ones.
            db.execute(delete(Record).where(Record.call_id == call.id, Record.reviewed.is_(False)))
            for r in result.records:
                db.add(Record(
                    id=uuid.UUID(r["id"]), org_id=call.org_id, client_id=call.client_id, call_id=call.id, run_id=run.id,
                    source="ai", type=r["type"], topic=r["topic"], value=r["value"], text=r["text"], who=r["who"],
                    owner=r["owner"], due_text=r["due_text"], due_date=r["due_date"], line_idx=r["line_idx"], t=r["t"],
                    quote=r["quote"], verified=r["verified"], numbers_ok=r["numbers_ok"], role_ok=r["role_ok"],
                    confidence=r["confidence"], outside_scope=r["outside_scope"], scope_note=r["scope_note"],
                    replaces=[uuid.UUID(x) for x in r["replaces"]], reviewed=False, original=r["original"]))
            record_usage(db, run.org_id, "extract", llm.model, result.usage)
            run.status, run.finished_at = "succeeded", datetime.now(timezone.utc)
            run.created_count, run.flagged_count = len(result.records), result.flagged
            run.input_tokens, run.output_tokens = result.usage.input_tokens, result.usage.output_tokens
            call.status, call.summary = "processed", " ".join(result.summaries)[:1200]
            audit.append(db, org_id=call.org_id, client_id=call.client_id, actor_user_id=run.created_by,
                         action="call_read", entity_type="call", entity_id=str(call.id),
                         note=f"Read “{call.title}”: {len(result.records)} new records for review"
                              + (f", {result.flagged} need a closer look" if result.flagged else ""),
                         after={"run_id": str(run.id), "model": llm.model, "prompt_version": PROMPT_VERSION})
    except Exception as e:  # noqa: BLE001 - every failure must be recorded on the run
        code = e.code if isinstance(e, LLMError) else "internal"
        message = str(e) if isinstance(e, LLMError) else "Something went wrong while reading the call."
        if not isinstance(e, LLMError):
            log.exception("extraction run %s failed", run_id)
        with session_scope() as db:
            run = db.get(ExtractionRun, run_id)
            run.status, run.error, run.finished_at = "failed", f"{code}: {message}", datetime.now(timezone.utc)
            spent = getattr(e, "usage", None)
            if spent and (spent.input_tokens or spent.output_tokens):
                record_usage(db, run.org_id, "extract_failed", run.model, spent)
                run.input_tokens, run.output_tokens = spent.input_tokens, spent.output_tokens
            call = db.get(Call, run.call_id)
            has_reviewed = db.execute(select(Record.id).where(Record.call_id == call.id)).first()
            call.status = "processed" if has_reviewed else "failed"
            audit.append(db, org_id=call.org_id, client_id=call.client_id, actor_user_id=run.created_by,
                         action="call_read_failed", entity_type="call", entity_id=str(call.id),
                         note=f"Reading “{call.title}” failed: {message}")
