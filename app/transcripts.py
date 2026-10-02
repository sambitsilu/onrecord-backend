"""Transcript parsing and chunking.

Accepts Google Meet / Zoom / Teams exports, WebVTT, SRT and plain
"[00:04:12] Name: text" lines. Lines without timestamps are kept, so typed
notes and voice-note summaries work too.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass


@dataclass
class Line:
    t: str | None  # "hh:mm:ss" or None
    speaker: str  # "" when unknown
    text: str

    def as_dict(self) -> dict:
        return asdict(self)


_CUE = re.compile(r"^((?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d+)?)\s*-->")
_TS = re.compile(r"^\(?\[?((?:\d{1,2}:)?\d{1,2}:\d{2})(?:[.,]\d+)?\]?\)?\s*[-–:]?\s*(.*)$")
_VOICE = re.compile(r"^<v\s+([^>]+)>(.*?)(?:</v>)?$")
# Speaker names: start with a letter, up to ~40 characters without a colon
# (allows Devanagari and other scripts whose vowel signs aren't "word" characters).
_SPEAKER = re.compile(r"^([^\W\d_][^:\n\[\]]{0,40}?):\s+(.+)$", re.UNICODE)
_TAG = re.compile(r"<[^>]+>")


def norm_time(s: str | None) -> str | None:
    if not s:
        return None
    s = s.replace(",", ".").split(".")[0]
    try:
        parts = [int(p) for p in s.split(":")]
    except ValueError:
        return None
    while len(parts) < 3:
        parts.insert(0, 0)
    return ":".join(f"{p:02d}" for p in parts[-3:])


def parse_transcript(raw: str) -> list[Line]:
    out: list[Line] = []
    pending_t: str | None = None
    for line in (raw or "").replace("\r", "").split("\n"):
        line = line.strip()
        if not line or line == "WEBVTT" or line.isdigit() or line.startswith("NOTE"):
            continue
        cue = _CUE.match(line)
        if cue:
            pending_t = norm_time(cue.group(1))
            continue
        t: str | None = None
        rest = line
        ts = _TS.match(line)
        if ts:
            t, rest = norm_time(ts.group(1)), ts.group(2)
        if not t and pending_t:
            t = pending_t
        pending_t = None
        speaker = ""
        voice = _VOICE.match(rest)
        if voice:
            speaker, rest = voice.group(1).strip(), voice.group(2)
        else:
            sp = _SPEAKER.match(rest)
            if sp:
                speaker, rest = sp.group(1).strip(), sp.group(2)
        rest = _TAG.sub("", rest).strip()
        if rest:
            out.append(Line(t=t, speaker=speaker, text=rest))
    return out


def chunk_ranges(lines: list[Line], max_chars: int = 60_000, overlap: int = 3) -> list[tuple[int, int]]:
    """Split a long transcript into overlapping [start, end) ranges."""
    if not lines:
        return []

    def cost(ln: Line) -> int:
        return len(ln.text) + len(ln.speaker) + 24

    out: list[tuple[int, int]] = []
    start, size = 0, 0
    for i, ln in enumerate(lines):
        if size + cost(ln) > max_chars and i > start:
            out.append((start, i))
            start = max(0, i - overlap)
            size = sum(cost(lines[j]) for j in range(start, i))
        size += cost(ln)
    out.append((start, len(lines)))
    return out


def lines_text(lines: list[Line], start: int, end: int) -> str:
    rows = []
    for i in range(start, end):
        ln = lines[i]
        rows.append(f"L{i}" + (f" [{ln.t}]" if ln.t else "") + " " + (f"{ln.speaker}: " if ln.speaker else "") + ln.text)
    return "\n".join(rows) + ("\n" if rows else "")


def short_time(t: str | None) -> str:
    if not t:
        return ""
    return t[3:] if t.startswith("00:") else t
