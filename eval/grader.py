"""Scores extraction output against known right answers (eval/cases.json)."""
from __future__ import annotations

import json
from pathlib import Path

from app.evidence import needs_check, norm_txt, reasons_for

CASES_PATH = Path(__file__).with_name("cases.json")
KIND = {"decision": "Decision", "approval": "Approval", "action": "Action item",
        "scope_change": "Change request", "open_question": "Open question"}


def load_cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def _hay(r: dict) -> str:
    return " " + norm_txt(" ".join(str(r.get(k) or "") for k in ("topic", "value", "text", "quote", "scope_note"))) + " "


def _has(hay: str, alts: list[str]) -> bool:
    for k in alts:
        n = norm_txt(k)
        if (n.isdigit() or len(n) <= 3) and f" {n} " in hay:
            return True
        if not (n.isdigit() or len(n) <= 3) and n in hay:
            return True
    return False


def matches(r: dict, spec: dict) -> bool:
    if spec.get("types") and r["type"] not in spec["types"]:
        return False
    hay = _hay(r)
    if any(not _has(hay, g) for g in spec.get("all", [])):
        return False
    if any(_has(hay, g) for g in spec.get("none", [])):
        return False
    if "outsideScope" in spec and bool(r.get("outside_scope")) != spec["outsideScope"]:
        return False
    return True


def brief(r: dict) -> str:
    due = r.get("due_date")
    return (f"{KIND.get(r['type'], r['type'])}: {r['text']}" + (f" (due {due})" if due else "")
            + (f" [replaces {','.join(r['replaces'])}]" if r.get("replaces") else "")
            + (" [outside scope]" if r.get("outside_scope") else ""))


def grade(case: dict, records: list[dict]) -> list[dict]:
    checks = []
    for f in case["find"]:
        cands = [r for r in records if matches(r, f)]
        ok, det = bool(cands), (f"Found: {brief(cands[0])}" if cands else "Not found")
        if ok and f.get("dueISO"):
            hit = [r for r in cands if str(r.get("due_date") or "") == f["dueISO"]]
            ok = bool(hit)
            det = f"Found: {brief(hit[0])}" if hit else f"Found, but due date was {cands[0].get('due_date') or 'not worked out'} instead of {f['dueISO']}"
        if ok and f.get("replaces"):
            hit = [r for r in cands if f["replaces"] in (r.get("replaces") or [])]
            ok = bool(hit)
            det = f"Found and linked: {brief(hit[0])}" if hit else f"Found, but not linked to the record it changes. {brief(cands[0])}"
        checks.append({"label": f["label"], "ok": ok, "detail": det, "kind": "find"})
    for nf in case.get("not_find", []):
        bad = [r for r in records if matches(r, nf) and not (nf.get("allowIfFlagged") and needs_check(r))]
        flagged = [r for r in records if nf.get("allowIfFlagged") and matches(r, nf) and needs_check(r)]
        det = (f"Wrongly recorded: {brief(bad[0])}" if bad else
               f"Recorded, but flagged for checking: {' '.join(reasons_for(flagged[0]))}" if flagged else "Correctly left out")
        checks.append({"label": nf["label"], "ok": not bad, "detail": det, "kind": "not_find"})
    for nr in case.get("no_replace", []):
        bad = [r for r in records if nr["id"] in (r.get("replaces") or [])]
        checks.append({"label": nr["label"], "ok": not bad, "detail": f"Wrongly linked: {brief(bad[0])}" if bad else "Correct",
                       "kind": "no_replace"})
    return checks
