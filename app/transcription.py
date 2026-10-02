"""Audio upload and transcription (phone calls, voice notes, recordings).

The audio is kept only until transcription finishes, then deleted; its SHA-256
fingerprint is recorded. Speakers come back as "Speaker A", "Speaker B"; the agency
can map them to real names afterwards without changing the stored transcript.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from . import audit
from .config import get_settings
from .db import session_scope
from .models import Call, Client, TranscriptionJob
from .transcripts import Line

log = logging.getLogger("onrecord.transcription")

AUDIO_TYPES = {"audio/mpeg", "audio/mp3", "audio/mp4", "audio/x-m4a", "audio/m4a", "audio/aac", "audio/wav",
               "audio/x-wav", "audio/wave", "audio/ogg", "audio/opus", "audio/webm", "audio/flac", "audio/amr",
               "audio/3gpp", "video/mp4", "video/webm", "video/quicktime"}


class TranscriptionError(Exception):
    pass


class Transcriber(Protocol):
    name: str

    def transcribe(self, path: str, language: str = "") -> tuple[list[Line], int | None]: ...


def _ts(ms: int | None) -> str | None:
    if ms is None:
        return None
    s = int(ms) // 1000
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


class AssemblyAITranscriber:
    """AssemblyAI REST API: upload, request a transcript with speaker labels, poll, then delete it."""
    name = "assemblyai"
    BASE = "https://api.assemblyai.com/v2"

    def __init__(self, api_key: str | None = None, poll_seconds: float = 3.0, timeout_seconds: int = 3600):
        import requests

        self._rq = requests
        self.key = api_key or get_settings().assemblyai_api_key
        self.poll, self.timeout = poll_seconds, timeout_seconds

    def _h(self) -> dict:
        return {"authorization": self.key}

    def transcribe(self, path: str, language: str = "") -> tuple[list[Line], int | None]:
        rq = self._rq
        with open(path, "rb") as f:
            up = rq.post(f"{self.BASE}/upload", headers=self._h(), data=f, timeout=600)
        if up.status_code in (401, 403):
            raise TranscriptionError("The transcription service rejected the API key.")
        up.raise_for_status()
        body = {"audio_url": up.json()["upload_url"], "speaker_labels": True}
        if language:
            body["language_code"] = language
        else:
            body["language_detection"] = True
        tr = rq.post(f"{self.BASE}/transcript", headers=self._h(), json=body, timeout=60)
        tr.raise_for_status()
        tid = tr.json()["id"]
        deadline = time.time() + self.timeout
        try:
            while True:
                r = rq.get(f"{self.BASE}/transcript/{tid}", headers=self._h(), timeout=60)
                r.raise_for_status()
                d = r.json()
                if d.get("status") == "completed":
                    break
                if d.get("status") == "error":
                    raise TranscriptionError(f"Transcription failed: {d.get('error') or 'unknown error'}")
                if time.time() > deadline:
                    raise TranscriptionError("Transcription took too long.")
                time.sleep(self.poll)
            utts = d.get("utterances") or []
            lines = [Line(t=_ts(u.get("start")), speaker=f"Speaker {u.get('speaker')}" if u.get("speaker") else "",
                          text=(u.get("text") or "").strip()) for u in utts if (u.get("text") or "").strip()]
            if not lines and (d.get("text") or "").strip():
                lines = [Line(t=None, speaker="", text=d["text"].strip())]
            dur = d.get("audio_duration")
            return lines, int(dur) if dur else None
        finally:
            try:  # don't leave client audio or transcripts with the provider
                rq.delete(f"{self.BASE}/transcript/{tid}", headers=self._h(), timeout=30)
            except Exception:  # noqa: BLE001
                log.warning("could not delete transcript %s at provider", tid)


def default_transcriber() -> Transcriber | None:
    s = get_settings()
    if s.transcription_provider == "assemblyai":
        return AssemblyAITranscriber()
    return None


async def save_upload(upload: UploadFile) -> tuple[str, str, int]:
    """Stream the upload to a private temp file, enforcing the size limit. Returns (path, sha256, bytes)."""
    s = get_settings()
    ctype = (upload.content_type or "").split(";")[0].strip().lower()
    if ctype not in AUDIO_TYPES:
        raise HTTPException(415, "Upload an audio file (MP3, M4A, WAV, OGG, WebM, AMR or MP4).")
    os.makedirs(s.audio_tmp_dir, mode=0o700, exist_ok=True)
    path = os.path.join(s.audio_tmp_dir, uuid.uuid4().hex)
    h, n = hashlib.sha256(), 0
    try:
        with open(path, "wb") as out:
            while chunk := await upload.read(1024 * 1024):
                n += len(chunk)
                if n > s.max_audio_bytes:
                    raise HTTPException(413, f"The file is too large (limit {s.max_audio_bytes // 1_000_000} MB).")
                h.update(chunk)
                out.write(chunk)
    except BaseException:
        if os.path.exists(path):
            os.remove(path)
        raise
    if n == 0:
        os.remove(path)
        raise HTTPException(422, "The file is empty.")
    return path, h.hexdigest(), n


def process_job(job_id: uuid.UUID, path: str, transcriber_factory: Callable[[], Transcriber | None],
                after_call: Callable[[uuid.UUID], None] | None = None) -> None:
    """Background entry point: transcribe, create the call, optionally start reading it. Always deletes the audio."""
    from .services import insert_call  # local import to avoid a cycle

    try:
        with session_scope() as db:
            job = db.get(TranscriptionJob, job_id)
            job.status = "running"
        transcriber = transcriber_factory()
        if transcriber is None:
            raise TranscriptionError("Audio transcription isn't set up on this server.")
        with session_scope() as db:
            job = db.get(TranscriptionJob, job_id)
            language = job.language
        lines, duration = transcriber.transcribe(path, language)
        if not lines:
            raise TranscriptionError("No speech was found in the recording.")
        with session_scope() as db:
            job = db.get(TranscriptionJob, job_id)
            client = db.get(Client, job.client_id)
            call = insert_call(db, org_id=job.org_id, client=client, user_id=job.created_by, title=job.title,
                               call_date=job.call_date, lines=lines, source="audio",
                               note_extra=f", transcribed from audio (fingerprint {job.audio_sha256[:12]})")
            job.status, job.call_id, job.duration_sec = "succeeded", call.id, duration
            job.finished_at = datetime.now(timezone.utc)
            extract, call_id = job.extract_after, call.id
        if extract and after_call:
            after_call(call_id)
    except Exception as e:  # noqa: BLE001 - record every failure on the job
        msg = str(e) if isinstance(e, (TranscriptionError, HTTPException)) else "Transcription failed. Try again."
        if not isinstance(e, TranscriptionError):
            log.exception("transcription job %s failed", job_id)
        with session_scope() as db:
            job = db.get(TranscriptionJob, job_id)
            job.status, job.error, job.finished_at = "failed", msg, datetime.now(timezone.utc)
            audit.append(db, org_id=job.org_id, client_id=job.client_id, actor_user_id=job.created_by,
                         action="transcription_failed", entity_type="transcription", entity_id=str(job.id),
                         note=f"Transcribing “{job.title}” failed: {msg}")
    finally:
        if os.path.exists(path):
            os.remove(path)


def create_job(db: Session, *, org_id, client_id, user_id, title, call_date, consent, language, sha, size,
               content_type, extract_after) -> TranscriptionJob:
    if not consent:
        raise HTTPException(422, "Confirm that everyone on the call knew it was recorded.")
    job = TranscriptionJob(org_id=org_id, client_id=client_id, title=title.strip()[:200], call_date=call_date, consent=True,
                           language=(language or "").strip()[:10], provider=get_settings().transcription_provider,
                           status="queued", audio_sha256=sha, audio_bytes=size, content_type=content_type,
                           extract_after=extract_after, created_by=user_id)
    db.add(job)
    db.flush()
    audit.append(db, org_id=org_id, client_id=client_id, actor_user_id=user_id, action="audio_uploaded",
                 entity_type="transcription", entity_id=str(job.id),
                 note=f"Uploaded audio for “{job.title}” ({size // 1000} KB, fingerprint {sha[:12]}) for transcription")
    db.commit()
    return job


def speaker_names(call: Call) -> dict:
    return dict(call.speaker_map or {})
