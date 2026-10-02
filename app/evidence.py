"""Checks done in code, never by the AI.

Every AI finding is checked against the transcript before a person sees it:
the quote must really appear, numbers must have been said, and approvals must
come from someone on the client's side. Failing checks become review flags.
"""
from __future__ import annotations

import re
import unicodedata

from .transcripts import Line

_NON_WORD = re.compile(r"[^\w\s]|_", re.UNICODE)
_SPACES = re.compile(r"\s+")
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_YEAR = re.compile(r"^(19|20)\d\d$")

VERIFY_THRESHOLD = 0.8


def norm_txt(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = _NON_WORD.sub(" ", s)
    return _SPACES.sub(" ", s).strip()


def toks(s: str | None) -> list[str]:
    n = norm_txt(s)
    return n.split(" ") if n else []


def coverage(quote: str, text: str) -> float:
    q = toks(quote)
    if not q:
        return 0.0
    have = set(toks(text))
    return sum(1 for t in q if t in have) / len(q)


def verify_evidence(lines: list[Line], line: int | None, quote: str) -> tuple[int | None, bool]:
    """Return (best line index, verified). Prefers the line the AI pointed at."""
    if not quote or not lines:
        return line, False
    n = len(lines)
    near = [] if line is None else [i for i in (line, line - 1, line + 1) if 0 <= i < n]
    order = near + [i for i in range(n) if i not in near]

    def one(i: int) -> str:
        return lines[i].text

    def two(i: int) -> str:
        return " ".join(ln.text for ln in lines[i:i + 2])

    for span in (one, two):
        best, best_score = None, 0.0
        for i in order:
            c = coverage(quote, span(i))
            if c > best_score + 1e-9:
                best, best_score = i, c
        if best_score >= VERIFY_THRESHOLD:
            return best, True
    return line, False


def numbers_in(s: str | None) -> list[str]:
    out = []
    for m in _NUM.findall(s or ""):
        m = m.replace(",", "")
        if not _YEAR.match(m):
            out.append(m)
    return out


def numbers_backed(lines: list[Line], line: int | None, value: str, text: str) -> bool:
    nums = set(numbers_in(value or text))
    if not nums:
        return True
    if line is None:
        return False
    ctx = " ".join(ln.text for ln in lines[max(0, line - 2): line + 3])
    return nums <= set(numbers_in(ctx))


def client_people(people: str | None) -> list[str]:
    return [p.strip().lower() for p in (people or "").split(",") if p.strip()]


def said_by_client(people: str | None, who: str | None) -> bool | None:
    names = client_people(people)
    if not names or not who:
        return None
    w = who.lower().strip()
    w_first = w.split(" ")[0]
    return any(p in w or w in p or p.split(" ")[0] == w_first for p in names)


def reasons_for(rec) -> list[str]:
    def get(k: str):
        return rec.get(k) if isinstance(rec, dict) else getattr(rec, k, None)

    r = []
    if get("verified") is False:
        r.append("The quote couldn't be found in the transcript. Read the call before accepting.")
    if get("numbers_ok") is False:
        r.append("A number or date here isn't in what was said nearby. Check it.")
    if get("role_ok") is False:
        r.append("This approval wasn't said by anyone listed on the client's side.")
    if get("confidence") == "low":
        r.append("The AI wasn't sure this was clearly agreed.")
    return r


def needs_check(rec) -> bool:
    return bool(reasons_for(rec))
